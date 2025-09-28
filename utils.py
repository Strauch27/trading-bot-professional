# utils.py - Utility-Funktionen und State-Management 
import os
import json
import pandas as pd
import math
from datetime import datetime, timezone
from collections import deque
import time
import random
import ccxt
from typing import List, Dict, Tuple
from config import (
    HISTORY_FILE, RUN_ID, symbol_min_cost_override,
    FEE_RATE, stop_loss_threshold, take_profit_threshold, be_activation_pct,
    trailing_stop_activation_pct, trailing_stop_distance_pct,
    use_trailing_take_profit, trailing_tp_activation_pct,
    trailing_tp_step_bp, trailing_tp_under_high_bp
)
from logger_setup import logger
from loggingx import (
    log_event, log_metric, sl_breakeven_bump, 
    sl_trail_update, tp_trail_update,
    trailing_bump, trailing_hit
)
import itertools
import re

# Self-Check für Min-Notional-Warnungen (V9_3-style)
def log_min_notional_warnings(exchange, symbols, position_size_usdt, buffer=1.02):
    """
    Prüft alle Symbole gegen POSITION_SIZE_USDT und warnt bei knappen Notional-Limits.

    Args:
        exchange: Exchange-Adapter mit markets
        symbols: Liste der zu prüfenden Symbole
        position_size_usdt: Konfigurierte Position-Size
        buffer: Puffer-Faktor für minNotional (default 2%)
    """
    warnings = []
    for s in symbols:
        try:
            m = exchange.get_market_info(s)
            if not m:
                continue
            limits = (m.get("limits") or {}).get("cost") or {}
            min_cost = float(limits.get("min") or 0.0)
            if min_cost > 0:
                need = max(min_cost * buffer, position_size_usdt)
                if position_size_usdt + 1e-9 < min_cost * buffer:
                    warning_msg = f"[WARN] {s}: POSITION_SIZE_USDT ({position_size_usdt}) < minNotional*{buffer:.2f} ({need:.2f})"
                    print(warning_msg)
                    logger.warning(warning_msg, extra={
                        'event_type': 'MIN_NOTIONAL_WARNING',
                        'symbol': s,
                        'position_size_usdt': position_size_usdt,
                        'min_notional': min_cost,
                        'buffer': buffer,
                        'required': need
                    })
                    warnings.append({'symbol': s, 'current': position_size_usdt, 'required': need})
        except Exception as e:
            logger.error(f"Error checking min notional for {s}: {e}")

    if warnings:
        logger.info(f"Min-Notional check completed: {len(warnings)} warnings found")
    else:
        logger.info("Min-Notional check completed: All symbols OK")

    return warnings

# =================================================================================
# Client Order ID Generator
# =================================================================================
_seq = itertools.count(1)
_MAX_COID_LEN = 32

def next_client_order_id(symbol: str, side: str, decision_id: str | None = None) -> str:
    """
    Kompakter, MEXC-kompatibler clientOrderId-Generator (≤32 Zeichen).
    Format: prefix(6) + symbol(10) + side(1) + nonce(7)
    Optional mit decision_id für Tracing
    
    MEXC erlaubt nur [0-9a-zA-Z_-] und max. 32 Zeichen
    """
    import time
    
    prefix = RUN_ID.replace("-", "")[:6]  # 6 Zeichen von RUN_ID
    sym = re.sub(r'[^0-9A-Za-z]', '', symbol.upper())[:10]  # max 10 Zeichen Symbol
    nonce = int(time.time() * 1000) % 10_000_000  # 7-stellige Zahl
    
    # Optional: decision_id einbauen für Tracing (wenn vorhanden)
    if decision_id:
        # Nutze decision_id als Teil des Prefix (max 10 chars von decision_id)
        dec_part = decision_id[:10]
        coid = f"{dec_part}{sym}{side[:1]}{nonce}"
    else:
        coid = f"{prefix}{sym}{side[:1]}{nonce}"
    
    # Sanity check und sanitize
    coid = re.sub(r'[^0-9A-Za-z_-]', '', coid)[:_MAX_COID_LEN]
    return coid

# =================================================================================
# Price and Amount Quantization
# =================================================================================
def quantize_price(symbol: str, price: float, exchange) -> float:
    """Quantize price to exchange precision"""
    try:
        return float(exchange.price_to_precision(symbol, price))
    except Exception:
        # Fallback to 8 decimal places
        return round(price, 8)

def quantize_amount(symbol: str, amount: float, exchange) -> float:
    """Quantize amount to exchange precision"""
    try:
        return float(exchange.amount_to_precision(symbol, amount))
    except Exception:
        # Fallback to 8 decimal places
        return round(amount, 8)

# =================================================================================
# Retry and Backoff Helpers
# =================================================================================
RETRYABLE_ERRORS = (ccxt.RateLimitExceeded, ccxt.NetworkError, ccxt.ExchangeNotAvailable)

def with_backoff(fn, *args, max_retries: int = 5, base_delay: float = 0.25, jitter: float = 0.15, **kwargs):
    """
    Ruft fn mit exponentiellem Backoff + Jitter auf (fängt 429/Netzwerk sauber ab).
    """
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except RETRYABLE_ERRORS as e:
            if attempt >= max_retries:
                raise
            delay = (base_delay * (2 ** attempt)) + random.uniform(0, jitter)
            time.sleep(delay)
            attempt += 1

# =================================================================================
# Ticker Cache mit Backoff
# =================================================================================
_ticker_cache = {}

