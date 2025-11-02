# portfolio.py - Portfolio und State Management
import functools
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

# =================================================================================
# Position Data Model
# =================================================================================

@dataclass
class Position:
    """
    Position tracking with lifecycle states and PnL.

    States:
        NEW: Position created, awaiting first fill
        OPEN: Position has fills, actively held
        PARTIAL_EXIT: Position being reduced
        CLOSED: Position fully closed (qty = 0)

    Attributes:
        symbol: Trading pair
        state: Current lifecycle state
        qty: Net quantity (>0 long, <0 short, =0 closed)
        avg_price: Weighted average entry price
        realized_pnl: Realized profit/loss from closed portions
        fees_paid: Total fees paid (in quote currency)
        opened_ts: Timestamp when position opened
        closed_ts: Timestamp when position closed (None if open)
        meta: Additional metadata dict
    """
    symbol: str
    state: str  # NEW|OPEN|PARTIAL_EXIT|CLOSED
    qty: float  # net qty (>0 long, <0 short)
    avg_price: float
    realized_pnl: float
    fees_paid: float
    opened_ts: float
    closed_ts: Optional[float] = None
    meta: dict = field(default_factory=dict)


# =================================================================================
# Thread Safety Decorator
# =================================================================================
def synchronized(lock_attr='_lock'):
    """Decorator for thread-safe method execution"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            lock = getattr(self, lock_attr)
            with lock:
                return func(self, *args, **kwargs)
        return wrapper
    return decorator

def synchronized_budget(func):
    """Special decorator for budget operations with automatic cleanup"""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._budget_lock:
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                # Log budget operation failure
                logger.error(f"Budget operation failed in {func.__name__}: {e}",
                           extra={'event_type': 'BUDGET_OP_FAILED', 'function': func.__name__, 'error': str(e)})
                raise
    return wrapper

from config import (
    DROP_ANCHORS_FILE,
    DUST_FORCE_MARKET_IOC,
    DUST_MIN_COST_USD,
    DUST_TARGET_QUOTE,
    STATE_FILE_HELD,
    STATE_FILE_OPEN_BUYS,
    cash_reserve_usdt,
    max_trades,
    reset_portfolio_on_start,
    safe_min_budget,
    symbol_cooldown_minutes,
)
from core.logging.logger_setup import logger
from core.logging.loggingx import log_audit_event, log_event
from core.utils import SettlementManager, load_state, save_state_safe
from trading import full_portfolio_reset, refresh_budget_from_exchange_safe
from trading.helpers import _base_currency, _get_free


class DustLedger:
    """Kleinstmengen sammeln und bei Erreichen der Börsen-Minima verkaufen."""
    def __init__(self, ledger: dict | None = None):
        # ledger: {"XLM/USDT": {"amount": "0.1234", "updated": iso}}
        self.ledger = {}
        if ledger:
            for s, v in ledger.items():
                amt = v.get("amount", "0")
                self.ledger[s] = {
                    "amount": str(Decimal(str(amt))),
                    "updated": v.get("updated"),
                    "fail": int(v.get("fail") or 0),
                    "last_attempt": v.get("last_attempt")
                }

    def as_dict(self):
        return {
            s: {
                "amount": float(Decimal(v["amount"])),
                "updated": v.get("updated"),
                "fail": int(v.get("fail") or 0),
                "last_attempt": v.get("last_attempt")
            } for s, v in self.ledger.items()
        }

    def add(self, symbol: str, amount: float):
        if amount <= 0:
            return
        node = self.ledger.get(symbol, {"amount": "0", "updated": None})
        node["amount"] = str(Decimal(node["amount"]) + Decimal(str(amount)))
        node["updated"] = datetime.now(timezone.utc).isoformat()
        self.ledger[symbol] = node
        log_event("DUST_ACCUMULATED", symbol=symbol, amount=float(Decimal(node["amount"])))

    def try_sweep_all(self, exchange, get_symbol_limits_func, quantize_amount_func):
        """Versucht alle Symbole zu verkaufen, sofern min_cost/min_qty erfüllt.
        Returns: Anzahl erfolgreicher Sweeps
        """
        import time
        from datetime import datetime, timezone
        BACKOFF_S = (0, 300, 900, 3600, 7200)  # 0s, 5min, 15min, 60min, 120min
        now = time.time()

        done = 0
        for symbol, node in list(self.ledger.items()):
            try:
                # Backoff-Check
                fail = int(node.get("fail") or 0)
                last_attempt_ts = node.get("last_attempt")
                if last_attempt_ts:
                    try:
                        last_attempt = float(last_attempt_ts)
                    except Exception:
                        last_attempt = 0.0
                else:
                    last_attempt = 0.0

                wait = BACKOFF_S[min(fail, len(BACKOFF_S)-1)]
                if now - last_attempt < wait:
                    continue  # noch im Backoff

                base, quote = symbol.split("/")
                market = f"{base}/{DUST_TARGET_QUOTE}" if quote != DUST_TARGET_QUOTE else symbol

                # aktueller Preis
                tkr = exchange.fetch_ticker(market)
                last = tkr.get("last") or tkr.get("close") or 0.0
                if not last:
                    continue

                amt = float(Decimal(node["amount"]))
                # Mindestgegenwert prüfen (eigener Schwellenwert vs. Börse)
                limits = get_symbol_limits_func(exchange, market)
                min_cost = max(DUST_MIN_COST_USD, float(limits.get("min_cost") or 0.0))
                est_cost = amt * float(last)
                if est_cost < min_cost:
                    continue

                # quantisieren
                amount_step = limits.get("amount_step", 8)  # Präzision in Dezimalstellen
                amt_q = quantize_amount_func(market, amt, amount_step)

                # letzter Check: konsistent mit utils.get_symbol_limits()
                min_amount = float(limits.get("min_amount") or 0.0)
                min_cost   = float(limits.get("min_cost")   or 0.0)

                if (min_amount and amt_q < min_amount) or (min_cost and (amt_q * float(last) < min_cost)):
                    continue

                params = {"timeInForce": "IOC"} if DUST_FORCE_MARKET_IOC else {}
                order = exchange.create_order(market, "market", "sell", amt_q, None, params)

                # Bei Erfolg: fail-counter zurücksetzen
                node["fail"] = 0
                node["last_attempt"] = str(now)

                # Ledger reduzieren (Rest bleibt, falls Rundung)
                rest = max(0.0, amt - amt_q)
                node["amount"] = str(Decimal(str(rest)))
                node["updated"] = datetime.now(timezone.utc).isoformat()
                self.ledger[symbol] = node
                log_event("DUST_SWEEP_EXECUTED", symbol=market, sold_amount=amt_q, price=float(last), cost_usd=amt_q*float(last), order=order)
                done += 1
            except Exception as e:
                # Bei Fehler: fail-counter erhöhen
                node["fail"] = int(node.get("fail") or 0) + 1
                node["last_attempt"] = str(now)
                self.ledger[symbol] = node
                log_event("DUST_SWEEP_ERROR", symbol=symbol, error=str(e), level="ERROR", fail_count=node["fail"])
                continue
        return done


class PortfolioManager:
    """Verwaltet Portfolio-State, Budget und offene Orders"""

    def is_holding(self, symbol: str, min_amount: float = 1e-12) -> bool:
        """True, wenn das Symbol aktuell im Portfolio gehalten wird (Menge > 0)."""
        data = (self.held_assets or {}).get(symbol)
        if not data:
            return False
        try:
            return float(data.get("amount") or 0.0) > min_amount
        except Exception:
            return False

    def has_open_buy(self, symbol: str) -> bool:
        """True, wenn für das Symbol eine offene Buy-Order getrackt wird."""
        return symbol in (self.open_buy_orders or {})

    def __init__(self, exchange, settlement_manager: SettlementManager, dust_sweeper=None):
        self.exchange = exchange
        self.settlement_manager = settlement_manager
        self.dust_sweeper = dust_sweeper

        # Thread Safety Locks
        self._lock = threading.RLock()  # Main portfolio lock
        self._budget_lock = threading.RLock()  # Dedicated budget operations lock
        self._state_lock = threading.RLock()  # State persistence lock

        # State
        self.held_assets: Dict[str, Dict] = {}
        self.open_buy_orders: Dict[str, Dict] = {}
        self.my_budget: float = 0.0

        # Drop Anchors
        self.drop_anchors = {}
        try:
            if os.path.exists(DROP_ANCHORS_FILE):
                with open(DROP_ANCHORS_FILE, "r", encoding="utf-8") as fh:
                    self.drop_anchors = json.load(fh) or {}
        except Exception:
            self.drop_anchors = {}

        # Cooldown tracking
        self.last_buy_time: Dict[str, float] = {}

        # Reserved budget tracking for partial fills
        self.reserved_quote: Dict[str, float] = {}

        # Budget reservation for atomic operations
        self.reserved_budget: float = 0.0
        self.reserved_base: Dict[str, float] = {}
        self.active_reservations: Dict[str, Dict] = {}  # symbol -> reservation metadata

        # CRITICAL FIX (C-PORT-01): Add missing verified_budget attribute
        # Used by settlement manager for budget health monitoring
        self.verified_budget: float = 0.0

        # Position Lifecycle & PnL Management (NEW - Reconciliation System)
        self.positions: Dict[str, Position] = {}  # symbol -> Position
        self.last_prices: Dict[str, float] = {}  # symbol -> last_price for marking
        self.fee_rate: float = getattr(__import__('config'), 'TAKER_FEE_RATE', 0.001)
        self.events: List[dict] = []  # Position events for audit trail

        # Initialize state (includes dust ledger)
        self.load_state()

        # Initialize budget
        self.initialize_budget()

    def load_state(self):
        """Lädt gespeicherten State von Disk (mit Spezialkeys)"""
        state = load_state(STATE_FILE_HELD) or {}
        dust_ledger = state.pop("__dust_ledger__", None)
        lbt = state.pop("__last_buy_time__", {}) or {}
        self.held_assets = state
        # Cooldown wiederherstellen
        try:
            self.last_buy_time = {s: float(ts) for s, ts in lbt.items()}
        except Exception:
            self.last_buy_time = {}
        self.open_buy_orders = load_state(STATE_FILE_OPEN_BUYS)
        # Dust-Ledger Instanz aktualisieren (falls du self.dust verwendest)
        try:
            self.dust = DustLedger(dust_ledger)
        except Exception:
            self.dust = DustLedger()

    @synchronized('_state_lock')
    def save_state(self):
        """Speichert aktuellen State auf Disk (inkl. Spezialkeys)"""
        held_with_meta = dict(self.held_assets or {})
        # Dust-Ledger anhängen
        try:
            held_with_meta["__dust_ledger__"] = self.dust.as_dict()
        except Exception:
            pass
        # Cooldown persistieren
        try:
            held_with_meta["__last_buy_time__"] = {s: int(ts) for s, ts in (self.last_buy_time or {}).items()}
        except Exception:
            pass
        save_state_safe(held_with_meta, STATE_FILE_HELD)
        save_state_safe(self.open_buy_orders, STATE_FILE_OPEN_BUYS)
        # NEU: Drop-Anker separat persistieren
        try:
            save_state_safe(self.drop_anchors, DROP_ANCHORS_FILE)
        except Exception:
            pass

    def initialize_budget(self):
        """Initialisiert Budget von Exchange"""
        if self.exchange:
            self.my_budget = _get_free(self.exchange, "USDT")
            logger.info(f"Verfügbares Startbudget: {self.my_budget:.2f} USDT",
                       extra={'event_type': 'BUDGET_INITIALIZED', 'initial_budget_usdt': self.my_budget})
        else:
            self.my_budget = 0
            logger.info("Bot im Observe-Modus gestartet (keine API-Keys)",
                       extra={'event_type': 'OBSERVE_MODE'})

        # Settlement-Manager initialisieren
        self.settlement_manager.update_verified_budget(self.my_budget)

    def sanity_check_and_reconcile(self, preise: Dict[str, float]):
        """Führt Sanity-Check für geladene Assets durch und reconciled mit Exchange"""
        if not self.held_assets or not self.exchange:
            if self.held_assets and not self.exchange:
                logger.info("Held assets vorhanden, aber keine Exchange-Verbindung (Observe-Mode) – Sanity-Check übersprungen.",
                           extra={'event_type': 'STATE_SANITY_CHECK_SKIPPED'})
            return

        logger.info("Führe Sanity-Check für geladene Assets durch: Vergleiche State mit realem Kontostand...",
                   extra={'event_type': 'STATE_SANITY_CHECK_START'})

        try:
            actual_balances = self.exchange.fetch_balance()
            total_balances = actual_balances.get('total', {})

            assets_to_prune = []
            reconciliation_stats = {
                'checked': 0, 'valid': 0, 'pruned': 0,
                'actual_gt_state': 0, 'state_gt_actual': 0
            }

            for symbol, asset_data in list(self.held_assets.items()):
                reconciliation_stats['checked'] += 1
                base_currency = _base_currency(symbol)
                actual_amount = total_balances.get(base_currency, 0)
                state_amount = asset_data.get('amount', 0)

                # Toleranz für Floating-Point-Vergleiche
                tolerance = 0.0001
                abs(actual_amount - state_amount)

                if actual_amount > state_amount + tolerance:
                    reconciliation_stats['actual_gt_state'] += 1
                    logger.warning(f"Diskrepanz für {symbol}: Exchange hat {actual_amount}, State hat {state_amount}. Korrigiere nach oben.",
                                 extra={'event_type': 'STATE_DISCREPANCY_UP', 'symbol': symbol,
                                       'actual': actual_amount, 'state': state_amount})
                    asset_data['amount'] = actual_amount

                elif state_amount > actual_amount + tolerance:
                    reconciliation_stats['state_gt_actual'] += 1
                    if actual_amount < 0.0001:
                        logger.warning(f"Asset {symbol} im State, aber nicht auf Exchange. Entferne aus State.",
                                     extra={'event_type': 'STATE_GHOST_ASSET', 'symbol': symbol})
                        assets_to_prune.append(symbol)
                    else:
                        logger.warning(f"Diskrepanz für {symbol}: State hat {state_amount}, aber Exchange nur {actual_amount}. Korrigiere nach unten.",
                                     extra={'event_type': 'STATE_DISCREPANCY_DOWN', 'symbol': symbol,
                                           'actual': actual_amount, 'state': state_amount})
                        asset_data['amount'] = actual_amount
                else:
                    reconciliation_stats['valid'] += 1

                # Prüfe auf Dust
                if symbol not in assets_to_prune and actual_amount > 0:
                    current_price = preise.get(symbol, 0)
                    if current_price > 0:
                        value = actual_amount * current_price
                        if value < 1.0:
                            logger.info(f"Dust-Asset erkannt: {symbol} - {actual_amount} im Wert von {value:.4f} USDT",
                                      extra={'event_type': 'DUST_ASSET_DETECTED', 'symbol': symbol,
                                            'amount': actual_amount, 'value': value})
                            # Füge zu Dust-Sweeper hinzu
                            if self.dust_sweeper:
                                self.dust_sweeper.add_dust(symbol, actual_amount, current_price)
                            assets_to_prune.append(symbol)

            # Entferne ungültige Assets
            reconciliation_stats['pruned'] = len(assets_to_prune)
            for symbol in assets_to_prune:
                if symbol in self.held_assets:
                    del self.held_assets[symbol]

            logger.info(f"Sanity-Check abgeschlossen. Stats: {reconciliation_stats}",
                       extra={'event_type': 'STATE_SANITY_CHECK_COMPLETE', **reconciliation_stats})

            if assets_to_prune:
                self.save_state()

        except Exception as e:
            logger.error("Kritischer Fehler beim Sanity-Check des Kontostands.",
                        extra={'event_type': 'STATE_SANITY_CHECK_FAILED', 'error': str(e)})

    def perform_startup_reset(self, preise: Dict[str, float]) -> bool:
        """Führt Portfolio-Reset beim Start durch wenn konfiguriert"""
        if not reset_portfolio_on_start or not self.exchange:
            return False

        logger.info("PORTFOLIO RESET: Verkaufe alle Nicht-USDT-Assets...",
                   extra={'event_type': 'PORTFOLIO_RESET_START'})

        # Audit trail - before reset
        log_audit_event("PORTFOLIO_RESET_INITIATED", {
            "budget_before": self.my_budget,
            "held_assets": list(self.held_assets.keys()),
            "open_orders": len(self.open_buy_orders)
        })

        try:
            # Verwende full_portfolio_reset aus trading.py
            full_portfolio_reset(
                self.exchange,
                self.settlement_manager
            )

            # Immer State synchronisieren nach Reset-Versuch
            refresh_func = lambda: refresh_budget_from_exchange_safe(self.exchange, self.my_budget, timeout=5.0)

            # Warte auf alle Settlements
            if self.settlement_manager.get_total_pending() > 0:
                if self.settlement_manager.wait_for_all_settlements(refresh_func, timeout=300):
                    self.my_budget = refresh_func()
                    self.settlement_manager.update_verified_budget(self.my_budget)
                    logger.info(f"Portfolio-Reset abgeschlossen. Neues Budget: {self.my_budget:.2f} USDT",
                               extra={'event_type': 'PORTFOLIO_RESET_COMPLETE', 'new_budget': self.my_budget})
                    # Audit trail - after reset
                    log_audit_event("PORTFOLIO_RESET_COMPLETED", {
                        "budget_after": self.my_budget,
                        "success": True
                    })
                else:
                    logger.warning("Settlement-Timeout nach Portfolio-Reset",
                                 extra={'event_type': 'PORTFOLIO_RESET_SETTLEMENT_TIMEOUT'})
            else:
                # Keine pending settlements, aber trotzdem Budget aktualisieren
                self.my_budget = refresh_func()
                self.settlement_manager.update_verified_budget(self.my_budget)
                logger.info(f"Portfolio-Reset abgeschlossen. Budget: {self.my_budget:.2f} USDT",
                           extra={'event_type': 'PORTFOLIO_RESET_COMPLETE', 'new_budget': self.my_budget})
                # Audit trail - after reset
                log_audit_event("PORTFOLIO_RESET_COMPLETED", {
                    "budget_after": self.my_budget,
                    "success": True
                })

            # State clearen - IMMER nach Reset
            self.held_assets.clear()
            self.open_buy_orders.clear()
            self.save_state()

            # Finale Sanity-Check zur Sicherheit
            if preise:
                self.sanity_check_and_reconcile(preise)

            return True

        except Exception as e:
            logger.error(f"Fehler beim Portfolio-Reset: {e}",
                        extra={'event_type': 'PORTFOLIO_RESET_ERROR', 'error': str(e)})

    @synchronized
    def get_all_positions(self) -> Dict[str, Position]:
        """
        Get all positions (thread-safe).

        FIX HIGH-2/HIGH-3: Thread-safe getter for position iteration.

        Returns:
            Dict copy of all positions (symbol -> Position)
        """
        return self.positions.copy()

    @synchronized
    def get_position(self, symbol: str) -> Optional[Position]:
        """
        Get single position (thread-safe).

        Args:
            symbol: Trading pair

        Returns:
            Position object or None if not found
        """
        return self.positions.get(symbol)

    def check_startup_budget(self) -> bool:
        """Prüft ob genug Budget vorhanden ist"""
        if self.my_budget < safe_min_budget:
            logger.warning(f"Budget {self.my_budget:.2f} USDT unter Minimum {safe_min_budget} USDT",
                         extra={'event_type': 'INSUFFICIENT_STARTUP_BUDGET',
                               'budget': self.my_budget, 'required': safe_min_budget})
            return False
        return True

    def refresh_budget(self) -> float:
        """Aktualisiert Budget von Exchange"""
        if not self.exchange:
            return self.my_budget

        # Use timeout-protected version to prevent main thread blocking
        actual_budget = refresh_budget_from_exchange_safe(self.exchange, self.my_budget, timeout=5.0)

        # Only log if there's a meaningful change
        delta = abs(actual_budget - (self.my_budget or 0.0))
        rel = delta / max(self.my_budget or 1.0, 1.0)

        if delta >= 0.5 or rel >= 0.005:  # Log if change is >= 0.5 USDT or >= 0.5%
            logger.info(f"Budget aktualisiert: {self.my_budget:.2f} -> {actual_budget:.2f} USDT",
                        extra={'event_type': 'BUDGET_REFRESHED',
                              'old': self.my_budget, 'new': actual_budget})

        self.my_budget = actual_budget
        self.settlement_manager.update_verified_budget(actual_budget)
        return actual_budget


    def get_available_slots(self) -> int:
        """Berechnet verfügbare Trading-Slots"""
        return max(0, max_trades - (len(self.held_assets) + len(self.open_buy_orders)))

    def get_per_trade_cap(self) -> float:
        """Berechnet maximales Budget pro Trade"""
        slots = max(1, self.get_available_slots())
        return max(0.0, (self.my_budget - cash_reserve_usdt) / slots)

    def is_symbol_on_cooldown(self, symbol: str) -> bool:
        """Prüft ob Symbol auf Cooldown ist"""
        if symbol not in self.last_buy_time:
            return False

        minutes_since_buy = (time.time() - self.last_buy_time[symbol]) / 60
        if minutes_since_buy < symbol_cooldown_minutes:
            logger.debug(f"Cooldown aktiv für {symbol}: {minutes_since_buy:.1f} < {symbol_cooldown_minutes} min",
                        extra={'event_type': 'COOLDOWN_ACTIVE', 'symbol': symbol})
            return True

        # Phase 3: Cooldown expired - log cooldown_release event and clear tracking
        try:
            from core.logger_factory import HEALTH_LOG, log_event
            from core.trace_context import Trace, get_context_var

            decision_id = get_context_var('decision_id')

            with Trace(decision_id=decision_id) if decision_id else Trace():
                log_event(
                    HEALTH_LOG(),
                    "cooldown_release",
                    symbol=symbol,
                    cooldown_duration_minutes=symbol_cooldown_minutes,
                    reason="cooldown_expired"
                )
        except Exception as e:
            # Don't fail cooldown check if logging fails
            logger.debug(f"Failed to log cooldown_release for {symbol}: {e}")

        # Remove from tracking to mark cooldown as released
        del self.last_buy_time[symbol]
        return False

    def set_cooldown(self, symbol: str):
        """Setzt Cooldown-Timer für Symbol"""
        cooldown_until = time.time() + (symbol_cooldown_minutes * 60)
        self.last_buy_time[symbol] = time.time()

        # Phase 3: Log cooldown_set event
        try:
            from datetime import datetime, timezone

            from core.logger_factory import HEALTH_LOG, log_event
            from core.trace_context import Trace, get_context_var

            decision_id = get_context_var('decision_id')

            with Trace(decision_id=decision_id) if decision_id else Trace():
                log_event(
                    HEALTH_LOG(),
                    "cooldown_set",
                    symbol=symbol,
                    cooldown_duration_minutes=symbol_cooldown_minutes,
                    cooldown_until=datetime.fromtimestamp(cooldown_until, tz=timezone.utc).isoformat(),
                    reason="post_trade"
                )
        except Exception as e:
            # Don't fail cooldown if logging fails
            logger.debug(f"Failed to log cooldown_set for {symbol}: {e}")

    def add_buy_order(self, symbol: str, order_data: Dict):
        """Fügt neue Buy-Order hinzu"""
        self.open_buy_orders[symbol] = order_data
        self.save_state()

    def remove_buy_order(self, symbol: str) -> Optional[Dict]:
        """Entfernt Buy-Order und gibt sie zurück"""
        if symbol in self.open_buy_orders:
            order = self.open_buy_orders.pop(symbol)
            self.save_state()
            return order
        return None

    def add_held_asset(self, symbol: str, asset_data: Dict):
        """
        Fügt gehaltenes Asset hinzu mit WAC und Fee-Tracking.

        Phase 5: Tracks first_fill_ts for accurate TTL calculation.
        """
        data = dict(asset_data)
        # Stelle sicher dass beide Felder existieren
        if 'buy_price' not in data and 'buying_price' in data:
            data['buy_price'] = data['buying_price']
        if 'buying_price' not in data and 'buy_price' in data:
            data['buying_price'] = data['buy_price']

        # WAC (Weighted Average Cost) für mehrfache Käufe
        existing = self.held_assets.get(symbol, {})
        old_amt = float(existing.get("amount") or 0.0)
        old_price = float(existing.get("entry_price") or existing.get("buy_price") or 0.0)
        new_amt = float(data.get("amount") or 0.0)
        new_price = float(data.get("entry_price") or data.get("buy_price") or 0.0)

        # Phase 5: Preserve first_fill_ts for TTL calculation
        # Only set if this is the first fill (no existing position)
        if old_amt == 0:
            # First fill - set timestamp
            data["first_fill_ts"] = data.get("first_fill_ts", time.time())
        else:
            # Subsequent fill - preserve original first_fill_ts
            data["first_fill_ts"] = existing.get("first_fill_ts", time.time())

        total_amt = old_amt + new_amt
        if total_amt > 0:
            wac_price = ((old_amt * old_price) + (new_amt * new_price)) / total_amt
            data["entry_price"] = wac_price
            data["buy_price"] = wac_price  # Kompatibilität
            data["amount"] = total_amt

            # WAC für Buy-Fees per Unit (Quote/Unit)
            old_fee_per_unit = float(existing.get("buy_fee_quote_per_unit") or 0.0)
            new_fee_per_unit = float(data.get("buy_fee_quote_per_unit") or 0.0)
            wac_fee = ((old_amt * old_fee_per_unit) + (new_amt * new_fee_per_unit)) / total_amt
            data["buy_fee_quote_per_unit"] = wac_fee

        self.held_assets[symbol] = data
        self.save_state()

    def on_partial_fill(self, symbol: str, client_order_id: str,
                        filled_quote: float, orig_quote: float):
        """Release budget on partial fill (thread-safe with _budget_lock)

        CRITICAL FIX (C-PORT-02): save_state() called OUTSIDE budget lock to prevent deadlock.
        Lock hierarchy: Never hold _budget_lock when acquiring _state_lock.

        Args:
            symbol: Trading pair
            client_order_id: The clientOrderId of the order
            filled_quote: How much was actually filled in quote currency
            orig_quote: Original quote amount that was reserved
        """
        if orig_quote <= 0:
            return

        # Perform budget operations under lock, then release before save_state()
        need_save = False
        with self._budget_lock:
            # Calculate how much to release
            fill_ratio = filled_quote / orig_quote
            reserved = self.reserved_quote.get(symbol, 0.0)
            release = reserved * (1 - fill_ratio)  # Release unfilled portion

            if release > 0:
                self.reserved_quote[symbol] = max(0.0, reserved - release)
                self.my_budget += release  # Return to available budget
                need_save = True

                log_event("BUDGET_RELEASED_PARTIAL", symbol=symbol, context={
                    "clientOrderId": client_order_id,
                    "released_usdt": float(release),
                    "filled_quote": float(filled_quote),
                    "orig_quote": float(orig_quote),
                    "fill_ratio": float(fill_ratio)
                })

        # CRITICAL: Persist state OUTSIDE budget lock to prevent lock hierarchy violation
        if need_save:
            self.save_state()

    @synchronized_budget
    def reserve_budget(self, quote_amount: float, symbol: str | None = None, order_info: Dict = None):
        """Reserviert Budget atomisch vor Order-Platzierung

        Args:
            quote_amount: Zu reservierender Betrag in USDT
            symbol: Optional - Symbol für das Budget reserviert wird
            order_info: Optional - Zusätzliche Order-Informationen für Tracking

        Raises:
            ValueError: Wenn nicht genug Budget verfügbar
        """
        available = self.my_budget - self.reserved_budget
        if quote_amount > available:
            raise ValueError(f"Insufficient budget: need {quote_amount:.2f} but only {available:.2f} available (budget: {self.my_budget:.2f}, reserved: {self.reserved_budget:.2f})")

        self.reserved_budget += quote_amount
        if symbol:
            self.reserved_quote[symbol] = self.reserved_quote.get(symbol, 0.0) + quote_amount
            # Track active reservations with metadata
            reservation = self.active_reservations.get(symbol, {})
            if reservation.get('side') not in (None, 'buy'):
                reservation = {}
            reservation.update({
                'side': 'buy',
                'quote': reservation.get('quote', 0.0) + quote_amount,
                'amount': reservation.get('quote', 0.0) + quote_amount,  # backwards compat
                'timestamp': time.time(),
                'order_info': order_info or {}
            })
            self.active_reservations[symbol] = reservation

        log_event("BUDGET_RESERVED", context={
            "reserved": float(quote_amount),
            "total_reserved": float(self.reserved_budget),
            "available": float(self.my_budget - self.reserved_budget),
            "symbol": symbol,
            "order_info": order_info
        })

    @synchronized_budget
    def release_budget(
        self,
        quote_amount: float,
        symbol: str | None = None,
        reason: str = "unknown",
        intent_id: str | None = None
    ):
        """Gibt reserviertes Budget wieder frei

        Args:
            quote_amount: Freizugebender Betrag in USDT
            symbol: Optional - Symbol für das Budget freigegeben wird
            reason: Grund für die Freigabe (für Audit Trail)
        """
        self.reserved_budget = max(0.0, self.reserved_budget - quote_amount)
        if symbol and symbol in self.reserved_quote:
            self.reserved_quote[symbol] = max(0.0, self.reserved_quote[symbol] - quote_amount)
            if self.reserved_quote[symbol] == 0.0:
                self.reserved_quote.pop(symbol, None)
            # Remove from active reservations
            reservation = self.active_reservations.get(symbol)
            if reservation and reservation.get('side') == 'buy':
                remaining = max(0.0, reservation.get('quote', 0.0) - quote_amount)
                if remaining <= 0:
                    self.active_reservations.pop(symbol, None)
                else:
                    reservation['quote'] = remaining
                    reservation['amount'] = remaining
                    reservation['timestamp'] = time.time()
                    self.active_reservations[symbol] = reservation

        context = {
            "released": float(quote_amount),
            "total_reserved": float(self.reserved_budget),
            "available": float(self.my_budget - self.reserved_budget),
            "symbol": symbol,
            "reason": reason
        }
        if intent_id:
            context["intent_id"] = intent_id

        log_event("BUDGET_RELEASED", context=context)

    @synchronized_budget
    def set_budget(self, amount: float, reason: str = "manual_set"):
        """
        Thread-safe budget setter.

        FIX HIGH-4: Explicit setter for encapsulation and thread safety.

        Args:
            amount: New budget amount (USDT)
            reason: Reason for change (for logging)
        """
        old_budget = self.my_budget
        self.my_budget = float(amount)

        logger.info(
            f"Budget updated: {old_budget:.2f} → {amount:.2f} (reason: {reason})",
            extra={
                'event_type': 'BUDGET_UPDATED',
                'old_budget': old_budget,
                'new_budget': amount,
                'reason': reason
            }
        )

        self._persist_state()

    @synchronized_budget
    def adjust_budget(self, delta: float, reason: str = "adjustment"):
        """
        Thread-safe budget adjustment (add or subtract).

        FIX HIGH-4: Explicit method for budget adjustments.

        Args:
            delta: Amount to add (positive) or subtract (negative)
            reason: Reason for adjustment (for logging)
        """
        old_budget = self.my_budget
        self.my_budget += delta

        logger.info(
            f"Budget adjusted: {old_budget:.2f} {delta:+.2f} = {self.my_budget:.2f} (reason: {reason})",
            extra={
                'event_type': 'BUDGET_ADJUSTED',
                'old_budget': old_budget,
                'delta': delta,
                'new_budget': self.my_budget,
                'reason': reason
            }
        )

        self._persist_state()

    @synchronized_budget
    def commit_budget(
        self,
        quote_amount: float,
        symbol: str | None = None,
        order_id: str = None,
        intent_id: str | None = None
    ):
        """Committet reserviertes Budget nach erfolgreicher Order

        Args:
            quote_amount: Zu committender Betrag in USDT
            symbol: Optional - Symbol für das Budget committed wird
            order_id: Optional - Order ID für Audit Trail
        """
        self.reserved_budget = max(0.0, self.reserved_budget - quote_amount)
        self.my_budget = max(0.0, self.my_budget - quote_amount)
        if symbol and symbol in self.reserved_quote:
            self.reserved_quote[symbol] = max(0.0, self.reserved_quote[symbol] - quote_amount)
            if self.reserved_quote[symbol] == 0.0:
                self.reserved_quote.pop(symbol, None)
            # Remove from active reservations
            reservation = self.active_reservations.get(symbol)
            if reservation and reservation.get('side') == 'buy':
                remaining = max(0.0, reservation.get('quote', 0.0) - quote_amount)
                if remaining <= 0:
                    self.active_reservations.pop(symbol, None)
                else:
                    reservation['quote'] = remaining
                    reservation['amount'] = remaining
                    reservation['timestamp'] = time.time()
                    self.active_reservations[symbol] = reservation

        commit_context = {
            "committed": float(quote_amount),
            "remaining_budget": float(self.my_budget),
            "reserved": float(self.reserved_budget),
            "symbol": symbol,
            "order_id": order_id
        }
        if intent_id:
            commit_context["intent_id"] = intent_id

        log_event("BUDGET_COMMITTED", context=commit_context)

    @synchronized('_budget_lock')
    def get_free_usdt(self) -> float:
        """Gibt wirklich verfügbares Budget zurück (abzgl. Reservierungen)"""
        return max(0, self.my_budget - self.reserved_budget)

    def get_balance(self, asset: str) -> float:
        """Return available balance for the requested asset."""
        if not asset:
            return 0.0
        asset = asset.upper()
        if asset == 'USDT':
            return self.get_free_usdt()
        with self._lock:
            total = 0.0
            for symbol, data in self.held_assets.items():
                if '/' not in symbol:
                    continue
                base, _ = symbol.split('/', 1)
                if base.upper() != asset:
                    continue
                try:
                    total += float(data.get('amount', 0.0))
                except (TypeError, ValueError):
                    continue
            # Subtract reserved base inventory for open sell reservations
            for symbol, reserved in self.reserved_base.items():
                try:
                    base_symbol, _ = symbol.split('/', 1)
                except ValueError:
                    continue
                if base_symbol.upper() == asset:
                    total = max(0.0, total - reserved)
            return total

    def balance(self, asset: str) -> float:
        """Alias for get_balance() for compatibility."""
        return self.get_balance(asset)

    @synchronized_budget
    def cleanup_stale_reservations(self, max_age_seconds: float = 3600):
        """Bereinigt veraltete Budget-Reservierungen (Budget Leak Prevention)"""
        current_time = time.time()
        stale_symbols = []

        for symbol, reservation in self.active_reservations.items():
            if current_time - reservation['timestamp'] > max_age_seconds:
                stale_symbols.append(symbol)

        total_quote_released = 0.0
        total_base_released = 0.0
        for symbol in stale_symbols:
            reservation = self.active_reservations.get(symbol)
            if not reservation:
                continue

            side = reservation.get('side') or reservation.get('order_info', {}).get('side')
            age_seconds = current_time - reservation.get('timestamp', current_time)

            if side == 'sell':
                base_amount = float(reservation.get('base') or reservation.get('amount') or 0.0)
                if base_amount > 0:
                    self.release(symbol, "sell", base_amount, price=0.0)
                    total_base_released += base_amount
                    logger.warning(
                        f"Cleaned up stale sell reservation: {symbol} - {base_amount:.8f} base units "
                        f"(age: {age_seconds:.1f}s)",
                        extra={
                            'event_type': 'STALE_BUDGET_CLEANUP',
                            'symbol': symbol,
                            'amount': base_amount,
                            'side': 'sell'
                        }
                    )
            else:
                quote_amount = float(reservation.get('quote') or reservation.get('amount') or 0.0)
                if quote_amount > 0:
                    self.release_budget(quote_amount, symbol, reason="stale_cleanup")
                    total_quote_released += quote_amount
                    logger.warning(
                        f"Cleaned up stale buy reservation: {symbol} - {quote_amount:.2f} USDT "
                        f"(age: {age_seconds:.1f}s)",
                        extra={
                            'event_type': 'STALE_BUDGET_CLEANUP',
                            'symbol': symbol,
                            'amount': quote_amount,
                            'side': 'buy'
                        }
                    )

            # Remove reservation entry regardless of side
            self.active_reservations.pop(symbol, None)

        if total_quote_released > 0 or total_base_released > 0:
            logger.info(
                "Total stale reservations released: "
                f"{total_quote_released:.2f} USDT, {total_base_released:.8f} base units "
                f"from {len(stale_symbols)} symbols",
                extra={
                    'event_type': 'STALE_BUDGET_CLEANUP_SUMMARY',
                    'total_released_quote': total_quote_released,
                    'total_released_base': total_base_released,
                    'count': len(stale_symbols)
                }
            )

            # P5: Log budget health metrics after cleanup
            try:
                health_metrics = self.get_budget_health_metrics()
                logger.info(
                    f"Budget health after cleanup: drift={health_metrics['drift_pct']:.2f}%, "
                    f"active_reservations={health_metrics['active_reservations_count']}",
                    extra={
                        'event_type': 'BUDGET_HEALTH_CHECK',
                        **health_metrics
                    }
                )
            except Exception as health_error:
                logger.debug(f"Budget health check failed: {health_error}")

        return total_quote_released

    def get_budget_health_metrics(self) -> Dict[str, Any]:
        """
        P5: Get budget health metrics for leak detection and monitoring.

        Returns:
            Dict with budget health indicators
        """
        with self._budget_lock:
            total_reserved_quote = sum(self.reserved_quote.values())
            total_reserved_base = sum(self.reserved_base.values())

            # Calculate expected vs actual available
            expected_available = self.my_budget - self.reserved_budget
            actual_available = self.get_free_usdt()

            # Drift detection (budget mismatch)
            budget_drift = abs(expected_available - actual_available)
            drift_pct = (budget_drift / max(self.my_budget, 1.0)) * 100

            # Analyze reservation ages
            old_reservations = []
            now = time.time()
            for symbol, reservation in self.active_reservations.items():
                age_s = now - reservation.get("timestamp", now)
                if age_s > 300:  # >5 minutes
                    old_reservations.append({
                        "symbol": symbol,
                        "side": reservation.get("side"),
                        "amount": reservation.get("amount") or reservation.get("quote") or reservation.get("base"),
                        "age_s": age_s
                    })

            # Determine health status
            if drift_pct > 1.0:
                health = "WARNING"  # >1% drift
            elif len(old_reservations) > 0:
                health = "WARNING"  # Old reservations exist
            else:
                health = "HEALTHY"

            return {
                "my_budget": self.my_budget,
                "reserved_budget": self.reserved_budget,
                "verified_budget": self.verified_budget,
                "reserved_quote_total": total_reserved_quote,
                "reserved_base_total": total_reserved_base,
                "expected_available": expected_available,
                "actual_available": actual_available,
                "budget_drift": budget_drift,
                "drift_pct": drift_pct,
                "active_reservations_count": len(self.active_reservations),
                "old_reservations": old_reservations,
                "health": health
            }

    @synchronized_budget
    def reconcile_budget_from_exchange(self):
        """Budget-Reconciliation mit Exchange (Drift-Erkennung)"""
        if not self.exchange:
            return

        try:
            actual_budget = _get_free(self.exchange, "USDT")
            expected_budget = self.my_budget
            drift = abs(actual_budget - expected_budget)

            # Phase 3: Log reconciliation_result
            try:
                from core.event_schemas import ReconciliationResult
                from core.logger_factory import AUDIT_LOG, log_event
                from core.trace_context import Trace

                cash_diff = actual_budget - expected_budget
                drift_detected = drift > 1.0

                reconciliation = ReconciliationResult(
                    cash_diff=cash_diff,
                    drift_detected=drift_detected
                )

                with Trace():
                    log_event(AUDIT_LOG(), "reconciliation_result", **reconciliation.model_dump())

            except Exception as e:
                logger.debug(f"Failed to log reconciliation_result: {e}")

            if drift > 1.0:  # Mehr als 1 USDT Abweichung
                logger.warning(f"Budget drift detected: expected {expected_budget:.2f}, actual {actual_budget:.2f} (drift: {drift:.2f})",
                             extra={'event_type': 'BUDGET_DRIFT_DETECTED', 'expected': expected_budget, 'actual': actual_budget, 'drift': drift})

                # Auto-correction für größere Abweichungen
                if drift > 10.0:
                    logger.info(f"Auto-correcting budget: {expected_budget:.2f} -> {actual_budget:.2f}",
                               extra={'event_type': 'BUDGET_AUTO_CORRECTED', 'old': expected_budget, 'new': actual_budget})
                    self.my_budget = actual_budget
                    self.settlement_manager.update_verified_budget(actual_budget)

        except Exception as e:
            logger.error(f"Budget reconciliation failed: {e}", extra={'event_type': 'BUDGET_RECONCILE_ERROR', 'error': str(e)})

    @synchronized()
    def remove_held_asset(self, symbol: str) -> Optional[Dict]:
        """Entfernt gehaltenes Asset und gibt es zurück"""
        if symbol in self.held_assets:
            asset = self.held_assets.pop(symbol)
            self.save_state()
            return asset
        return None

    # Drop Anchor Helper-Methoden
    @synchronized()
    def set_drop_anchor(self, symbol: str, price: float, ts_iso: str):
        """Setzt Drop-Anchor für Symbol"""
        # Phase 1: Log anchor updates
        old_anchor = self.drop_anchors.get(symbol, {}).get("price")
        new_anchor = float(price)

        # Only log if anchor actually changed (not initial set)
        if old_anchor is not None and abs(new_anchor - old_anchor) > 1e-8:
            try:
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace, get_context_var

                # Use current decision_id if available in context
                decision_id = get_context_var('decision_id')

                with Trace(decision_id=decision_id) if decision_id else Trace():
                    log_event(
                        DECISION_LOG(),
                        "anchor_update",
                        symbol=symbol,
                        anchor_old=old_anchor,
                        anchor_new=new_anchor,
                        source="drop_anchor"
                    )
            except Exception as e:
                # Don't fail anchor update if logging fails
                logger.debug(f"Failed to log anchor_update for {symbol}: {e}")

        self.drop_anchors[symbol] = {"price": new_anchor, "ts": ts_iso}
        self.save_state()

    @synchronized()
    def get_drop_anchor(self, symbol: str) -> float:
        """Gibt Drop-Anchor-Preis für Symbol zurück"""
        rec = self.drop_anchors.get(symbol)
        return float(rec.get("price")) if isinstance(rec, dict) and "price" in rec else None

    @synchronized()
    def get_drop_anchor_info(self, symbol: str):
        """
        Liefert (anchor_price, anchor_ts_str) oder (None, None)
        """
        rec = self.drop_anchors.get(symbol) or {}
        price = float(rec['price']) if 'price' in rec else None
        ts = rec.get('ts')  # ISO-String
        return price, ts

    @synchronized()
    def update_held_asset(self, symbol: str, updates: Dict):
        """Aktualisiert gehaltenes Asset"""
        if symbol in self.held_assets:
            u = dict(updates)
            # Stelle sicher dass beide Felder synchron bleiben
            if 'buying_price' in u and 'buy_price' not in u:
                u['buy_price'] = u['buying_price']
            if 'buy_price' in u and 'buying_price' not in u:
                u['buying_price'] = u['buy_price']
            self.held_assets[symbol].update(u)
            self.save_state()

    def get_portfolio_value(self, preise: Dict[str, float]) -> float:
        """Berechnet Gesamtwert des Portfolios"""
        total = self.my_budget
        for symbol, data in self.held_assets.items():
            amount = data.get('amount', 0)
            price = preise.get(symbol, 0)
            total += amount * price
        return total

    def get_portfolio_summary(self) -> Dict:
        """Gibt Portfolio-Zusammenfassung zurück"""
        return {
            'held_assets_count': len(self.held_assets),
            'open_buy_orders_count': len(self.open_buy_orders),
            'budget_usdt': self.my_budget,
            'available_slots': self.get_available_slots()
        }


    def has_open_order(self, symbol: str) -> bool:
        """True, wenn für das Symbol eine offene BUY-Order vorliegt."""
        return symbol in self.open_buy_orders

    def get_symbol_exposure_usdt(self, symbol: str) -> float:
        """Berechnet die gesamte Exposition für ein Symbol in USDT.

        Inkludiert:
        - Gehaltene Assets (amount * buy_price)
        - Reserviertes Budget für das Symbol
        - Offene Buy-Orders für das Symbol
        """
        # Gehaltene Position
        held = self.held_assets.get(symbol) or {}
        amt = float(held.get("amount", 0.0))
        px = float(held.get("buy_price", held.get("buying_price", 0.0)))
        held_cost = amt * px

        # Reserviertes Budget für dieses Symbol
        reserved = float(self.reserved_quote.get(symbol, 0.0))

        # Offene Buy-Order
        open_buy = self.open_buy_orders.get(symbol) or {}
        pending = float(open_buy.get("quote") or open_buy.get("quote_amount", 0.0))

        return held_cost + reserved + pending

    # ==================================================================================
    # OrderRouter Integration - Reserve/Release/Reconcile API
    # ==================================================================================

    def reserve(self, symbol: str, side: str, qty: float, price: float) -> bool:
        """
        Reserve budget for OrderRouter before placing order.

        This wraps the existing reserve_budget() method with the API
        that OrderRouter expects.

        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            qty: Quantity to trade
            price: Price for notional calculation

        Returns:
            True if reservation successful, False if insufficient budget
        """
        try:
            notional = qty * price

            if side == "buy":
                # Buy: reserve quote currency (USDT)
                self.reserve_budget(notional, symbol=symbol, order_info={
                    "side": side,
                    "qty": qty,
                    "price": price
                })
                return True

            else:  # sell
                # Sell: check if we have enough base currency (excludes already reserved)
                base_currency = symbol.split("/")[0]
                available = self.get_balance(base_currency)

                if available + 1e-12 >= qty:
                    self.reserved_base[symbol] = self.reserved_base.get(symbol, 0.0) + qty
                    reservation = self.active_reservations.get(symbol, {})
                    if reservation.get('side') not in (None, 'sell'):
                        reservation = {}
                    reservation.update({
                        "side": "sell",
                        "base": reservation.get('base', 0.0) + qty,
                        "amount": reservation.get('base', 0.0) + qty,
                        "timestamp": time.time(),
                        "order_info": {"side": "sell", "qty": qty, "price": price}
                    })
                    self.active_reservations[symbol] = reservation
                    return True
                else:
                    logger.warning(
                        f"Insufficient {base_currency} for sell: need {qty}, have {available}"
                    )
                    return False

        except ValueError as e:
            # reserve_budget raises ValueError if insufficient
            logger.warning(f"Budget reservation failed for {symbol}: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error in reserve(): {e}")
            return False

    def release(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        exchange_error: Optional[str] = None,
        intent_id: Optional[str] = None
    ) -> None:
        """
        Release reserved budget (on order failure or partial fill).

        This wraps the existing release_budget() method.

        Args:
            symbol: Trading pair
            side: "buy" or "sell"
            qty: Quantity to release
            price: Price used for notional calculation
            exchange_error: Exchange error message/code (if failure)
            intent_id: Intent ID for tracing
        """
        try:
            notional = qty * price

            if side == "buy":
                # Release reserved quote currency
                # Include exchange_error and intent_id in reason for observability
                reason = "order_router_release"
                if exchange_error and intent_id:
                    reason = f"order_router_release|intent={intent_id}|error={exchange_error[:50]}"
                elif intent_id:
                    reason = f"order_router_release|intent={intent_id}"

                self.release_budget(notional, symbol=symbol, reason=reason)

            else:  # sell
                current_reserved = self.reserved_base.get(symbol, 0.0)
                self.reserved_base[symbol] = max(0.0, current_reserved - qty)
                if self.reserved_base.get(symbol, 0.0) == 0.0:
                    self.reserved_base.pop(symbol, None)

                reservation = self.active_reservations.get(symbol)
                if reservation and reservation.get('side') == 'sell':
                    remaining = max(0.0, reservation.get('base', 0.0) - qty)
                    if remaining <= 0:
                        self.active_reservations.pop(symbol, None)
                    else:
                        reservation['base'] = remaining
                        reservation['amount'] = remaining
                        reservation['timestamp'] = time.time()
                        self.active_reservations[symbol] = reservation

        except Exception as e:
            logger.error(f"Error releasing budget for {symbol}: {e}")

    def apply_fills(self, symbol: str, trades: list) -> dict:
        """
        Apply exchange fills to portfolio using Position lifecycle.

        Converts reservations into actual positions with accurate
        prices, fees, and PnL tracking. Updates position state machine.

        Position State Transitions:
        - NEW → OPEN (first fills)
        - OPEN → PARTIAL_EXIT (position being reduced)
        - any → CLOSED (qty = 0)

        Args:
            symbol: Trading pair
            trades: List of trade dicts from exchange with:
                - price: Execution price
                - amount: Filled quantity
                - cost: Notional (price * amount)
                - fee: Fee dict with cost and currency
                - side: "buy" or "sell"

        Returns:
            Summary dict with qty_delta, notional, fees, state
        """
        try:
            if not trades:
                logger.warning(f"apply_fills called with no trades for {symbol}")
                return {}

            # Get or create position
            pos = self.positions.get(symbol)
            if pos is None:
                pos = Position(
                    symbol=symbol,
                    state="NEW",
                    qty=0.0,
                    avg_price=0.0,
                    realized_pnl=0.0,
                    fees_paid=0.0,
                    opened_ts=time.time()
                )
                self.positions[symbol] = pos

            # Aggregate trade data
            tot_qty, notional, fees = 0.0, 0.0, 0.0
            buy_quote_total = 0.0
            sell_quote_total = 0.0
            buy_fees = 0.0
            sell_fees = 0.0
            sell_qty_total = 0.0

            for t in trades:
                q = float(t.get("amount", 0))
                px = float(t.get("price", 0))
                f = float((t.get("fee") or {}).get("cost") or 0.0)
                side = t.get("side", "buy")

                # Fee conversion if needed
                fee_curr = (t.get("fee") or {}).get("currency", "")
                if fee_curr != "USDT" and f > 0:
                    f = f * px  # Convert base fee to quote
                fee_value = f if f > 0 else q * px * self.fee_rate

                signed = q if side == "buy" else -q

                # Update avg_price (only for net additions)
                if (pos.qty >= 0 and signed > 0) or (pos.qty <= 0 and signed < 0):
                    # Adding to position (long or short)
                    if abs(pos.qty) > 0:
                        pos.avg_price = (
                            abs(pos.qty) * pos.avg_price + abs(signed) * px
                        ) / (abs(pos.qty) + abs(signed))
                    else:
                        pos.avg_price = px
                else:
                    # Reducing position → calculate realized PnL
                    reduced = min(abs(pos.qty), abs(signed))
                    if reduced > 0:
                        pnl_unit = (px - pos.avg_price) * (1 if pos.qty > 0 else -1)
                        pos.realized_pnl += pnl_unit * reduced

                pos.qty += signed
                tot_qty += signed
                notional += q * px
                fees += fee_value

                if side == "buy":
                    buy_quote_total += q * px
                    buy_fees += fee_value
                else:
                    sell_quote_total += q * px
                    sell_fees += fee_value
                    sell_qty_total += q

            pos.fees_paid += fees

            # State transitions
            if pos.qty == 0:
                pos.state = "CLOSED"
                pos.closed_ts = time.time()
            elif pos.state == "NEW" and pos.qty != 0:
                pos.state = "OPEN"
            elif pos.state == "OPEN" and self._is_reducing(trades):
                pos.state = "PARTIAL_EXIT"

            # CRITICAL FIX (C-PORT-03): Partial fill budget leak fixed
            # Only commit what was actually spent (cost + fees), not the full reservation
            if buy_quote_total > 0:
                reserved_for_symbol = self.reserved_quote.get(symbol, 0.0)

                # Calculate actual total cost including fees
                actual_cost = buy_quote_total + buy_fees

                # Only commit what was actually spent, up to the reservation
                commit_amount = min(reserved_for_symbol, actual_cost)

                if commit_amount > 0:
                    self.commit_budget(commit_amount, symbol=symbol)

                # Release any surplus reservation (important for partial fills)
                surplus_reserved = max(0.0, reserved_for_symbol - commit_amount)
                if surplus_reserved > 0:
                    self.release_budget(surplus_reserved, symbol=symbol, reason="partial_fill_surplus")

                # Handle case where actual cost exceeds reservation (slippage)
                extra_cost = max(0.0, actual_cost - reserved_for_symbol)
                if extra_cost > 0:
                    logger.warning(
                        f"Actual cost ({actual_cost:.2f}) exceeded reservation ({reserved_for_symbol:.2f}) by {extra_cost:.2f} USDT",
                        extra={'event_type': 'BUDGET_SLIPPAGE_OVERAGE', 'symbol': symbol, 'overage': extra_cost}
                    )
                    self.my_budget = max(0.0, self.my_budget - extra_cost)

            if sell_qty_total > 0:
                reserved_base_amt = self.reserved_base.get(symbol, 0.0)
                remaining_base = max(0.0, reserved_base_amt - sell_qty_total)

                if remaining_base > 0:
                    self.reserved_base[symbol] = remaining_base
                else:
                    self.reserved_base.pop(symbol, None)

                reservation = self.active_reservations.get(symbol)
                if reservation and reservation.get('side') == 'sell':
                    if remaining_base <= 0:
                        self.active_reservations.pop(symbol, None)
                    else:
                        reservation['base'] = remaining_base
                        reservation['amount'] = remaining_base
                        reservation['timestamp'] = time.time()
                        self.active_reservations[symbol] = reservation

                cash_inflow = max(0.0, sell_quote_total - sell_fees)
                if cash_inflow > 0:
                    self.my_budget += cash_inflow

            # Clean up any lingering reservation metadata
            if symbol in self.active_reservations and not self.active_reservations[symbol]:
                self.active_reservations.pop(symbol, None)

            # Keep settlement manager in sync with latest cash balance
            try:
                self.settlement_manager.update_verified_budget(self.my_budget)
            except Exception as settlement_error:
                logger.debug(f"Failed to update settlement manager after fills: {settlement_error}")

            # Emit position event
            self._emit_event({
                "type": "position_update",
                "symbol": symbol,
                "qty": pos.qty,
                "avg": pos.avg_price,
                "fees": pos.fees_paid,
                "state": pos.state,
                "timestamp": time.time()
            })

            # Legacy: also update held_assets for backwards compatibility
            if pos.qty > 0:
                self.add_held_asset(symbol, {
                    "amount": pos.qty,
                    "entry_price": pos.avg_price,
                    "buy_price": pos.avg_price,
                    "buy_fee_quote_per_unit": pos.fees_paid / pos.qty if pos.qty > 0 else 0.0,
                    "first_fill_ts": pos.opened_ts
                })
            elif pos.state == "CLOSED":
                self.remove_held_asset(symbol)

            logger.info(
                f"Applied {len(trades)} fills for {symbol}: "
                f"qty={pos.qty:.8f}, avg={pos.avg_price:.8f}, "
                f"state={pos.state}, fees={fees:.4f}"
            )

            return {
                "qty_delta": tot_qty,
                "notional": notional,
                "fees": fees,
                "state": pos.state
            }

        except Exception as e:
            logger.error(f"Error applying fills for {symbol}: {e}", exc_info=True)
            return {}

    def _is_reducing(self, trades: List[dict]) -> bool:
        """
        Check if trades contain both buy and sell sides (position reduction).

        Args:
            trades: List of trade dicts

        Returns:
            True if trades contain mixed sides
        """
        sides = set(t.get("side") for t in trades)
        return ("buy" in sides and "sell" in sides)

    def last_price(self, symbol: str) -> Optional[float]:
        """
        Get last known price for symbol.

        Used by OrderRouter for slippage guard and budget calculations.

        Args:
            symbol: Trading pair

        Returns:
            Last price or None if not available
        """
        try:
            # Try to get from held assets (entry price)
            if symbol in self.held_assets:
                asset = self.held_assets[symbol]
                entry_price = asset.get("entry_price") or asset.get("buy_price")
                if entry_price and entry_price > 0:
                    return float(entry_price)

            # Try to get from open buy orders
            if symbol in self.open_buy_orders:
                order = self.open_buy_orders[symbol]
                order_price = order.get("price")
                if order_price and order_price > 0:
                    return float(order_price)

            # Fallback: fetch from exchange
            if self.exchange:
                ticker = self.exchange.fetch_ticker(symbol)
                last = ticker.get("last")
                if last and last > 0:
                    return float(last)

            return None

        except Exception as e:
            logger.debug(f"Could not get last price for {symbol}: {e}")
            return None

    # ==================================================================================
    # Position Lifecycle & PnL Management (NEW - Reconciliation System)
    # ==================================================================================

    def mark_price(self, symbol: str, last: float) -> None:
        """
        Mark position with current market price for valuation.

        Used by engine to update position valuations from market snapshots.

        Args:
            symbol: Trading pair
            last: Current market price
        """
        self.last_prices[symbol] = last

    def unrealized_pnl(self, symbol: str) -> float:
        """
        Calculate unrealized PnL for a position.

        Args:
            symbol: Trading pair

        Returns:
            Unrealized PnL in quote currency (positive = profit, negative = loss)
        """
        pos = self.positions.get(symbol)
        last = self.last_prices.get(symbol)

        if not pos or last is None or pos.qty == 0:
            return 0.0

        direction = 1 if pos.qty > 0 else -1
        return (last - pos.avg_price) * abs(pos.qty) * direction

    def position_view(self, symbol: str) -> dict:
        """
        Get consolidated position view for UI/monitoring.

        Provides single source of truth for position state, PnL, and fees.

        Args:
            symbol: Trading pair

        Returns:
            Dict with:
                - qty: Current quantity
                - avg: Average entry price
                - state: Position state (NEW/OPEN/PARTIAL_EXIT/CLOSED)
                - upnl: Unrealized PnL
                - rpnl: Realized PnL
                - fees: Total fees paid
        """
        pos = self.positions.get(symbol)

        if not pos:
            return {
                "qty": 0.0,
                "avg": 0.0,
                "state": "NONE",
                "upnl": 0.0,
                "rpnl": 0.0,
                "fees": 0.0
            }

        upnl = self.unrealized_pnl(symbol)

        return {
            "qty": pos.qty,
            "avg": pos.avg_price,
            "state": pos.state,
            "upnl": upnl,
            "rpnl": pos.realized_pnl,
            "fees": pos.fees_paid
        }

    def save_open_buy_order(self, symbol: str, order_data: dict) -> None:
        """
        Persist open buy order to disk for crash recovery.

        Args:
            symbol: Trading symbol
            order_data: Dict with order_id, client_order_id, price, amount, etc.
        """
        with self._lock:
            self.open_buy_orders[symbol] = order_data
            self.save_state()  # Persist to disk immediately
            logger.debug(f"Persisted buy order for {symbol}: order_id={order_data.get('order_id')}")

    def get_open_buy_order(self, symbol: str) -> Optional[dict]:
        """
        Retrieve persisted buy order data.

        Args:
            symbol: Trading symbol

        Returns:
            Order data dict or None if not found
        """
        with self._lock:
            return self.open_buy_orders.get(symbol)

    def _emit_event(self, ev: dict) -> None:
        """
        Emit position event for audit trail.

        Args:
            ev: Event dict to append to events list
        """
        self.events.append(ev)

    def get_positions_with_ghosts(self, engine=None) -> List[Dict]:
        """
        Get all positions including ghost positions (rejected buy intents).

        Ghost positions are buy intents that failed compliance validation.
        They're included for UI transparency but don't affect PnL or budget.

        Args:
            engine: Optional FSM engine with ghost_store

        Returns:
            List of position dicts with held assets + ghost positions
        """
        positions = []

        # Add real held positions
        with self._lock:
            for symbol, asset in (self.held_assets or {}).items():
                try:
                    amount = float(asset.get('amount', 0))
                    if amount > 0:
                        entry_price = asset.get('entry_price', 0) or asset.get('buy_price', 0)
                        positions.append({
                            'symbol': symbol,
                            'amount': amount,
                            'entry_price': entry_price,
                            'state': 'OPEN',
                            'is_ghost': False
                        })
                except Exception as e:
                    logger.debug(f"Failed to parse position {symbol}: {e}")

        # Add ghost positions if engine available
        if engine and hasattr(engine, 'ghost_store'):
            try:
                ghosts = engine.ghost_store.list_active()
                for ghost in ghosts:
                    positions.append({
                        'symbol': ghost['symbol'],
                        'amount': 0,  # Ghost has no actual size
                        'entry_price': ghost['q_price'],
                        'state': 'GHOST',
                        'is_ghost': True,
                        'ghost_id': ghost['id'],
                        'abort_reason': ghost['abort_reason'],
                        'violations': ghost.get('violations', []),
                        'raw_price': ghost.get('raw_price'),
                        'raw_amount': ghost.get('raw_amount'),
                        'market_precision': ghost.get('market_precision', {})
                    })
            except Exception as e:
                logger.debug(f"Failed to fetch ghost positions: {e}")

        return positions
