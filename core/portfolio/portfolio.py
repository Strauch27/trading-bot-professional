# portfolio.py - Portfolio und State Management
import os
import time
import json
import threading
import functools
from typing import Dict, List, Tuple, Optional, Any
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

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
    STATE_FILE_HELD, STATE_FILE_OPEN_BUYS, DROP_ANCHORS_FILE,
    max_trades, cash_reserve_usdt, symbol_cooldown_minutes,
    reset_portfolio_on_start, safe_min_budget,
    use_drop_anchor_since_last_close,
    DUST_TARGET_QUOTE, DUST_MIN_COST_USD, DUST_FORCE_MARKET_IOC
)
from core.logging.logger_setup import logger
from core.logging.loggingx import log_event, log_metric, log_audit_event
from core.utils import (
    save_state_safe, load_state, save_trade_history,
    SettlementManager, get_symbol_limits
)
from trading import (
    refresh_budget_from_exchange,
    full_portfolio_reset
)
from trading.helpers import _get_free, _base_currency


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
        from datetime import datetime, timezone
        import time
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
        self.active_reservations: Dict[str, Dict] = {}  # symbol -> {amount, timestamp, order_info}
        
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
            from core.utils import with_backoff
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
                diff = abs(actual_amount - state_amount)
                
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
            success = full_portfolio_reset(
                self.exchange,
                self.settlement_manager
            )
            
            # Immer State synchronisieren nach Reset-Versuch
            refresh_func = lambda: refresh_budget_from_exchange(self.exchange, self.my_budget)
            
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
            return False
    
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
            
        actual_budget = refresh_budget_from_exchange(self.exchange, self.my_budget)
        
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
        return False
    
    def set_cooldown(self, symbol: str):
        """Setzt Cooldown-Timer für Symbol"""
        self.last_buy_time[symbol] = time.time()
    
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
        """Fügt gehaltenes Asset hinzu mit WAC und Fee-Tracking"""
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
        """Release budget on partial fill
        
        Args:
            symbol: Trading pair
            client_order_id: The clientOrderId of the order
            filled_quote: How much was actually filled in quote currency
            orig_quote: Original quote amount that was reserved
        """
        if orig_quote <= 0:
            return
            
        # Calculate how much to release
        fill_ratio = filled_quote / orig_quote
        reserved = self.reserved_quote.get(symbol, 0.0)
        release = reserved * (1 - fill_ratio)  # Release unfilled portion
        
        if release > 0:
            self.reserved_quote[symbol] = max(0.0, reserved - release)
            self.my_budget += release  # Return to available budget
            
            log_event("BUDGET_RELEASED_PARTIAL", symbol=symbol, context={
                "clientOrderId": client_order_id,
                "released_usdt": float(release),
                "filled_quote": float(filled_quote),
                "orig_quote": float(orig_quote),
                "fill_ratio": float(fill_ratio)
            })
            
            # Persist state
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
            self.active_reservations[symbol] = {
                'amount': quote_amount,
                'timestamp': time.time(),
                'order_info': order_info or {}
            }
        
        log_event("BUDGET_RESERVED", context={
            "reserved": float(quote_amount),
            "total_reserved": float(self.reserved_budget),
            "available": float(self.my_budget - self.reserved_budget),
            "symbol": symbol,
            "order_info": order_info
        })
    
    @synchronized_budget
    def release_budget(self, quote_amount: float, symbol: str | None = None, reason: str = "unknown"):
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
            if symbol in self.active_reservations:
                del self.active_reservations[symbol]
        
        log_event("BUDGET_RELEASED", context={
            "released": float(quote_amount),
            "total_reserved": float(self.reserved_budget),
            "available": float(self.my_budget - self.reserved_budget),
            "symbol": symbol,
            "reason": reason
        })
    
    @synchronized_budget
    def commit_budget(self, quote_amount: float, symbol: str | None = None, order_id: str = None):
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
            if symbol in self.active_reservations:
                del self.active_reservations[symbol]
        
        log_event("BUDGET_COMMITTED", context={
            "committed": float(quote_amount),
            "remaining_budget": float(self.my_budget),
            "reserved": float(self.reserved_budget),
            "symbol": symbol,
            "order_id": order_id
        })
    
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
        
        total_released = 0.0
        for symbol in stale_symbols:
            reservation = self.active_reservations[symbol]
            amount = reservation['amount']
            self.release_budget(amount, symbol, reason="stale_cleanup")
            total_released += amount
            logger.warning(f"Cleaned up stale budget reservation: {symbol} - {amount:.2f} USDT (age: {current_time - reservation['timestamp']:.1f}s)",
                         extra={'event_type': 'STALE_BUDGET_CLEANUP', 'symbol': symbol, 'amount': amount})
        
        if total_released > 0:
            logger.info(f"Total stale budget released: {total_released:.2f} USDT from {len(stale_symbols)} reservations",
                       extra={'event_type': 'STALE_BUDGET_CLEANUP_SUMMARY', 'total_released': total_released, 'count': len(stale_symbols)})
        
        return total_released
    
    @synchronized_budget  
    def reconcile_budget_from_exchange(self):
        """Budget-Reconciliation mit Exchange (Drift-Erkennung)"""
        if not self.exchange:
            return
        
        try:
            actual_budget = _get_free(self.exchange, "USDT")
            expected_budget = self.my_budget
            drift = abs(actual_budget - expected_budget)
            
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
        self.drop_anchors[symbol] = {"price": float(price), "ts": ts_iso}
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