def fetch_ticker_cached(exchange, symbol: str, ttl_s: float = 2.0):
    """Fetch ticker mit In-Memory Cache und exponential backoff
    
    Args:
        exchange: CCXT Exchange Instanz
        symbol: Trading-Paar (z.B. "ETH/USDT")
        ttl_s: Cache Time-To-Live in Sekunden
    
    Returns:
        Ticker-Dictionary von der Exchange
    """
    now = time.time()
    item = _ticker_cache.get(symbol)
    
    # Cache-Hit
    if item and (now - item["t"]) <= ttl_s:
        return item["v"]
    
    # Retry mit exponential backoff
    retry_count = 0
    for retry in (0.2, 0.5, 1.0):
        try:
            v = exchange.fetch_ticker(symbol)
            _ticker_cache[symbol] = {"v": v, "t": now}
            return v
        except Exception as e:
            retry_count += 1
            # Log API retry metric
            if retry_count > 1:  # Don't count first attempt as retry
                log_metric("API_RETRY_COUNT", 1, {"endpoint": "fetch_ticker", "symbol": symbol})
            
            if retry < 1.0:  # Nicht beim letzten Versuch schlafen
                time.sleep(retry)
            else:
                # Beim letzten Fehlschlag Exception weitergeben
                raise
    
# =================================================================================
# State-Management-Funktionen
# =================================================================================
def save_state_safe(data, file_path):
    """Speichert State-Datei atomisch"""
    temp_file_path = file_path + ".tmp"
    try:
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(temp_file_path, file_path)
        
        # Erfolg loggen
        file_name = os.path.basename(file_path)
        data_count = len(data) if hasattr(data, '__len__') else 0
        logger.debug(f"State saved: {file_name} ({data_count} entries)",
                    extra={'event_type': 'STATE_SAVED', 
                          'file': file_name, 
                          'entries': data_count})
    except Exception as e:
        # Detailliertes Logging für State-Save-Fehler
        error_context = {
            'file_path': file_path,
            'temp_file_path': temp_file_path if 'temp_file_path' in locals() else None,
            'data_type': type(data).__name__,
            'data_size': len(data) if hasattr(data, '__len__') else 'unknown'
        }
        
        # Prüfe auf spezifische Fehlertypen
        if 'permission' in str(e).lower():
            error_context['likely_cause'] = 'Keine Schreibrechte im Verzeichnis'
        elif 'disk' in str(e).lower() or 'space' in str(e).lower():
            error_context['likely_cause'] = 'Festplatte voll'
        elif 'json' in str(e).lower():
            error_context['likely_cause'] = 'Daten nicht JSON-serialisierbar'
        
        logger.error("Fehler beim Speichern des Zustands.", 
                    extra={'event_type': 'STATE_SAVE_ERROR', **error_context})

def load_state(file_path):
    """Lädt State-Datei"""
    if not os.path.exists(file_path): 
        logger.debug(f"State file not found: {os.path.basename(file_path)} (will start fresh)",
                    extra={'event_type': 'STATE_FILE_NOT_FOUND', 
                          'file': os.path.basename(file_path)})
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content: 
                logger.debug(f"State file empty: {os.path.basename(file_path)}",
                            extra={'event_type': 'STATE_FILE_EMPTY', 
                                  'file': os.path.basename(file_path)})
                return {}
            data = json.loads(content)
            data_count = len(data) if hasattr(data, '__len__') else 0
            logger.debug(f"State loaded: {os.path.basename(file_path)} ({data_count} entries)",
                        extra={'event_type': 'STATE_LOADED', 
                              'file': os.path.basename(file_path),
                              'entries': data_count})
            return data
    except Exception as e:
        logger.warning(f"Zustandsdatei {file_path} konnte nicht geladen werden.", 
                      extra={'event_type': 'STATE_LOAD_WARNING', 'file_path': file_path, 'error': str(e)})
        return {}

def _sum_trade_fees_quote(trades: List[dict]) -> float:
    """
    Summiert Fees in Quote; wenn Fee in Base gezahlt wurde, konvertiert per Tradepreis in Quote.
    Erwartet ccxt-ähnliche Trades mit Feldern: price, amount, fee{cost,currency}, symbol.
    """
    total = 0.0
    for t in (trades or []):
        fee = t.get("fee") or {}
        cost = float(fee.get("cost") or 0.0)
        if cost == 0.0:
            continue
        cur = (fee.get("currency") or "").upper()
        price = float(t.get("price") or 0.0)
        symbol = (t.get("symbol") or "")
        quote  = symbol.split("/")[-1].upper() if "/" in symbol else ""
        if cur and quote and cur == quote:
            total += cost
        elif price > 0.0:
            # vermutlich Fee in Base -> in Quote umrechnen
            total += cost * price
    return total

def compute_avg_fill_and_fees(order: dict, trades: List[dict]) -> Tuple[float, float, float, float]:
    """
    Liefert (avg_price, filled_qty, proceeds_quote, total_fees_quote).
    Nutzt Trades, fällt sonst auf Order-Felder (average, filled, fee) zurück.
    """
    if trades:
        filled = sum(float(t.get("amount") or 0.0) for t in trades)
        notional = sum(float(t.get("price") or 0.0) * float(t.get("amount") or 0.0) for t in trades)
        if filled > 0:
            avg_price = notional / filled
            fees_q = _sum_trade_fees_quote(trades)
            return avg_price, filled, notional, fees_q
    # Fallback auf Order-Level
    avg_price = float(order.get("average") or order.get("price") or 0.0)
    filled    = float(order.get("filled") or 0.0)
    proceeds  = avg_price * filled
    fees_q    = float((order.get("fee") or {}).get("cost") or 0.0)
    return avg_price, filled, proceeds, fees_q

def compute_realized_pnl_net_sell(
    symbol: str,
    order: dict,
    trades: List[dict],
    entry_avg_price: float,
    buy_fee_quote_per_unit: float
) -> dict:
    """
    Netto-PnL (Quote) für SELL (Long-Spot) aus echten Fills:
    PnL_net = (Sell-Proceeds_quote - SellFees_quote) - (Qty * EntryAvgPrice + BuyFees_alloc_quote).
    """
    exit_avg_px, qty, proceeds_q, sell_fees_q = compute_avg_fill_and_fees(order, trades)
    buy_fees_alloc_q = (buy_fee_quote_per_unit or 0.0) * qty
    cost_basis_q = entry_avg_price * qty + buy_fees_alloc_q
    pnl_net_q = proceeds_q - sell_fees_q - cost_basis_q
    return {
        "avg_exit_price": exit_avg_px,
        "qty": qty,
        "proceeds_quote": proceeds_q,
        "sell_fees_quote": sell_fees_q,
        "buy_fees_alloc_quote": buy_fees_alloc_q,
        "entry_avg_price": entry_avg_price,
        "cost_basis_quote": cost_basis_q,
        "pnl_net_quote": pnl_net_q,
    }

def save_trade_history(trade_data):
    """Erweiterte Trade-History mit mehr Details für ML und Analysis"""
    # Ergänze Standard-Felder
    trade_data['run_id'] = RUN_ID

    # Only set timestamp_utc if not already provided (preserve exchange timestamps)
    if not trade_data.get('timestamp_utc'):

        trade_data['timestamp_utc'] = datetime.now(timezone.utc).isoformat()


    # Always capture log time separately from trade time
    trade_data['logged_at'] = datetime.now(timezone.utc).isoformat()

    # Füge fehlende Felder mit Defaults hinzu falls nicht vorhanden
    trade_data.setdefault('client_order_id', '')
    trade_data.setdefault('exchange_order_id', '')
    trade_data.setdefault('side', 'sell')  # Default ist sell (beim Schließen)
    trade_data.setdefault('quantity', 0)
    trade_data.setdefault('fee_asset', 'USDT')
    trade_data.setdefault('fee_amount', 0)
    trade_data.setdefault('is_maker', False)
    trade_data.setdefault('trigger_reason', '')
    trade_data.setdefault('revenue_usdt', 0)
    
    file_exists = os.path.exists(HISTORY_FILE)
    df_to_save = pd.DataFrame([trade_data])
    df_to_save.to_csv(HISTORY_FILE, mode='a', header=not file_exists, index=False)

# =================================================================================
# Settlement-Manager für robuste Budget-Synchronisation
# =================================================================================
class SettlementManager:
    """Verwaltet pending settlements und verhindert Race-Conditions"""
    def __init__(self, dust_sweeper=None):
        self.pending_settlements = {}  # order_id -> {'amount': x, 'timestamp': y}
        self.last_verified_budget = 0
        self.settlement_history = deque(maxlen=100)  # Für Statistiken
        self.dust_sweeper = dust_sweeper  # Optional: DustSweeper für Dust-Handling
        
    def add_pending(self, order_id, amount, symbol="", **extra):
        """Fügt ein pending settlement hinzu mit optionalen Extra-Daten"""
        self.pending_settlements[order_id] = {
            'amount': amount,
            'timestamp': time.time(),
            'symbol': symbol,
            **extra  # Entry price, reason, etc.
        }
        logger.debug(f"Settlement pending: {amount:.2f} USDT von {symbol}", 
                    extra={'event_type': 'SETTLEMENT_PENDING', 'order_id': order_id, 
                          'amount': amount, 'symbol': symbol})
    
    def remove_pending(self, order_id):
        """Entfernt ein settlement nach Bestätigung"""
        if order_id in self.pending_settlements:
            settlement = self.pending_settlements.pop(order_id)
            self.settlement_history.append({
                'order_id': order_id,
                'amount': settlement['amount'],
                'duration': time.time() - settlement['timestamp']
            })
    
    def get_total_pending(self):
        """Berechnet die Summe aller pending settlements"""
        return sum(s['amount'] for s in self.pending_settlements.values())
    
    def wait_for_all_settlements(self, refresh_budget_func, timeout=60, tolerance=0.99, check_interval=30, max_attempts=8):
        """
        Wartet bis alle pending settlements angekommen sind.
        Nutzt progressiven Retry-Mechanismus mit mehreren kurzen Warteperioden.
        """
        if not self.pending_settlements:
            return True
            
        start = time.time()
        initial_pending = self.get_total_pending()
        attempts = 0
        
        logger.info(f"Warte auf {len(self.pending_settlements)} settlements ({initial_pending:.2f} USDT total)...", 
                   extra={'event_type': 'WAITING_FOR_SETTLEMENTS', 
                         'count': len(self.pending_settlements), 
                         'total_amount': initial_pending})
        
        # Progressive Retry-Schleife
        while self.pending_settlements and attempts < max_attempts:
            attempts += 1
            check_start = time.time()
            
            # Warte für ein Intervall mit kontinuierlichen Budget-Checks
            while (time.time() - check_start) < check_interval and self.pending_settlements:
                actual = refresh_budget_func()
                expected = self.last_verified_budget + self.get_total_pending()
                
                if actual >= expected * tolerance:  # Toleranz für Fees
                    cleared = list(self.pending_settlements.keys())
                    for order_id in cleared:
                        self.remove_pending(order_id)
                    self.last_verified_budget = actual
                    
                    logger.info(f"Alle settlements bestätigt nach {time.time()-start:.1f}s (Versuch {attempts})", 
                              extra={'event_type': 'SETTLEMENTS_CONFIRMED', 
                                    'duration': time.time()-start,
                                    'attempts': attempts,
                                    'new_budget': actual})
                    return True
                
                # Prüfe auf Teil-Settlements
                if actual > self.last_verified_budget:
                    partial = actual - self.last_verified_budget
                    logger.debug(f"Partial settlement: {partial:.2f} USDT angekommen (Versuch {attempts})", 
                               extra={'event_type': 'PARTIAL_SETTLEMENT', 
                                     'amount': partial,
                                     'attempts': attempts})
                    
                    # Entferne settlements die teilweise erfüllt wurden
                    partially_cleared = []
                    remaining = self.get_total_pending()
                    if partial >= remaining * 0.5:  # Wenn mind. 50% angekommen
                        for order_id, settlement in self.pending_settlements.items():
                            if settlement['amount'] <= partial * 1.1:  # Mit kleiner Toleranz
                                partially_cleared.append(order_id)
                    
                    for order_id in partially_cleared:
                        self.remove_pending(order_id)
                    
                    self.last_verified_budget = actual
                
                time.sleep(1)
            
            # Log nach jedem Versuch
            if self.pending_settlements and attempts < max_attempts:
                logger.debug(f"Settlement Check {attempts}/{max_attempts}: Noch {len(self.pending_settlements)} ausstehend", 
                           extra={'event_type': 'SETTLEMENT_CHECK_RETRY',
                                 'attempt': attempts,
                                 'max_attempts': max_attempts,
                                 'pending_count': len(self.pending_settlements),
                                 'pending_amount': self.get_total_pending()})
        
        # Nach allen Versuchen - entferne alte settlements
        expired = []
        for order_id, settlement in self.pending_settlements.items():
            if time.time() - settlement['timestamp'] > 180:  # 3 Minuten alt
                expired.append(order_id)
        
        for order_id in expired:
            self.remove_pending(order_id)
            logger.warning(f"Settlement timeout für Order {order_id} nach {attempts} Versuchen", 
                         extra={'event_type': 'SETTLEMENT_TIMEOUT', 
                               'order_id': order_id,
                               'attempts': attempts})
        
        # Return True wenn nur noch sehr kleine Beträge ausstehen
        remaining = self.get_total_pending()
        if remaining < 1.0:  # Weniger als 1 USDT
            logger.info(f"Kleine Settlement-Reste ignoriert: {remaining:.2f} USDT",
                       extra={'event_type': 'SETTLEMENT_SMALL_REMAINDER',
                             'amount': remaining})
            return True
        
        return False
    
    def update_verified_budget(self, amount):
        """Aktualisiert das verifizierte Budget"""
        self.last_verified_budget = amount
    
    def get_average_settlement_time(self):
        """Berechnet die durchschnittliche Settlement-Zeit"""
        if not self.settlement_history:
            return 5.0  # Default
        return sum(s['duration'] for s in self.settlement_history) / len(self.settlement_history)
    
    def on_sell_settled(self, order_id, symbol, exit_price, amount, fee=0.0, **extra):
        """Handler für abgeschlossene SELL-Orders mit PnL-Berechnung"""
        import time
        from loggingx import on_order_filled
        from telegram_notify import tg
        
        # Get pending settlement data
        settlement = self.pending_settlements.get(order_id, {})
        entry_price = float(settlement.get('entry_price', exit_price))
        entry_time = float(settlement.get('entry_time', time.time()))
        reason = settlement.get('reason', '')
        
        # Calculate PnL
        pnl_pct = ((exit_price / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0
        profit_usdt = (amount * (exit_price - entry_price)) - fee
        duration_s = time.time() - entry_time
        
        # Log with full details
        on_order_filled(
            "sell", symbol, order_id, 
            avg=exit_price, filled=amount, 
            remaining=0.0, fee=fee, maker=False,
            order_type="LIMIT", tif="IOC",
            pnl_percentage=pnl_pct,
            profit_usdt=profit_usdt,
            duration_s=duration_s,
            reason=reason,
            **extra
        )
        
        # Send EXIT notification with reason
        try:
            tg.notify_exit(
                symbol, reason, entry_price, exit_price, amount,
                pnl_percentage=pnl_pct, profit_usdt=profit_usdt,
                duration_s=duration_s, order_id=order_id,
                order_type="LIMIT", exit_type="limit_ioc"
            )
        except Exception:
            pass
        
        # Entkopplung via Runtime-Event; Engine-Hook aktualisiert PnL & Telegram
        try:
            from event_bus import emit
            emit(
                "EXIT_FILLED",
                symbol=symbol,
                qty=amount,
                avg_exit_price=exit_price,
                entry_avg_price=entry_price,
                sell_fees_quote=fee,
                buy_fees_alloc_quote=0.0,  # Vereinfacht für utils.py
                reason="exit_utils"
            )
        except Exception:
            pass
        
        # Remove from pending
        self.remove_pending(order_id)
        return profit_usdt

# =================================================================================
# Exchange-Helper-Funktionen (erweitert)
# =================================================================================
def round_to_precision(exchange_obj, symbol, value_type, value):
    """Rundet Werte auf Exchange-Präzision"""
    try:
        market = exchange_obj.market(symbol)
        return exchange_obj.price_to_precision(symbol, value) if value_type == 'price' else exchange_obj.amount_to_precision(symbol, value)
    except Exception:
        return value

def get_market_min_cost(exchange_obj, symbol):
    """Gibt minimale Order-Kosten für Symbol zurück"""
    try:
        # Erst Override checken
        if symbol in symbol_min_cost_override:
            return symbol_min_cost_override[symbol]
        
        market = exchange_obj.market(symbol)
        return market.get('limits', {}).get('cost', {}).get('min', 5.1)
    except Exception:
        return symbol_min_cost_override.get(symbol, 5.1)

def get_symbol_limits(exchange_obj, symbol):
    """Holt alle relevanten Limits für ein Symbol"""
    try:
        market = exchange_obj.market(symbol)
        limits = market.get('limits', {})
        precision = market.get('precision', {})
        
        # Check für Symbol-Override
        min_cost = symbol_min_cost_override.get(symbol, 
                   limits.get('cost', {}).get('min', 5.1))
        
        return {
            'min_cost': min_cost,
            'min_amount': limits.get('amount', {}).get('min', 0),
            'amount_step': precision.get('amount', 8),
            'price_step': precision.get('price', 8)
        }
    except Exception as e:
        logger.warning(f"Could not get limits for {symbol}: {e}",
                      extra={'event_type': 'LIMITS_ERROR', 'symbol': symbol})
        return {
            'min_cost': symbol_min_cost_override.get(symbol, 5.1),
            'min_amount': 0,
            'amount_step': 8,
            'price_step': 8
        }

def floor_to_step(value, step):
    """Rundet einen Wert auf die nächste gültige Schrittgröße ab"""
    if step == 0 or step is None:
        return value
    
    if isinstance(step, int) and step > 0:
        # Dezimalstellen (Präzision)
        factor = 10 ** step
        return math.floor(value * factor) / factor
    elif isinstance(step, float) and step > 0:
        # Echter Step-Wert
        return math.floor(value / step) * step
    else:
        # Fallback: verwende 8 Dezimalstellen
        return math.floor(value * 100000000) / 100000000

def ceil_to_step(value, step):
    """Rundet einen Wert auf die nächste gültige Schrittgröße auf"""
    if step == 0 or step is None:
        return value
    
    if isinstance(step, int) and step > 0:
        # Dezimalstellen (Präzision)
        factor = 10 ** step
        return math.ceil(value * factor) / factor
    elif isinstance(step, float) and step > 0:
        # Echter Step-Wert
        return math.ceil(value / step) * step
    else:
        # Fallback: verwende 8 Dezimalstellen
        return math.ceil(value * 100000000) / 100000000

def log_initial_config(config_vars):
    """Loggt die initiale Bot-Konfiguration"""
    logger.info("Bot-Konfiguration geladen", 
               extra={'event_type': 'CONFIGURATION_LOADED', 'config': config_vars})

def compute_affordable_qty(
    exchange,
    symbol: str,
    available_usdt: float,
    price: float,
    *,
    fee_rate: float,
    slippage_headroom: float,
    min_order_buffer: float,
):
    """
    Liefert (qty, est_cost, min_required, reason_dict_or_None)
    - qty: kaufbare Menge (nach Präzision/Lot gerundet)
    - est_cost: geschätzte Gesamtkosten inkl. Fee+Slippage-Puffer bei 'price'
    - min_required: Börsen-Mininotional * Buffer
    - reason: None, wenn okay; sonst z.B. {'reason': 'BELOW_MIN_NOTIONAL'} usw.
    """
    limits = get_symbol_limits(exchange, symbol)
    min_amount = float(limits.get("min_amount", 0.0) or 0.0)
    min_cost   = float(limits.get("min_cost", 5.1) or 5.1) * float(min_order_buffer)

    price = float(price)
    if price <= 0 or available_usdt <= 0:
        return 0.0, 0.0, min_cost, {"reason": "NO_BUDGET"}

    eff_price = price * (1.0 + float(slippage_headroom))  # Puffer gegen Preisdrift
    fee_mult  = 1.0 + float(fee_rate)

    # Max. Menge, die mit den verfügbaren USDT bezahlbar wäre
    raw_qty = available_usdt / (eff_price * fee_mult)
    qty = float(exchange.amount_to_precision(symbol, raw_qty))

    # Mindestmenge beachten
    if min_amount > 0 and qty < min_amount:
        qty = float(exchange.amount_to_precision(symbol, min_amount))

    # Kosten schätzen (inkl. Fee+Puffer)
    est_cost = qty * eff_price * fee_mult

    # Falls noch zu teuer → iterativ reduzieren
    steps = 0
    while qty > 0 and est_cost > available_usdt and steps < 12:
        qty = float(exchange.amount_to_precision(symbol, qty * 0.98))
        est_cost = qty * eff_price * fee_mult
        steps += 1

    if qty <= 0:
        return 0.0, est_cost, min_cost, {"reason": "INSUFFICIENT_AFTER_ROUNDING"}

    # Min-Notional (mit Buffer) final prüfen
    if est_cost < min_cost:
        return 0.0, est_cost, min_cost, {"reason": "BELOW_MIN_NOTIONAL"}

    return qty, est_cost, min_cost, None

# =================================================================================
# Dust-Sweeper Klasse (NEU)
# =================================================================================
class DustSweeper:
    """Sammelt und verkauft Dust-Assets gebündelt"""
    def __init__(self, exchange, min_sweep_value=5.0):
        self.exchange = exchange
        self.min_sweep_value = min_sweep_value
        self.dust_accumulator = {}  # {symbol: amount}
        self.last_sweep_attempt = {}  # {symbol: timestamp}
    
    def add_dust(self, symbol, amount, price):
        """Fügt Dust zum Accumulator hinzu"""
        value = amount * price
        if value < self.min_sweep_value:  # Unter min_sweep_value = Dust
            if symbol not in self.dust_accumulator:
                self.dust_accumulator[symbol] = 0

            previous_amount = self.dust_accumulator[symbol]
            self.dust_accumulator[symbol] += amount
            total_amount = self.dust_accumulator[symbol]
            total_value = total_amount * price

            logger.info(f"DUST_ACCUMULATED - {symbol}: +{amount} -> total={total_amount} (${total_value:.4f})",
                       extra={'event_type': 'DUST_ACCUMULATED', 'symbol': symbol,
                              'added_amount': amount, 'total_amount': total_amount,
                              'added_value': value, 'total_value': total_value,
                              'price': price, 'min_sweep_value': self.min_sweep_value})
            return True
        return False
    
    def sweep(self, preise):
        """Verkauft angesammelten Dust wenn genug Wert vorhanden"""
        swept_symbols = []
        
        for symbol, amount in list(self.dust_accumulator.items()):
            # Prüfe ob wir kürzlich schon versucht haben
            if symbol in self.last_sweep_attempt:
                if time.time() - self.last_sweep_attempt[symbol] < 300:  # 5 Min Cooldown
                    continue
            
            price = preise.get(symbol, 0)
            if price > 0:
                value = amount * price
                if value >= self.min_sweep_value:
                    try:
                        # Get symbol limits and check if order is valid
                        limits = get_symbol_limits(self.exchange, symbol)
                        
                        # Apply precision to amount
                        precise_amount = floor_to_step(amount, limits['amount_step'])
                        
                        # Check minimum order value
                        min_cost = limits.get('min_cost', 5.1)
                        order_value = precise_amount * price
                        
                        if order_value < min_cost:
                            logger.debug(f"Dust-Sweep für {symbol} unter Min-Cost: {order_value:.2f} < {min_cost}",
                                       extra={'event_type': 'DUST_BELOW_MIN_COST', 
                                             'symbol': symbol, 'value': order_value, 'min_cost': min_cost})
                            continue
                        
                        # Verkaufe den gesammelten Dust mit korrekter Präzision und robusten Fallbacks
                        try:
                            order = self.exchange.create_market_order(symbol, "sell", precise_amount)
                        except Exception as e:
                            err = str(e).lower()
                            
                            # 1) Preis-Pflicht (MEXC 700004) -> Market mit Preis oder Limit IOC
                            if ("mandatory parameter 'price'" in err) or ("700004" in err):
                                ob = self.exchange.fetch_order_book(symbol)
                                exec_price = None
                                if ob and ob.get('bids'):
                                    exec_price = float(ob['bids'][0][0])
                                elif ob and ob.get('asks'):
                                    exec_price = float(ob['asks'][0][0])
                                
                                if exec_price:
                                    exec_price = float(self.exchange.price_to_precision(symbol, exec_price))
                                    logger.warning(f"Dust-Sweep: nutze Preis {exec_price} wegen 700004",
                                                 extra={'event_type': 'DUST_SWEEP_PRICE_FALLBACK', 
                                                       'symbol': symbol, 'price': exec_price})
                                    try:
                                        order = self.exchange.create_market_order(symbol, 'sell', precise_amount)
                                    except Exception:
                                        # Limit IOC als Fallback
                                        order = self.exchange.create_limit_order(symbol, 'sell', precise_amount, exec_price, 'IOC')
                                else:
                                    raise e
                            
                            # 2) Symbol-Route Problem (10007) -> Limit-IOC mit aggressivem Preis
                            elif ("symbol not support" in err) or ("10007" in err):
                                try:
                                    self.exchange.load_markets(True)
                                except Exception:
                                    pass
                                
                                px = None
                                try:
                                    ob = self.exchange.fetch_order_book(symbol)
                                    if ob and ob.get('bids'):
                                        px = float(ob['bids'][0][0])
                                    elif ob and ob.get('asks'):
                                        px = float(ob['asks'][0][0])
                                except Exception:
                                    px = None
                                
                                if px:
                                    px = float(self.exchange.price_to_precision(symbol, px * 0.9995))  # 0.05% unter Bid
                                    logger.warning(f"Dust-Sweep: Limit-IOC bei {px} wegen 10007",
                                                 extra={'event_type': 'DUST_SWEEP_LIMIT_IOC', 
                                                       'symbol': symbol, 'price': px})
                                    try:
                                        order = self.exchange.create_limit_order(symbol, 'sell', precise_amount, px, 'IOC')
                                    except Exception as e2:
                                        # GTC als letzter Fallback
                                        logger.warning(f"Dust-Sweep: IOC fehlgeschlagen, versuche GTC",
                                                     extra={'event_type': 'DUST_SWEEP_LIMIT_GTC', 
                                                           'symbol': symbol, 'price': px})
                                        order = self.exchange.create_limit_order(symbol, 'sell', precise_amount, px)
                                else:
                                    # Letzter Versuch: Market ohne Preis (nur wenn erlaubt)
                                    from config import dust_allow_market_fallback
                                    if dust_allow_market_fallback:
                                        logger.warning(f"Dust-Sweep: Market-Fallback für {symbol}",
                                                     extra={'event_type': 'DUST_SWEEP_MARKET_FALLBACK', 
                                                           'symbol': symbol})
                                        order = self.exchange.create_market_order(symbol, "sell", precise_amount)
                                    else:
                                        logger.error(f"Dust-Sweep: Market-Fallback blockiert (dust_allow_market_fallback=False)",
                                                   extra={'event_type': 'DUST_SWEEP_MARKET_BLOCKED', 
                                                         'symbol': symbol})
                                        raise Exception("Market fallback not allowed for dust sweep")
                            else:
                                raise e
                        
                        # Erweiterte Telemetrie für erfolgreichen Sweep
                        avg_price = order_value / precise_amount if precise_amount > 0 else 0
                        logger.info(f"DUST_SWEEP_EXECUTED - {symbol}: {precise_amount} @ ${avg_price:.6f} = ${order_value:.2f}",
                                   extra={'event_type': 'DUST_SWEEP_EXECUTED', 'symbol': symbol,
                                          'amount': precise_amount, 'cost_usd': order_value,
                                          'avg_price': avg_price, 'order_id': order.get('id', 'unknown'),
                                          'order_status': order.get('status', 'unknown')})
                        swept_symbols.append(symbol)
                    except Exception as e:
                        logger.error(f"Dust-Sweep fehlgeschlagen für {symbol}: {e}",
                                   extra={'event_type': 'DUST_SWEEP_ERROR', 
                                         'symbol': symbol, 'error': str(e)})
                        self.last_sweep_attempt[symbol] = time.time()
        
        # Entferne erfolgreich verkaufte Symbole
        for symbol in swept_symbols:
            del self.dust_accumulator[symbol]
            if symbol in self.last_sweep_attempt:
                del self.last_sweep_attempt[symbol]
        
        return len(swept_symbols)

# =================================================================================
# TRAILING CONTROLLER - Kombiniertes Trailing TP & SL
# =================================================================================
class TrailingController:
    """Kombiniertes Trailing für TP & SL (Worst-Case, BE-Bump, TSL, TTP)"""
    
    def __init__(self, fees_rt: float = FEE_RATE):
        self.state = {}  # position_id -> {entry, highest, sl, tp, tp_steps}
        self.fees_rt = float(fees_rt)

    def on_entry(self, position_id: str, entry: float):
        """Initialisiert Trailing für neue Position"""
        self.state[position_id] = {
            "entry": float(entry), 
            "highest": float(entry), 
            "sl": None, 
            "tp": None, 
            "tp_steps": 0
        }

    def on_tick(self, position_id: str, last: float, *, set_sl_cb, set_tp_cb):
        """Aktualisiert Trailing bei jedem Tick"""
        st = self.state.get(position_id)
        if not st:
            return
            
        e = st["entry"]
        h = st["highest"]
        
        # Update Highest
        if last > h:
            st["highest"] = h = float(last)

        # 1) Worst-Case SL
        floor_sl = e * stop_loss_threshold
        if st["sl"] is None or floor_sl > st["sl"]:
            st["sl"] = floor_sl
            set_sl_cb(floor_sl)

        # 2) Initial TP
        init_tp = e * take_profit_threshold
        if st["tp"] is None or init_tp < st["tp"]:
            st["tp"] = init_tp
            set_tp_cb(init_tp)

        # 3) Breakeven-Bump
        if last >= e * be_activation_pct:
            be_sl = e * (1.0 + self.fees_rt + 0.0002)  # +2 bp
            if be_sl > st["sl"]:
                old_sl = st["sl"]
                st["sl"] = be_sl
                set_sl_cb(be_sl)
                sl_breakeven_bump(position_id, price=be_sl)
                trailing_bump(position_id, old=old_sl, new=be_sl, ctx={"type": "breakeven"})

        # 4) Trailing SL
        if last >= e * trailing_stop_activation_pct:
            trail = max(st["sl"], last * trailing_stop_distance_pct, e * (1.0 + self.fees_rt))
            if trail > st["sl"]:
                old_sl = st["sl"]
                st["sl"] = trail
                set_sl_cb(trail)
                sl_trail_update(position_id, price=trail)
                trailing_bump(position_id, old=old_sl, new=trail, ctx={"type": "trailing_stop"})

        # 5) Trailing TP (Stufen)
        if use_trailing_take_profit and last >= e * trailing_tp_activation_pct:
            run_bp = int(((h / e) - 1.0) * 10000)
            steps  = max(0, run_bp // trailing_tp_step_bp)
            if steps > st["tp_steps"]:
                st["tp_steps"] = steps
                new_tp = h * (1.0 - trailing_tp_under_high_bp/10000.0)
                if new_tp <= st["sl"]:
                    new_tp = st["sl"] * 1.002  # nie ≤ SL
                old_tp = st["tp"]
                st["tp"] = new_tp
                set_tp_cb(new_tp)
                tp_trail_update(position_id, price=new_tp, steps=steps)
                trailing_bump(position_id, old=old_tp, new=new_tp, ctx={"type": "trailing_tp", "step": steps})

    def on_close(self, position_id: str):
        """Bereinigt State nach Position-Close"""
        self.state.pop(position_id, None)

# =================================================================================
# HELPER FUNCTIONS FOR PRICE/SIZE/MIN_NOTIONAL
# =================================================================================
def bps(x: float) -> float:
    return x / 10_000.0

def best_prices_from_ticker(exchange, symbol):
    """Sichere Preisquelle: Ticker, fallback Orderbuch."""
    t = exchange.fetch_ticker(symbol)
    ask = t.get("ask") or t.get("last")
    bid = t.get("bid") or t.get("last")
    if not ask or ask <= 0:
        ob = exchange.fetch_order_book(symbol, limit=5)
        if ob["asks"]:
            ask = ob["asks"][0][0]
    if not bid or bid <= 0:
        ob = exchange.fetch_order_book(symbol, limit=5)
        if ob["bids"]:
            bid = ob["bids"][0][0]
    return float(ask), float(bid)

def compute_min_cost(exchange, market):
    # CCXT: market['limits']['cost']['min'] oder notional rules
    limits = (market.get("limits") or {})
    cost = (limits.get("cost") or {}).get("min")
    if cost: return float(cost)
    notional = (limits.get("notional") or {}).get("min")
    if notional: return float(notional)
    return 0.0

def round_amount(exchange, market, amount):
    import math  # Import immer am Anfang
    
    # Verwende exchange.amount_to_precision für korrekte Quantisierung
    symbol = market.get("symbol")
    if symbol and hasattr(exchange, 'amount_to_precision'):
        a = float(exchange.amount_to_precision(symbol, amount))
    else:
        # Fallback falls exchange.amount_to_precision nicht verfügbar
        prec = market.get("precision", {}).get("amount", 0)
        if isinstance(prec, int):
            a = float(round(amount, prec))
        else:
            # prec ist float (Ticksize) - konvertiere zu Dezimalstellen
            if isinstance(prec, float) and prec > 0:
                ndigits = max(0, int(round(-math.log10(prec))))
            else:
                ndigits = 8
            a = float(round(amount, ndigits))
    
    # Step-basierte Rundung (falls vorhanden)
    step = market.get("limits", {}).get("amount", {}).get("min")
    if step and step > 0:
        a = math.floor(a / step) * step
    return max(a, 0.0)

def size_for_quote(exchange, market, price, quote_budget):
    """Errechne amount so, dass amount*price >= minCost und Präzision passt."""
    min_cost = compute_min_cost(exchange, market)
    amount = quote_budget / price
    amount = round_amount(exchange, market, amount)
    if amount * price < min_cost:
        # hebe minimal auf min_cost an – wenn Budget zu klein, liefere 0 zurück
        target_amt = min_cost / price
        target_amt = round_amount(exchange, market, target_amt)
        if target_amt * price <= quote_budget:
            amount = target_amt
        else:
            return 0.0, min_cost
    return amount, min_cost

def dynamic_wait_seconds(spread_bps: float, default_wait: int):
    if not spread_bps or spread_bps <= 0:
        return default_wait
    wait = 0.5 + 0.2 * spread_bps   # z. B. 5 bps → 1.5 s
    return int(max(2, min(15, wait)))

def check_min_requirements(qty: float, price: float, min_qty: float | None, min_cost: float | None):
    """
    Prüft nach Quantisierung, ob Menge/Wert die Börsen-Minima erfüllen.
    Rückgabe: (ok: bool, reason: str, info: dict)
    reason: "OK" | "MIN_QTY" | "MIN_COST"
    """
    from decimal import Decimal
    
    dqty   = Decimal(str(qty))
    dprice = Decimal(str(price))
    dminq  = Decimal(str(min_qty or 0))
    dminc  = Decimal(str(min_cost or 0))
    cost   = dqty * dprice

    if dminq and dqty < dminq:
        return False, "MIN_QTY", {"qty": float(dqty), "price": float(dprice), "min_qty": float(dminq), "min_cost": float(dminc), "cost": float(cost)}
    if dminc and cost < dminc:
        return False, "MIN_COST", {"qty": float(dqty), "price": float(dprice), "min_qty": float(dminq), "min_cost": float(dminc), "cost": float(cost)}
    return True, "OK", {"qty": float(dqty), "price": float(dprice), "min_qty": float(dminq), "min_cost": float(dminc), "cost": float(cost)}


# =================================================================================
# DYNAMIC TRADING CONFIGURATION
# =================================================================================

def compute_gtc_wait_seconds(spread_bps: float,
                            base: float = 0.5, slope: float = 0.2,
                            lo: float = 2.0, hi: float = 10.0) -> float:
    """
    Berechnet dynamische GTC-Wartezeit basierend auf aktuellem Spread.
    
    Args:
        spread_bps: Aktueller Spread in Basispunkten
        base: Basis-Wartezeit in Sekunden
        slope: Steigung für Spread-Abhängigkeit
        lo: Minimale Wartezeit
        hi: Maximale Wartezeit
    
    Returns:
        Wartezeit in Sekunden
    """
    wait = base + slope * max(0.0, spread_bps)
    return max(lo, min(hi, wait))


def current_regime(atr_pct_30m: float, spread_bps: float) -> str:
    """
    Bestimmt das aktuelle Markt-Regime basierend auf Zeit und Volatilität.
    
    Args:
        atr_pct_30m: 30-Minuten ATR in Prozent
        spread_bps: Aktueller Spread in Basispunkten
    
    Returns:
        "HIGH_VOL", "NIGHT" oder "BASE"
    """
    import pytz
    from datetime import datetime
    
    # Hoch-Volatilitäts-Check
    if spread_bps > 25 or atr_pct_30m > 1.2:
        return "HIGH_VOL"
    
    # Zeit-basierter Check (Europe/Berlin)
    try:
        tz = pytz.timezone("Europe/Berlin")
        h = datetime.now(tz).hour
        if 22 <= h or h < 6:
            return "NIGHT"
    except Exception:
        pass  # Falls pytz nicht verfügbar, ignorieren
    
    return "BASE"


def apply_regime_overrides(regime: str, cfg: dict) -> dict:
    """
    Wendet Regime-spezifische Config-Overrides an.
    
    Args:
        regime: "BASE", "NIGHT" oder "HIGH_VOL"
        cfg: Aktuelle Konfiguration (wird modifiziert)
    
    Returns:
        Modifizierte Konfiguration
    """
    if regime == "NIGHT":
        # Nacht-Override (dünnere Bücher, defensiver)
        cfg.update({
            "DROP_TRIGGER_VALUE": 0.9740,      # konservativer Entry
            "BUY_LIMIT_PREMIUM_BPS": 10,       # mehr Konzession
            "GUARD_MAX_SPREAD_BPS": 15,        # striktere Spread-Limits
            "TAKE_PROFIT_THRESHOLD": 1.0080,   # kleineres TP-Target
            "TRADE_TTL_MIN": 60,                # längerer TTL
        })
        logger.info("Markt-Regime: NACHT - defensive Parameter aktiv",
                   extra={'event_type': 'REGIME_SWITCH', 'regime': 'NIGHT'})
    
    elif regime == "HIGH_VOL":
        # Hoch-Volatilitäts-Override
        cfg.update({
            "DROP_TRIGGER_VALUE": 0.9800,      # sehr konservativ
            "TAKE_PROFIT_THRESHOLD": 1.0120,   # größeres TP-Target
            "STOP_LOSS_THRESHOLD": 0.9960,     # breiterer Stop
            "GUARD_MAX_SPREAD_BPS": 15,        # toleranter bei Spreads
            "BUY_LIMIT_PREMIUM_BPS": 12,       # Fill trotz springender Spreads
        })
        logger.info("Markt-Regime: HOHE VOLATILITÄT - erweiterte Parameter aktiv",
                   extra={'event_type': 'REGIME_SWITCH', 'regime': 'HIGH_VOL'})
    
    return cfg

def would_exit_be_under_min(exchange, symbol: str, amount: float, price: float) -> bool:
    """
    Prüft ob ein Exit (TP/SL) unter dem Minimum-Notional liegen würde
    
    Args:
        exchange: CCXT Exchange-Instanz
        symbol: Trading-Symbol
        amount: Menge in Base-Currency
        price: Preis pro Einheit
        
    Returns:
        True wenn Exit unter Minimum, False sonst
    """
    try:
        market = exchange.market(symbol)
        notional = float(amount) * float(price)
        min_cost = float(market.get("limits", {}).get("cost", {}).get("min") or 0.0)
        return min_cost > 0 and notional < min_cost
    except Exception:
        # Bei Fehler konservativ False zurückgeben (Trade erlauben)
        return False