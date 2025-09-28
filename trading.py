# trading.py - Trading-Funktionen und Order-Management
import time
import math
import ccxt
import re
from typing import Optional, Dict, Any
from config import (
    DUST_FACTOR, MIN_ORDER_VALUE, min_order_buffer,
    SETTLEMENT_TIMEOUT, SETTLEMENT_TOLERANCE,
    SAFETY_BUFFER_PCT, SAFETY_BUFFER_MIN,
    ioc_price_buffers_bps, ioc_retry_sleep_s,
    post_only_rest_ttl_s, post_only_undershoot_bps,
    exit_ladder_bps, exit_ioc_ttl_ms, exit_ladder_sleep_ms, NEVER_MARKET_SELLS,
    RUN_ID
)
from logger_setup import logger
from loggingx import log_event, on_order_placed, on_order_filled, on_order_replaced, on_exit_executed
from utils import save_trade_history, get_symbol_limits, floor_to_step

# =================================================================================
# ClientOrderId Sanitizer
# =================================================================================
def _sanitize_coid(coid: str) -> str:
    """Stellt sicher dass clientOrderId MEXC-konform ist (max 32 Zeichen, nur [0-9a-zA-Z_-])"""
    coid = re.sub(r'[^0-9A-Za-z_-]', '', str(coid))
    return coid[:32]

# =================================================================================
# Order-Funktionen mit clientOrderId Support
# =================================================================================
def place_limit_buy_with_coid(exchange, symbol, amount, price, coid):
    """Limit Buy Order mit clientOrderId"""
    coid = _sanitize_coid(coid)
    return exchange.create_order(symbol, "limit", "buy", amount, price, {"clientOrderId": coid})

def place_limit_ioc_buy_with_coid(exchange, symbol, amount, price, coid):
    """Limit IOC Buy Order mit clientOrderId"""
    coid = _sanitize_coid(coid)
    return exchange.create_order(symbol, "limit", "buy", amount, price, {"timeInForce": "IOC", "clientOrderId": coid})

# LEGACY - NICHT MEHR VERWENDET (nur für historische Kompatibilität behalten)
# def place_market_ioc_buy_quote_with_coid(exchange, symbol, quote_amount, coid):
#     """Market Buy per Quote-Amount - OBSOLET"""
#     return exchange.create_order(
#         symbol, "market", "buy", None, None,
#         {"quoteOrderQty": float(quote_amount), "clientOrderId": coid}
#     )

def place_limit_ioc_sell_with_coid(exchange, symbol, amount, price, coid):
    """Limit IOC Sell Order mit clientOrderId"""
    coid = _sanitize_coid(coid)
    try:
        return exchange.create_order(symbol, "limit", "sell", amount, price, {"timeInForce": "IOC", "clientOrderId": coid})
    except ccxt.BaseError as e:
        logger.debug(f"LIMIT_IOC_SELL_REJECT {symbol}: {e}", 
                    extra={'event_type':'LIMIT_IOC_SELL_REJECT','symbol':symbol,'error':str(e)})
        raise

def place_market_ioc_sell_with_coid(exchange, symbol, amount, coid, held_assets=None, preise=None):
    """Market IOC Sell Order mit clientOrderId und safe amount calculation"""
    coid = _sanitize_coid(coid)
    
    # Use safe amount calculation if held_assets and preise are provided
    safe_amount = amount
    if held_assets is not None and preise is not None:
        safe_amount = compute_safe_sell_amount(exchange, symbol, amount, held_assets, preise, consider_order_remaining=True)
        if safe_amount <= 0:
            logger.debug(f"MARKET_IOC_SELL_NO_SAFE_AMOUNT {symbol}: no sellable amount available",
                        extra={'event_type': 'MARKET_IOC_SELL_NO_SAFE_AMOUNT', 'symbol': symbol, 
                              'desired_amount': amount, 'safe_amount': safe_amount})
            return None
        if safe_amount != amount:
            logger.debug(f"MARKET_IOC_SELL_AMOUNT_ADJUSTED {symbol}: {amount} -> {safe_amount}",
                        extra={'event_type': 'MARKET_IOC_SELL_AMOUNT_ADJUSTED', 'symbol': symbol, 
                              'original_amount': amount, 'safe_amount': safe_amount})
    
    try:
        return exchange.create_order(symbol, "market", "sell", safe_amount, None, {"timeInForce": "IOC", "clientOrderId": coid})
    except ccxt.BaseError as e:
        logger.debug(f"MARKET_IOC_SELL_REJECT {symbol}: {e}", 
                    extra={'event_type':'MARKET_IOC_SELL_REJECT','symbol':symbol,'error':str(e)})
        raise

# =================================================================================
# Helper-Funktionen für Trading
# =================================================================================
def compute_min_cost(exchange, symbol: str) -> float:
    """Berechnet Minimum-Cost mit Buffer"""
    m = exchange.markets.get(symbol, {})
    limits = m.get('limits', {}) or {}
    cost_limits = limits.get('cost', {}) or {}
    raw_min = cost_limits.get('min') or 5.0
    return float(raw_min) * float(min_order_buffer)

def size_limit_sell(exchange, symbol: str, price: float, max_amount: float) -> float:
    """Ermittelt die Mindestmenge für Sell, damit quote_value >= minCost*buffer liegt."""
    min_cost = compute_min_cost(exchange, symbol)
    min_amt = min_cost / float(price) if price > 0 else 0.0
    # auf Marktspezifikation runden
    amt = _amount_to_precision(exchange, symbol, max(min_amt, 0.0))
    # nur gültige Mengen >= min_amt zurückgeben, sonst 0
    return amt if max_amount >= amt and amt > 0 else 0.0

def size_limit_buy(exchange, symbol: str, price: float, max_quote: float) -> float:
    """
    max_quote = verfügbares USDT-Budget.
    Stellt sicher, dass nach amount_to_precision der Orderwert >= minCost liegt.
    """
    min_cost = compute_min_cost(exchange, symbol)
    if max_quote < min_cost:
        return 0.0

    amt = max_quote / float(price)
    amt = _amount_to_precision(exchange, symbol, amt)

    # Nach-Rundung prüfen
    val = amt * float(price)
    if val + 1e-12 < min_cost:
        # minimal anheben, wieder präzisieren
        delta = (min_cost - val) / float(price)
        amt = _amount_to_precision(exchange, symbol, amt + delta * 1.05)
    return amt

# LEGACY - NICHT MEHR VERWENDET
# def create_market_buy_by_quote(exchange, symbol, quote_usdt: float, params=None):
#     """Market-Kauf per Quote - OBSOLET"""
#     from loggingx import log_mexc_order
#     from utils import next_client_order_id
#     params = params or {}
#     params = {**params, 'quoteOrderQty': float(quote_usdt)}
#     params.setdefault('clientOrderId', next_client_order_id(symbol, side="BUY"))
#     order = exchange.create_order(symbol, 'market', 'buy', None, None, params)
#     
#     # Log MEXC order
#     try:
#         log_mexc_order(order)
#     except Exception:
#         pass
#     
#     return order

def _base_currency(symbol: str) -> str:
    """Extrahiert Basis-Währung aus Symbol"""
    try:
        return symbol.split('/')[0]
    except Exception:
        return symbol

def _amount_to_precision(exchange, symbol, amount):
    """Konvertiert Amount zu Exchange-Präzision"""
    try:
        return float(exchange.amount_to_precision(symbol, amount))
    except Exception:
        try:
            return float(f"{amount:.8f}")
        except Exception:
            return amount

def _price_to_precision(exchange, symbol, price):
    """Konvertiert Preis zu Exchange-Präzision"""
    try:
        return float(exchange.price_to_precision(symbol, price))
    except Exception:
        try:
            return float(f"{price:.8f}")
        except Exception:
            return price

def _get_free(exchange, currency: str) -> float:
    """Holt freien Bestand einer Währung"""
    try:
        from utils import with_backoff
        bal = with_backoff(exchange.fetch_balance)
        # Prefer standardized structure
        free_map = bal.get("free", {})
        if isinstance(free_map, dict) and currency in free_map:
            return float(free_map.get(currency) or 0.0)
        # Fallback to raw "info" structure
        info = bal.get("info", {})
        balances = info.get("balances", [])
        for b in balances:
            if b.get("asset") == currency:
                return float(b.get("free", 0.0) or 0.0)
    except Exception:
        pass
    return 0.0

# =================================================================================
# Depth Sweep Helpers (synthetic-market via Limit IOC)
# =================================================================================
def _fetch_order_book_depth(exchange, symbol: str, limit: int = 20):
    """Liest Orderbuch bis 'limit' Levels; return (bids, asks) als [[price, qty], ...]."""
    try:
        ob = exchange.fetch_order_book(symbol, limit=limit)
        return ob.get("bids") or [], ob.get("asks") or []
    except Exception:
        return [], []

def _cumulative_level_price(levels, target_qty: float):
    """Akkumuliert Tiefe, bis target_qty gedeckt ist. Liefert (limit_price, cum_qty)."""
    cum = 0.0
    limit_px = None
    for px, q in levels:
        if px is None or q is None:
            continue
        cum += float(q)
        limit_px = float(px)
        if cum >= float(target_qty):
            break
    return limit_px, cum

def _bps(a: float, b: float) -> float:
    """Berechnet Basispunkte Differenz"""
    if a is None or b is None or b == 0:
        return float("inf")
    return (abs(a - b) / b) * 10000.0

def compute_sweep_limit_price(exchange, symbol: str, side: str, target_qty: float,
                              max_slippage_bps: float, levels: int = 20):
    """
    Berechnet ein Limit, das genug Tiefe 'kreuzt', um target_qty zu füllen.
    BUY: asks aufsummieren; SELL: bids aufsummieren.
    Slippage wird ggü. Best-Ask/Bid gedeckelt.
    """
    side_up = str(side).upper()
    bids, asks = _fetch_order_book_depth(exchange, symbol, limit=levels)
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None

    if side_up == "BUY":
        limit_px, cum_qty = _cumulative_level_price(asks, target_qty)
        ref = best_ask
        if limit_px is None:
            return None
        max_px = ref * (1.0 + max_slippage_bps / 10000.0) if ref else limit_px
        px = min(limit_px, max_px)
        had_enough = cum_qty >= target_qty
    else:
        limit_px, cum_qty = _cumulative_level_price(bids, target_qty)
        ref = best_bid
        if limit_px is None:
            return None
        min_px = ref * (1.0 - max_slippage_bps / 10000.0) if ref else limit_px
        px = max(limit_px, min_px)
        had_enough = cum_qty >= target_qty

    px = _price_to_precision(exchange, symbol, px)
    return {
        "price": float(px),
        "best_ref": float(ref) if ref else None,
        "slippage_bps": _bps(px, ref) if ref else None,
        "cum_qty": float(cum_qty),
        "had_enough_depth": bool(had_enough),
    }

def poll_order_canceled(exchange, symbol, order_id, timeout_s=6.0):
    """Wartet auf Bestätigung der Order-Stornierung"""
    from loggingx import log_mexc_update
    from utils import with_backoff
    t0 = time.time()
    while time.time() - t0 <= timeout_s:
        try:
            st = with_backoff(exchange.fetch_order, order_id, symbol)
            # Log MEXC update
            try:
                log_mexc_update(order_id, st or {})
            except:
                pass
            if st and st.get("status") in ("canceled", "closed"):
                return st
        except (ccxt.OrderNotFound, ccxt.BadRequest):
            # Consider it gone
            return None
        time.sleep(0.4)
    return None

# =================================================================================
# Präzise Limit-Buy Funktion (NEU)
# =================================================================================
# V6 BUY LOGIC - LIMIT BEI LAST_PRICE * FACTOR
def place_precise_limit_buy(exchange: ccxt.Exchange, symbol: str, quote_usdt: float, limit_price: float) -> Optional[Dict[str, Any]]:
    """
    V6 Buy-Logic: Platziert eine Limit-Buy-Order bei einem spezifizierten Preis.
    Wird von der Engine mit limit_price = last_price * predictive_buy_zone_pct aufgerufen.
    """
    try:
        # Preis auf Exchange-Präzision
        px = _price_to_precision(exchange, symbol, float(limit_price))
        if px <= 0:
            logger.error(f"Invalid limit price for {symbol}: {limit_price}",
                         extra={'event_type':'BUY_LIMIT_INVALID_PRICE','symbol':symbol,'price':limit_price})
            return None

        # Limits & Steps
        limits = get_symbol_limits(exchange, symbol)
        min_cost = float(limits.get('min_cost', 5.1)) * float(min_order_buffer)
        min_amt  = float(limits.get('min_amount', 0.0))
        amt_step = float(limits.get('amount_step', 0))  # Kann digits oder step sein, floor_to_step behandelt beides

        # Amount aus Quote berechnen (und nach unten quantisieren)
        raw_amt = quote_usdt / px
        amt = floor_to_step(raw_amt, amt_step)

        # Min-Checks: amount & cost
        order_val = amt * px
        if amt < min_amt:
            # auf mindestens min_amt hochrunden (quantisiert)
            amt = floor_to_step(max(min_amt, amt), amt_step)
            order_val = amt * px

        if order_val < min_cost:
            # Versuche, die Amount minimal zu erhöhen, solange budgetbasiert sinnvoll
            need_val = min_cost - order_val
            if need_val > 0:
                delta_amt = need_val / px
                amt = floor_to_step(amt + delta_amt, amt_step)
                order_val = amt * px
                
                # Check if we exceed budget after increasing amount
                if order_val > quote_usdt:
                    logger.info(f"BUY_SKIP_BUDGET {symbol}: need={order_val:.2f} > budget={quote_usdt:.2f}",
                                extra={'event_type':'BUY_SKIP_BUDGET','symbol':symbol,
                                       'need': order_val, 'budget': quote_usdt})
                    return None

        # final auf Exchange-Präzision
        amt = _amount_to_precision(exchange, symbol, amt)

        # finale Sicherheitsprüfung
        if amt <= 0 or (amt * px) < min_cost:
            logger.warning(f"BUY_SKIP_UNDER_MIN {symbol}: amt={amt}, px={px}, val={amt*px:.4f} < min_cost={min_cost:.4f}",
                           extra={'event_type':'BUY_SKIP_UNDER_MIN','symbol':symbol,'amount':amt,'price':px,'min_cost':min_cost})
            return None

        # Order platzieren
        from utils import next_client_order_id
        client_order_id = next_client_order_id(symbol, side="BUY")
        client_order_id = _sanitize_coid(client_order_id)  # Doppelte Absicherung
        order = exchange.create_order(symbol, 'limit', 'buy', amt, px, {'clientOrderId': client_order_id})
        logger.info(f"BUY_LIMIT_PLACED {symbol}: limit={px:.8f}, amt={amt:.8f}, val={amt*px:.4f}",
                    extra={'event_type':'BUY_LIMIT_PLACED','symbol':symbol,'price':px,'amount':amt,'order_value':amt*px,'client_order_id':client_order_id})
        return order

    except Exception as e:
        msg = str(e)
        logger.error(f"BUY_LIMIT_ERROR {symbol}: {msg}",
                     extra={'event_type':'BUY_LIMIT_ERROR','symbol':symbol,'error':msg})
        return None

# =================================================================================
# Haupt-Trading-Funktionen
# =================================================================================
def sync_active_order_and_state(exchange, symbol, held_assets, my_budget, settlement_manager):
    """Update state from currently active order (TP/SL) to reflect partial/filled fills.
       Returns (status, order_dict) where status is one of None/'filled'/'partial'/'open'.
       Note: my_budget parameter is kept for compatibility but not modified internally.
    """
    data = held_assets.get(symbol)
    if not data:
        return None, None
    order_id = data.get("active_order_id")
    if not order_id:
        return None, None
    try:
        st = exchange.fetch_order(order_id, symbol)
        # Log MEXC order update
        try:
            from loggingx import log_mexc_update
            log_mexc_update(order_id, st)
        except Exception:
            pass
    except Exception:
        return None, None

    status = st.get("status")
    filled = float(st.get("filled", 0) or 0.0)
    avg = float(st.get("average", 0) or 0.0)
    remaining = float(st.get("remaining", 0) or 0.0)

    if status in ("closed", "filled"):
        revenue = filled * avg
        if revenue > 0:
            # Only track in settlement manager, don't modify budget locally
            if settlement_manager:
                settlement_manager.add_pending(order_id, revenue, symbol)
        try:
            if data.get("buying_price"):
                perf = ((avg / data["buying_price"]) - 1) * 100
        except Exception:
            perf = None
        # Markiere Asset als vollständig verkauft
        try:
            held_assets[symbol]["amount"] = 0.0
        except Exception:
            pass
        logger.info(f"ORDER FILLED (sync) {symbol}: revenue={revenue:.6f} USDT", 
                   extra={"event_type":"ORDER_FILLED_LIVE","symbol":symbol,"revenue_usdt":revenue,"performance_pct":perf,
                          "side":"sell","amount":filled,"price":avg,"order_id":order_id})
        try:
            save_trade_history({
                "coin": symbol, 
                "buying_price": data.get("buying_price", avg), 
                "selling_price": avg, 
                "Revenue": perf,
                "quantity": filled,
                "exchange_order_id": order_id,
                "trigger_reason": "sync_filled",
                "revenue_usdt": revenue,
                "side": "sell"
            })
        except Exception:
            pass
        return "filled", st

    if filled > 0 and status == "open":
        revenue = filled * avg
        if revenue > 0:
            # Only track in settlement manager, don't modify budget locally
            if settlement_manager:
                settlement_manager.add_pending(f"{order_id}_partial_{time.time()}", revenue, symbol)
        
        # Validierung des tatsächlichen Bestands nach Teilfüllung
        actual_balance = _get_free(exchange, _base_currency(symbol))
        if abs(remaining - actual_balance) > 0.0001:
            logger.warning(f"Diskrepanz zwischen erwartetem ({remaining}) und tatsächlichem ({actual_balance}) Bestand für {symbol}", 
                         extra={"event_type":"BALANCE_DISCREPANCY","symbol":symbol,"expected":remaining,"actual":actual_balance})
            remaining = min(remaining, actual_balance)
        
        held_assets[symbol]["amount"] = remaining
        logger.warning(f"PARTIAL FILL (sync) {symbol}: remaining={remaining}", 
                      extra={"event_type":"PARTIAL_FILL_SYNC","symbol":symbol,"remaining_amount":remaining})
        
        perf = None
        try:
            if data.get("buying_price"):
                perf = ((avg / data["buying_price"]) - 1) * 100
        except Exception:
            perf = None
            
        try:
            save_trade_history({
                "coin": symbol, 
                "buying_price": data.get("buying_price", avg), 
                "selling_price": avg, 
                "Revenue": perf,
                "quantity": filled,
                "exchange_order_id": order_id,
                "trigger_reason": "partial_fill_sync",
                "revenue_usdt": revenue,
                "side": "sell"
            })
        except Exception:
            pass
        return "partial", st

    return status, st

def compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise, 
                            consider_order_remaining=True):
    """Compute safe sellable amount with proper minimum checks"""
    
    remaining = float(desired_amount or 0.0)
    
    # Get current order remaining if needed
    if consider_order_remaining:
        data = held_assets.get(symbol, {})
        oid = data.get("active_order_id")
        if oid:
            try:
                st = exchange.fetch_order(oid, symbol)
                remaining = min(remaining, float(st.get("remaining", remaining) or remaining))
            except Exception:
                pass
    
    # Get actual free balance
    free_base = _get_free(exchange, _base_currency(symbol))
    amt = min(remaining, free_base) * DUST_FACTOR
    
    # Get symbol limits
    limits = get_symbol_limits(exchange, symbol)
    
    # Round to step
    precise_amt = floor_to_step(amt, limits['amount_step'])
    
    # Check minimum order value
    current_price = preise.get(symbol, 0)
    
    # Fallback to ticker bid if no price in cache
    if current_price <= 0:
        try:
            t = exchange.fetch_ticker(symbol)
            current_price = float(t.get("bid") or t.get("last") or 0.0)
        except Exception:
            current_price = 0.0
    
    if current_price > 0:
        order_value = precise_amt * current_price
        min_required = limits['min_cost'] * min_order_buffer  # Mit Buffer
        
        if order_value < min_required:
            logger.debug(f"Sell amount für {symbol} unter Minimum: {order_value:.2f} < {min_required:.2f}",
                        extra={"event_type": "SELL_BELOW_MINIMUM", 
                              "symbol": symbol, 
                              "value": order_value,
                              "min_required": min_required})
            return 0  # Cannot sell
    
    return precise_amt

def place_safe_market_sell(exchange, symbol, desired_amount, held_assets, preise, active_order_id=None):
    """Cancel existing order (optional), poll, then market sell only what is actually sellable."""
    if active_order_id:
        try:
            exchange.cancel_order(active_order_id, symbol)
        except Exception:
            pass
        poll_order_canceled(exchange, symbol, active_order_id, 6.0)

    # One sync pass to reflect partial/filled
    try:
        sync_active_order_and_state(exchange, symbol, held_assets, 0, None)
    except Exception:
        pass

    amt = compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise, consider_order_remaining=True)
    if amt <= 0:
        # Graceful handling: might be already filled or funds not yet released
        for _ in range(8):
            time.sleep(0.4)
            try:
                sync_active_order_and_state(exchange, symbol, held_assets, 0, None)
            except Exception:
                pass
            amt = compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise, consider_order_remaining=True)
            if amt > 0:
                break
        if amt <= 0:
            try:
                logger.debug(f"SAFE_SELL_NO_AMOUNT {symbol}: kein frei veräußerbarer Bestand. Überspringe.", 
                            extra={"event_type":"SAFE_SELL_NO_AMOUNT","symbol":symbol})
            except Exception:
                pass
            return None

    try:
        from loggingx import log_mexc_order
        order = exchange.create_market_sell_order(symbol, amt)
        try:
            log_mexc_order(order)
        except Exception:
            pass
        return order
    except Exception as e:
        # Retry once after short wait with a fresh balance check
        logger.warning(f"SAFE_SELL_RETRY {symbol}: first attempt failed: {e}", 
                      extra={"event_type":"SAFE_SELL_RETRY","symbol":symbol})
        time.sleep(0.6)
        amt = compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise, consider_order_remaining=False)
        if amt <= 0:
            return None
        order = exchange.create_market_sell_order(symbol, amt)
        try:
            from loggingx import log_mexc_order
            log_mexc_order(order)
        except Exception:
            pass
        return order

def safe_create_limit_sell_order(exchange, symbol, desired_amount, price, held_assets, preise, active_order_id=None, extra_params=None):
    """Cancel (optional) with polling, then place a limit sell using balance-aware amount & precision."""
    if active_order_id:
        try:
            exchange.cancel_order(active_order_id, symbol)
        except Exception:
            pass
        poll_order_canceled(exchange, symbol, active_order_id, 6.0)

    # Sync to capture potential partial fills before placing the new order
    try:
        sync_active_order_and_state(exchange, symbol, held_assets, 0, None)
    except Exception:
        pass

    amt = compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise, consider_order_remaining=True)
    if amt <= 0:
        logger.debug(f"Cannot create limit sell for {symbol} - amount too small",
                    extra={"event_type": "LIMIT_SELL_AMOUNT_TOO_SMALL", "symbol": symbol})
        return None
        
    px = _price_to_precision(exchange, symbol, price)
    from utils import next_client_order_id
    params = extra_params or {}
    params.setdefault('clientOrderId', next_client_order_id(symbol, side="SELL"))
    
    try:
        order = exchange.create_order(symbol, 'limit', 'sell', amt, px, params)
        
        # Log MEXC order
        try:
            from loggingx import log_mexc_order
            log_mexc_order(order)
        except Exception:
            pass
        
        return order
    except ccxt.BaseError as e:
        logger.debug(f"LIMIT_SELL_REJECT {symbol}: {e}", 
                    extra={'event_type':'LIMIT_SELL_REJECT','symbol':symbol,'error':str(e)})
        return None

def place_limit_ioc_sell(exchange, symbol, amount, reference_price, held_assets, preise,
                         price_buffer_pct=0.10, active_order_id=None, tif="IOC"):
    """
    Versucht nacheinander IOC-Limits mit steigendem Abschlag unter Best-Bid,
    z.B. 0.10% → 0.25% → 0.50%. Falls alles nicht füllt, finaler Market (mit Preis).
    """
    # Best-Bid mit neuer Funktion bestimmen
    best_bid, _ = fetch_top_of_book(exchange, symbol, depth=5)
    if not best_bid:
        best_bid = reference_price

    # Mehrere Versuche mit steigendem Buffer (in Prozent)
    attempts = [price_buffer_pct, price_buffer_pct * 2.5, price_buffer_pct * 5.0]  # in %
    
    for buf_pct in attempts:
        px = best_bid * (1.0 - buf_pct / 100.0)
        px = _price_to_precision(exchange, symbol, px)
        
        if px <= 0:
            continue
            
        try:
            order = safe_create_limit_sell_order(
                exchange, symbol, amount, px, held_assets, preise,
                active_order_id=active_order_id,
                extra_params={'timeInForce': tif}
            )
            if order:
                if str(order.get('status','')).lower() in ('filled','closed') or float(order.get('filled') or 0.0) > 0.0:
                    return order  # Erfolg
                # Update active_order_id für nächsten Versuch
                active_order_id = order.get('id')
        except Exception as e:
            logger.debug(f"IOC attempt failed for {symbol}: {e}",
                        extra={'event_type':'IOC_ATTEMPT_FAIL','symbol':symbol,'buffer_pct':buf_pct})
            pass  # nächster Versuch
    
    # Final: Market mit explizitem Preis (einige Routen verlangen 'price')
    try:
        from utils import next_client_order_id
        px = _price_to_precision(exchange, symbol, best_bid)
        client_order_id = next_client_order_id(symbol, side="SELL")
        return exchange.create_order(symbol, 'market', 'sell', amount, px, {'clientOrderId': client_order_id})
    except Exception as e:
        logger.error(f"FINAL_SELL_FALLBACK_FAILED {symbol}: {e}",
                     extra={'event_type':'FINAL_SELL_FALLBACK_FAILED','symbol':symbol,'error':str(e)})
        return None

def refresh_budget_from_exchange(exchange, my_budget, max_retries=3, delay=2.0):
    """Holt das aktuelle USDT-Budget von der Exchange mit Retry-Logik"""
    for attempt in range(int(max_retries)):
        try:
            usdt_free = _get_free(exchange, "USDT")  # Nutze die robuste Helper-Funktion
            old_budget = my_budget
            my_budget = usdt_free
            if abs(old_budget - my_budget) > 0.01:  # Nur loggen bei signifikanter Änderung
                logger.info(f"Budget von Exchange aktualisiert: {old_budget:.2f} -> {my_budget:.2f} USDT", 
                          extra={'event_type': 'BUDGET_REFRESHED', 'old': old_budget, 'new': my_budget})
            
            # Speichere Zeit des letzten Erfolgs
            refresh_budget_from_exchange.last_success = time.time()
            return my_budget
        except Exception as e:
            if attempt < max_retries - 1:
                error_context = {
                    'attempt': attempt + 1,
                    'max_retries': max_retries,
                    'delay': delay,
                    'last_known_budget': my_budget,
                    'error_type': type(e).__name__
                }
                logger.warning(f"Budget-Refresh fehlgeschlagen (Versuch {attempt+1}/{max_retries}), warte {delay}s...", 
                             extra={'event_type': 'BUDGET_REFRESH_RETRY', **error_context})
                time.sleep(delay)
            else:
                # Detailliertes Logging beim finalen Fehler
                error_context = {
                    'attempts_made': max_retries,
                    'last_known_budget': my_budget,
                    'time_since_last_success': time.time() - getattr(refresh_budget_from_exchange, 'last_success', 0),
                    'error_details': str(e),
                    'error_type': type(e).__name__
                }
                
                # Prüfe auf spezifische Netzwerk-Fehler
                if 'timeout' in str(e).lower():
                    error_context['likely_cause'] = 'Exchange-Timeout'
                elif 'connection' in str(e).lower():
                    error_context['likely_cause'] = 'Verbindungsproblem'
                elif 'rate' in str(e).lower():
                    error_context['likely_cause'] = 'Rate-Limiting'
                
                logger.error(f"Budget-Refresh endgültig fehlgeschlagen nach {max_retries} Versuchen", 
                           extra={'event_type': 'BUDGET_REFRESH_FAILED', **error_context})
    
    return my_budget

def wait_for_balance_settlement(my_budget, expected_increase, settlement_manager, refresh_func, max_wait=15, check_interval=1.0):
    """Wartet bis eine erwartete Gutschrift auf der Exchange angekommen ist"""
    initial_budget = my_budget
    target_budget = initial_budget + (expected_increase * 0.98)  # 2% Toleranz für Fees
    
    logger.info(f"Warte auf Gutschrift von ~{expected_increase:.2f} USDT (Ziel: {target_budget:.2f})", 
              extra={'event_type': 'WAITING_FOR_SETTLEMENT', 'expected': expected_increase})
    
    # Nutze Settlement-Manager-Statistiken für bessere Wartezeit
    avg_settlement_time = settlement_manager.get_average_settlement_time()
    adjusted_max_wait = max(max_wait, avg_settlement_time * 2)  # Mindestens 2x durchschnittliche Zeit
    
    for i in range(int(adjusted_max_wait / check_interval)):
        time.sleep(check_interval)
        current = refresh_func()
        
        # Update Settlement Manager
        settlement_manager.update_verified_budget(current)
        
        if current >= target_budget:
            logger.info(f"Gutschrift bestätigt nach {(i+1)*check_interval:.1f}s! Budget: {current:.2f} USDT", 
                      extra={'event_type': 'SETTLEMENT_CONFIRMED', 'budget': current, 
                            'duration': (i+1)*check_interval})
            return True
        
        # Zusätzlicher Check: Wenn nach 5 Sekunden noch nichts da ist, warne
        if i == 5 and current < initial_budget + (expected_increase * 0.1):
            logger.warning(f"Settlement verzögert sich. Erwartet: {expected_increase:.2f}, bisher: {current-initial_budget:.2f}", 
                         extra={'event_type': 'SETTLEMENT_DELAYED', 'expected': expected_increase, 
                               'received': current-initial_budget})
    
    # Finale Warnung mit Details
    final_budget = refresh_func()
    received = final_budget - initial_budget
    logger.warning(f"Gutschrift nicht vollständig in {adjusted_max_wait}s bestätigt. Erwartet: {expected_increase:.2f}, Erhalten: {received:.2f}, Budget: {final_budget:.2f}", 
                 extra={'event_type': 'SETTLEMENT_INCOMPLETE', 'expected': expected_increase, 
                       'received': received, 'budget': final_budget})
    return False

def cleanup_stale_orders(exchange, held_assets, open_buy_orders, max_age_minutes=60):
    """Bereinigt alte offene Orders die Kapital blockieren - mit Crash-Recovery"""
    from loggingx import log_mexc_update, log_mexc_cancel
    try:
        from config import take_profit_threshold, stop_loss_threshold
        cleaned_count = 0
        reattached_count = 0
        
        # MEXC benötigt ein Symbol für fetchOpenOrders - iteriere über alle gehaltenen Assets und offenen Orders
        symbols_to_check = set()
        
        # Füge Symbole von gehaltenen Assets hinzu
        for symbol in held_assets.keys():
            symbols_to_check.add(symbol)
        
        # Füge Symbole von offenen Buy-Orders hinzu
        for symbol in open_buy_orders.keys():
            symbols_to_check.add(symbol)
            
        # Prüfe Orders für jedes Symbol
        for symbol in symbols_to_check:
            try:
                open_orders = exchange.fetch_open_orders(symbol)
                for order in open_orders:
                    if order['timestamp']:
                        age_minutes = (time.time() - order['timestamp']/1000) / 60
                        
                        # Überprüfe ob diese Order in unseren aktiven Orders ist
                        is_active = False
                        for asset_symbol, asset_data in held_assets.items():
                            if asset_data.get('active_order_id') == order['id']:
                                is_active = True
                                break
                        for buy_symbol, buy_data in open_buy_orders.items():
                            if buy_data.get('order_id') == order['id']:
                                is_active = True
                                break
                        
                        # Crash-Recovery: Reattach verloren gegangene TP/SL Orders
                        if not is_active and symbol in held_assets:
                            px = float(order.get('price') or 0.0)
                            bp = float(held_assets[symbol].get('buying_price') or 0.0)
                            if px and bp:
                                # Check ob es eine TP oder SL Order ist (mit 2% Toleranz)
                                tp_ok = px >= bp * take_profit_threshold * 0.98
                                sl_ok = px <= bp * stop_loss_threshold * 1.02
                                
                                if tp_ok or sl_ok:
                                    # Reattach die Order zum State
                                    held_assets[symbol]['active_order_id'] = order['id']
                                    held_assets[symbol]['active_order_type'] = 'TAKE_PROFIT' if tp_ok else 'STOP_LOSS'
                                    if sl_ok:
                                        held_assets[symbol]['stop_loss_price'] = px
                                    is_active = True
                                    reattached_count += 1
                                    logger.info(f"Reattached active order {order['id']} to {symbol} (crash-recovery)",
                                              extra={'event_type':'ORDER_REATTACHED','symbol':symbol,
                                                    'order_id':order['id'],'type':'TP' if tp_ok else 'SL'})
                        
                        # Nur canceln, wenn weiterhin nicht aktiv UND alt
                        if not is_active and age_minutes > max_age_minutes and order['status'] == 'open':
                            try:
                                exchange.cancel_order(order['id'], symbol)
                                cleaned_count += 1
                                # Log MEXC cancel
                                try:
                                    log_mexc_cancel(order['id'], symbol, reason="stale_cleanup")
                                except:
                                    pass
                                logger.info(f"Alte Order storniert: {symbol} - {order['id']}", 
                                          extra={'event_type': 'STALE_ORDER_CLEANED', 'symbol': symbol, 
                                                'order_id': order['id'], 'age_minutes': age_minutes})
                            except Exception as ce:
                                logger.debug(f"Cancel stale order failed {symbol}: {ce}",
                                           extra={'event_type':'STALE_ORDER_CANCEL_FAIL','symbol':symbol})
                                
            except Exception as e:
                logger.debug(f"Konnte offene Orders für {symbol} nicht abrufen: {e}", 
                           extra={'event_type': 'FETCH_OPEN_ORDERS_ERROR', 'symbol': symbol, 'error': str(e)})
                
        if cleaned_count > 0 or reattached_count > 0:
            logger.info(f"{cleaned_count} alte Orders bereinigt, {reattached_count} Orders reattached", 
                       extra={'event_type': 'STALE_ORDERS_RESULT', 'cleaned': cleaned_count, 'reattached': reattached_count})
    except Exception as e:
        logger.error(f"Fehler bei der Bereinigung alter Orders: {e}", 
                    extra={'event_type': 'CLEANUP_ERROR', 'error': str(e)})

def full_portfolio_reset(exchange, settlement_manager):
    """
    Verkauft beim Start alle Assets außer USDT zum Marktpreis und wartet auf das Settlement.
    Diese Funktion dient dazu, mit einem sauberen Zustand zu starten.
    Returns: True bei Erfolg (auch wenn keine Assets zu verkaufen waren), False bei Fehler
    """
    from loggingx import log_mexc_cancel
    # Note: PORTFOLIO_RESET_START already logged in portfolio.py - no duplicate needed
    
    try:
        markets = exchange.load_markets()
        balance = exchange.fetch_balance()
    except Exception as e:
        logger.error(f"Konnte Markets oder Balance nicht abrufen: {e}", extra={'event_type': 'PORTFOLIO_RESET_FETCH_FAIL'})
        return False

    totals = balance.get('total', {}) or {}
    free = balance.get('free', {}) or {}
    used = balance.get('used', {}) or {}
    
    # Mapping "BTCUSDT" -> "BTC/USDT" vorbereiten
    market_symbols = set(markets.keys())
    normalized = {s.replace("/", ""): s for s in market_symbols}
    
    # Stablecoins die wir nicht verkaufen wollen
    stablecoins = {"USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "UST"}
    
    assets_processed = 0
    assets_sold = 0
    
    for asset, qty_total in totals.items():
        if not asset or asset.upper() in stablecoins:
            continue
        
        # bevorzugt normalisiert (z.B. 'BTCUSDT' -> 'BTC/USDT'), sonst Fallback
        symbol_key = f"{asset}USDT"
        symbol = normalized.get(symbol_key, f"{asset}/USDT")
        
        if symbol not in markets:
            logger.info(f"Reset: {symbol} nicht in Markets – übersprungen.",
                        extra={'event_type': 'RESET_SKIP_NOT_IN_MARKETS', 'symbol': symbol, 'asset': asset})
            continue
        
        m = markets.get(symbol, {})
        m_type = (m.get('type') or ('spot' if m.get('spot') else None))
        if m_type != 'spot':
            logger.info(f"Reset: {symbol} ist kein Spot-Markt – überspringe.",
                        extra={'event_type': 'RESET_SKIP_NOT_SPOT', 'symbol': symbol,
                               'm_type': m_type, 'active': m.get('active')})
            continue
        if m.get('active') is False:
            logger.info(f"Reset: {symbol} laut CCXT inactive – versuche Verkauf trotzdem.",
                        extra={'event_type': 'RESET_TRY_INACTIVE_MARKET', 'symbol': symbol})
        
        assets_processed += 1

        # Storniere offene Orders für dieses Symbol
        try:
            open_orders = exchange.fetch_open_orders(symbol)
            if open_orders:
                for o in open_orders:
                    try:
                        exchange.cancel_order(o['id'], symbol)
                        # Log MEXC cancel
                        try:
                            log_mexc_cancel(o['id'], symbol, reason="portfolio_reset")
                        except:
                            pass
                    except Exception as ce:
                        logger.warning(f"Cancel fehlgeschlagen {symbol}: {ce}",
                                     extra={'event_type': 'RESET_CANCEL_FAILED', 'symbol': symbol})
                logger.info(f"{len(open_orders)} offene Order(s) für {symbol} storniert",
                          extra={'event_type': 'RESET_ORDERS_CANCELED', 'symbol': symbol, 'count': len(open_orders)})
                # Nach Cancel Balance refreshen
                time.sleep(1)
                balance = exchange.fetch_balance()
                free = balance.get('free', {}) or {}
                used = balance.get('used', {}) or {}
        except Exception as e:
            logger.debug(f"Open orders check Fehler {symbol}: {e}",
                       extra={'event_type': 'RESET_OPEN_ORDERS_CHECK_ERROR', 'symbol': symbol})
        
        # Bestimme verfügbare Menge (free mit Fallback auf total)
        qty_free = float(free.get(asset, 0.0) or 0.0)
        qty_used = float(used.get(asset, 0.0) or 0.0)
        qty_total = float(totals.get(asset, 0.0) or 0.0)
        qty = qty_free if qty_free > 0 else qty_total
        
        if qty <= 0:
            logger.debug(f"Reset: {symbol} hat keine Menge (free={qty_free}, used={qty_used}, total={qty_total})",
                       extra={'event_type': 'RESET_SKIP_NO_BALANCE', 'symbol': symbol, 
                             'free': qty_free, 'used': qty_used, 'total': qty_total})
            continue
        
        # Preis on-demand holen (nicht auf vorgeholte tickers verlassen)
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = float(ticker.get('last') or ticker.get('close') or ticker.get('ask') or 0.0)
        except Exception as pe:
            logger.debug(f"Reset: Konnte Preis für {symbol} nicht abrufen: {pe}",
                       extra={'event_type': 'RESET_SKIP_NO_PRICE_FETCH', 'symbol': symbol})
            price = 0.0
            # Fallback: Best Bid/Ask aus Orderbuch
            try:
                ob = exchange.fetch_order_book(symbol)
                if ob and ob.get('bids'):
                    price = float(ob['bids'][0][0])
                elif ob and ob.get('asks'):
                    price = float(ob['asks'][0][0])
            except Exception:
                pass
        
        if price <= 0:
            logger.info(f"Reset: {symbol} hat keinen Preis – versuche dennoch Market-Sell ohne Wertprüfung.",
                        extra={'event_type': 'RESET_NO_PRICE_ATTEMPT_SELL', 'symbol': symbol,
                               'free': qty_free, 'used': qty_used, 'qty': qty})
            # Wir setzen später order_value nur, wenn price > 0 ist.
        
        # Limits und Min-Order-Value prüfen (nur wenn wir einen Preis haben)
        limits = markets[symbol].get('limits', {})
        
        # Amount-Präzision anwenden
        amount_precision = (limits.get('amount') or {}).get('precision')
        if amount_precision:
            step = 10 ** (-amount_precision)
            qty = floor_to_step(qty, step)
        
        # Nur prüfen wenn price > 0
        if price > 0:
            min_cost_raw = float((limits.get('cost') or {}).get('min') or 5.1)
            min_cost = min_cost_raw * min_order_buffer
            order_value = qty * price
            
            if order_value < min_cost:
                logger.info(f"Reset: {symbol} ist Dust (Wert={order_value:.2f} < {min_cost:.2f} USDT, qty={qty})",
                          extra={'event_type': 'RESET_SKIP_DUST', 'symbol': symbol, 
                                'value': order_value, 'min_required': min_cost, 
                                'qty': qty, 'price': price})
                continue
        else:
            order_value = 0  # Kein Preis verfügbar
        
        # Market-Sell ausführen
        try:
            if price > 0:
                logger.info(f"Verkaufe {qty} {asset} ({symbol}) für ~{order_value:.2f} USDT",
                          extra={'event_type': 'RESET_SELL_ATTEMPT', 'symbol': symbol, 
                                'amount': qty, 'est_value': order_value})
            else:
                logger.info(f"Verkaufe {qty} {asset} ({symbol}) ohne Preisinformation",
                          extra={'event_type': 'RESET_SELL_ATTEMPT', 'symbol': symbol, 
                                'amount': qty})
            
            # Nutze amount_to_precision für finale Präzision
            precise_qty = _amount_to_precision(exchange, symbol, qty)
            try:
                order = exchange.create_market_sell_order(symbol, precise_qty)
            except Exception as e:
                err = str(e).lower()
                
                # 1) Preis-Pflicht (MEXC 700004) -> Market mit Preis (oder Limit IOC) aus Orderbuch
                if ("mandatory parameter 'price'" in err) or ("700004" in err):
                    # Bestes Gebot (Bid) als Ausführungspreis holen
                    ob = exchange.fetch_order_book(symbol)
                    exec_price = None
                    if ob and ob.get('bids'):
                        exec_price = float(ob['bids'][0][0])
                    elif ob and ob.get('asks'):
                        exec_price = float(ob['asks'][0][0])
                    
                    if exec_price:
                        exec_price = _price_to_precision(exchange, symbol, exec_price)
                        logger.warning(
                            f"RESET_SELL_PRICE_FALLBACK {symbol}: nutze Preis {exec_price} wegen 700004",
                            extra={'event_type': 'RESET_SELL_PRICE_FALLBACK', 'symbol': symbol, 'price': exec_price}
                        )
                        # a) Market + price (manche MEXC-Setups akzeptieren das)
                        from utils import next_client_order_id
                        try:
                            client_order_id = next_client_order_id(symbol, side="SELL")
                            order = exchange.create_order(symbol, 'market', 'sell', precise_qty, exec_price, {'clientOrderId': client_order_id})
                        except Exception:
                            # b) Limit IOC als letzter Rettungsanker
                            order = exchange.create_limit_sell_order(symbol, precise_qty, exec_price, {'timeInForce': 'IOC'})
                    else:
                        raise e  # kein Preis verfügbar -> weiterwerfen
                
                # 2) Symbol-Route/Mapping-Problem (10007) -> Märkte neu laden & Limit-IOC versuchen
                elif ("symbol not support" in err) or ("10007" in err):
                    # a) Märkte neu laden
                    try:
                        exchange.load_markets(True)
                    except Exception:
                        pass
                    logger.warning(
                        f"RESET_SELL_RETRY {symbol}: first attempt failed: {e}, retrying with LIMIT-IOC",
                        extra={'event_type': 'RESET_SELL_RETRY', 'symbol': symbol}
                    )
                    
                    # b) Besten Bid als Preis holen
                    px = None
                    try:
                        ob = exchange.fetch_order_book(symbol)
                        if ob and ob.get('bids'):
                            px = float(ob['bids'][0][0])
                        elif ob and ob.get('asks'):
                            px = float(ob['asks'][0][0])
                    except Exception:
                        px = None
                    
                    if px:
                        px = _price_to_precision(exchange, symbol, px * 0.9995)  # 0.05% unter Bid für aggressiveres Kreuzen
                        # c) Limit-IOC platzieren (kreuzt Orderbuch und füllt i.d.R. sofort)
                        try:
                            logger.warning(
                                f"RESET_SELL_LIMIT_IOC {symbol}: Limit-IOC bei {px}",
                                extra={'event_type':'RESET_SELL_LIMIT_IOC','symbol':symbol,'price':px}
                            )
                            order = exchange.create_limit_sell_order(symbol, precise_qty, px, {'timeInForce': 'IOC'})
                        except Exception as e2:
                            # Fallback ohne IOC (GTC) – sollte ebenfalls sofort gefüllt werden
                            logger.warning(
                                f"RESET_SELL_LIMIT_GTC {symbol}: IOC fehlgeschlagen ({e2}), versuche GTC",
                                extra={'event_type':'RESET_SELL_LIMIT_GTC','symbol':symbol,'price':px}
                            )
                            order = exchange.create_limit_sell_order(symbol, precise_qty, px)
                    else:
                        # Kein Preis ermittelt → als letzter Versuch noch einmal Market
                        order = exchange.create_market_sell_order(symbol, precise_qty)
                
                else:
                    # Unbekannter Fehler → bubbled up
                    raise e
            
            if order:
                filled = float(order.get('filled', order.get('amount', 0)) or 0.0)
                avg = float(order.get('average', order.get('price', price)) or price)
                revenue = filled * avg
                
                if revenue <= 0:
                    # Letzter Versuch: Fills nachziehen
                    try:
                        # 1) Order-Status aktualisieren
                        oid = order.get('id')
                        if oid:
                            refreshed = exchange.fetch_order(oid, symbol)
                            if refreshed:
                                filled = float(refreshed.get('filled') or filled or 0)
                                avg = float(refreshed.get('average') or refreshed.get('price') or avg or price or 0)
                                revenue = (filled * avg) if (filled and avg) else revenue
                        # 2) Trades der letzten ~90s summieren
                        if revenue <= 0 and hasattr(exchange, 'fetch_my_trades'):
                            import time as _t
                            since = int((_t.time() - 90) * 1000)
                            trades = exchange.fetch_my_trades(symbol, since=since)
                            sold = [t for t in trades or [] if str(t.get('side')).lower() == 'sell']
                            if sold:
                                sum_cost = sum(float(t.get('cost') or 0) for t in sold)
                                sum_amt  = sum(float(t.get('amount') or 0) for t in sold)
                                if sum_cost > 0 and sum_amt > 0:
                                    revenue = sum_cost
                                    filled = sum_amt
                                    avg = revenue / filled
                    except Exception:
                        pass
                
                if revenue > 0:
                    settlement_manager.add_pending(f"{symbol}_{time.time()}", revenue, symbol)
                    assets_sold += 1
                    logger.info(f"Verkauf von {symbol} erfolgreich: {filled} @ {avg:.8f} = {revenue:.2f} USDT",
                              extra={'event_type': 'RESET_SELL_SUCCESS', 'symbol': symbol,
                                    'amount': filled, 'price': avg, 'revenue': revenue})
                else:
                    logger.warning(f"Reset: Verkauf von {symbol} ohne Revenue",
                                 extra={'event_type': 'RESET_SELL_NO_REVENUE', 'symbol': symbol})
            else:
                logger.warning(f"Reset: Verkauf von {symbol} gab None zurück",
                             extra={'event_type': 'RESET_SELL_NONE', 'symbol': symbol, 'qty': qty})
                
        except Exception as e:
            logger.error(f"Reset: Verkauf von {symbol} fehlgeschlagen: {e}",
                       extra={'event_type': 'RESET_SELL_FAILED', 'symbol': symbol, 
                             'qty': qty, 'price': price, 'error': str(e)})
    
    logger.info(f"Portfolio-Reset: {assets_processed} Assets geprüft, {assets_sold} verkauft",
              extra={'event_type': 'PORTFOLIO_RESET_SUMMARY', 
                    'assets_processed': assets_processed, 'assets_sold': assets_sold})

    # Auf Abschluss aller Settlements warten
    if settlement_manager.get_total_pending() > 0:
        logger.info("Alle Verkaufs-Orders platziert. Warte auf Abschluss der Settlements...")
        refresh_func = lambda: refresh_budget_from_exchange(exchange, 0)
        settlement_manager.wait_for_all_settlements(refresh_func, timeout=300)
    
    logger.info("Portfolio-Reset abgeschlossen.", extra={'event_type': 'PORTFOLIO_RESET_COMPLETE'})
    return True  # Erfolg

# =================================================================================
# Orderbuch-Analyse Funktionen
# =================================================================================
def fetch_top_of_book(exchange, symbol, depth=5):
    """Returns (best_bid, best_ask) from order book (top-of-book).
    Falls kein Orderbuch verfügbar ist, (None, None) zurückgeben."""
    try:
        ob = exchange.fetch_order_book(symbol, limit=depth)
        best_bid = ob['bids'][0][0] if ob.get('bids') else None
        best_ask = ob['asks'][0][0] if ob.get('asks') else None
        return best_bid, best_ask
    except Exception:
        return None, None

def compute_limit_buy_price_from_book(exchange, symbol, reference_last, aggressiveness=0.25):
    """
    Nutze Best-Ask + leichte Aggressivität, um Fills zu erleichtern.
    aggressiveness in %, z.B. 0.25 → 0.25% über Best-Bid/Ask.
    """
    best_bid, best_ask = fetch_top_of_book(exchange, symbol, depth=5)
    if best_ask:
        px = best_ask * (1.0 + aggressiveness / 100.0)  # etwas über Ask, damit IOC/Fill wahrscheinlicher
    elif best_bid:
        px = best_bid * (1.0 + aggressiveness / 100.0)  # kein Ask? → über Bid kaufen
    else:
        px = reference_last  # Fallback
    return _price_to_precision(exchange, symbol, px)

def place_limit_ioc_buy(exchange, symbol, amount, reference_price, price_buffer_pct=0.10, tif="IOC"):
    """
    Limit-IOC Buy mit Depth-Sweep (synthetic market) und Reprice-Loop.
    Fällt automatisch auf Best-Ask-Pricing zurück, wenn USE_DEPTH_SWEEP=False.
    """
    from config import (
        use_depth_sweep, sweep_orderbook_levels, max_slippage_bps_entry,
        sweep_reprice_attempts, sweep_reprice_sleep_ms, RUN_ID,
        ENTRY_LIMIT_OFFSET_BPS, ENTRY_ORDER_TIF
    )
    from utils import next_client_order_id
    
    filled_total = 0.0
    last_order = None
    remaining = float(amount or 0.0)
    attempt = 0
    if remaining <= 0:
        return None

    if use_depth_sweep:
        while remaining > 0 and attempt < int(sweep_reprice_attempts):
            attempt += 1
            sweep = compute_sweep_limit_price(
                exchange, symbol, "BUY", remaining, max_slippage_bps_entry, levels=int(sweep_orderbook_levels)
            )
            if not sweep:
                break
            px = sweep["price"]
            coid = next_client_order_id(symbol, side="BUY", decision_id=f"SWEEP-A{attempt}")
            logger.info(
                f"BUY_ORDER_PLACED {symbol}: Price={px:.8f} | Amount={remaining:.8f}",
                extra={
                    "event_type": "BUY_ORDER_PLACED",
                    "symbol": symbol,
                    "limit_price": px,
                    "amount": remaining,
                    "order_type": "LIMIT",
                    "sweep": True,
                    "slippage_bps": sweep.get("slippage_bps"),
                    "cum_qty": sweep.get("cum_qty"),
                    "had_enough_depth": sweep.get("had_enough_depth"),
                },
            )
            try:
                order = exchange.create_order(
                    symbol, "limit", "buy", remaining, px,
                    {"timeInForce": tif, "clientOrderId": coid}
                )
                last_order = order
                filled = float(order.get("filled") or 0.0)
                remaining = max(0.0, remaining - filled)
                filled_total += filled
                if filled > 0.0 and remaining <= 0.0:
                    return order
            except Exception as e:
                logger.debug(
                    f"LIMIT_IOC_BUY_REJECT {symbol}: {e}",
                    extra={"event_type": "LIMIT_IOC_BUY_REJECT", "symbol": symbol, "error": str(e)},
                )
            if attempt < int(sweep_reprice_attempts):
                time.sleep(float(sweep_reprice_sleep_ms) / 1000.0)
        return last_order
    else:
        # Fallback: bisherige Best-Ask + Premium Logik
        _, best_ask = fetch_top_of_book(exchange, symbol, depth=5)
        if not best_ask:
            best_ask = reference_price

        # Nutze ENTRY_LIMIT_OFFSET_BPS wenn definiert
        if ENTRY_LIMIT_OFFSET_BPS and ENTRY_LIMIT_OFFSET_BPS > 0:
            # Verwende konfigurierten Offset
            px = best_ask * (1.0 + ENTRY_LIMIT_OFFSET_BPS / 10000.0)
            px = _price_to_precision(exchange, symbol, px)
            try:
                coid = next_client_order_id(symbol, side="BUY")
                # Verwende ENTRY_ORDER_TIF wenn definiert, sonst den übergebenen tif
                order_tif = ENTRY_ORDER_TIF if ENTRY_ORDER_TIF else tif
                order = exchange.create_order(symbol, 'limit', 'buy', amount, px, {'timeInForce': order_tif, 'clientOrderId': coid})
                if order:
                    logger.info(f"BUY_ORDER_WITH_OFFSET {symbol}: Ask={best_ask:.8f}, Price={px:.8f} (+{ENTRY_LIMIT_OFFSET_BPS}bps), TIF={order_tif}",
                               extra={'event_type': 'BUY_ORDER_WITH_OFFSET', 'symbol': symbol, 
                                      'ask': best_ask, 'price': px, 'offset_bps': ENTRY_LIMIT_OFFSET_BPS})
                    if str(order.get('status','')).lower() in ('filled','closed') or float(order.get('filled') or 0.0) > 0.0:
                        return order
            except Exception as e:
                logger.debug(f"Order with offset failed: {e}")
        
        # Fallback zu Original-Logik mit mehreren Versuchen
        attempts = [price_buffer_pct, price_buffer_pct * 2.5, price_buffer_pct * 5.0]  # in %
        for buf_pct in attempts:
            px = best_ask * (1.0 + buf_pct / 100.0)  # etwas über Ask
            px = _price_to_precision(exchange, symbol, px)
            try:
                coid = next_client_order_id(symbol, side="BUY")
                order = exchange.create_order(symbol, 'limit', 'buy', amount, px, {'timeInForce': tif, 'clientOrderId': coid})
                if order:
                    if str(order.get('status','')).lower() in ('filled','closed') or float(order.get('filled') or 0.0) > 0.0:
                        return order
            except Exception:
                pass
        return None

def place_ioc_ladder_no_market(exchange, symbol, amount, reference_price,
                               held_assets, preise, active_order_id=None,
                               tif="IOC"):
    """
    Mehrstufige IOC-Leiter ohne Market-Fallback.
    - Aktualisiert vor jedem Schritt Best-Bid
    - Reduziert Menge bei Partial-Fills
    - Rest: kurzer POST_ONLY-GTC mit Auto-Reprice
    """
    from config import RUN_ID
    from loggingx import sell_limit_placed, order_replaced
    
    if active_order_id:
        try:
            exchange.cancel_order(active_order_id, symbol)
        except Exception:
            pass
        poll_order_canceled(exchange, symbol, active_order_id, 6.0)

    remaining = amount
    last_oid = None
    step_count = 0

    # 1) IOC-Stufen
    for bps in ioc_price_buffers_bps:
        # Top-of-Book holen
        best_bid, _ = fetch_top_of_book(exchange, symbol, depth=5)
        bid = best_bid or reference_price
        px  = _price_to_precision(exchange, symbol, bid * (1.0 - bps/10000.0))
        
        # Min notional check at this price level
        min_cost = compute_min_cost(exchange, symbol)
        order_value = remaining * px
        if order_value < min_cost:
            logger.debug(f"IOC ladder skip step {bps}bp for {symbol}: order_value={order_value:.4f} < min_cost={min_cost:.4f}",
                        extra={'event_type': 'IOC_LADDER_SKIP_MIN_COST', 'symbol': symbol, 
                              'step_bps': bps, 'order_value': order_value, 'min_cost': min_cost})
            continue
        
        client_oid = f"{RUN_ID}-{symbol}-{int(time.time()*1000)}-S{bps}"
        extra_params = {'timeInForce': tif, 'clientOrderId': client_oid}
        
        prev_oid = last_oid
        order = safe_create_limit_sell_order(exchange, symbol, remaining, px,
                                             held_assets, preise, active_order_id=last_oid,
                                             extra_params=extra_params)
        if not order:
            time.sleep(ioc_retry_sleep_s)
            continue

        last_oid = order.get('id')
        
        # Logging
        if step_count == 0:
            sell_limit_placed(symbol, last_oid, client_oid, px, remaining, tif, False, False,
                            price_src=f"best_bid-{bps}bp", intention="taker-ish")
        else:
            order_replaced(symbol, prev_oid, last_oid, step_count, 
                          bid * (1.0 - ioc_price_buffers_bps[step_count-1]/10000.0), px, bps)
        step_count += 1
        
        filled = float(order.get('filled') or 0.0)
        rem    = float(order.get('remaining') or 0.0)
        if filled > 0 and rem <= 0:
            return order                    # vollständig gefüllt
        if rem > 0:
            remaining = rem                 # Teilfüllung – erneut versuchen
            time.sleep(ioc_retry_sleep_s)
        else:
            # nicht gefüllt – nächste Stufe probieren
            time.sleep(ioc_retry_sleep_s)

    # 2) Restmenge als POST_ONLY mit kurzer TTL & Reprice
    t_start = time.time()
    while remaining > 0 and (time.time() - t_start) < post_only_rest_ttl_s:
        best_bid, _ = fetch_top_of_book(exchange, symbol, depth=5)
        bid = best_bid or reference_price
        px  = _price_to_precision(exchange, symbol, bid * (1.0 - post_only_undershoot_bps/10000.0))
        
        client_oid = f"{RUN_ID}-{symbol}-{int(time.time()*1000)}-SPO"
        extra_params = {'postOnly': True, 'clientOrderId': client_oid}
        
        prev_oid = last_oid
        order = safe_create_limit_sell_order(exchange, symbol, remaining, px,
                                             held_assets, preise,
                                             active_order_id=last_oid,
                                             extra_params=extra_params)
        if not order:
            time.sleep(0.3)
            continue

        last_oid = order.get('id')
        
        # Logging
        if prev_oid:
            order_replaced(symbol, prev_oid, last_oid, step_count, 
                          bid * (1.0 - post_only_undershoot_bps/10000.0), px, post_only_undershoot_bps)
        else:
            sell_limit_placed(symbol, last_oid, client_oid, px, remaining, "GTC", True, True,
                            price_src=f"best_bid-{post_only_undershoot_bps}bp", intention="maker")
        step_count += 1
        
        filled = float(order.get('filled') or 0.0)
        rem    = float(order.get('remaining') or 0.0)
        if filled > 0 and rem <= 0:
            return order
        if rem > 0:
            remaining = rem
            # Leichtes Reprice in der Schleife
            time.sleep(0.5)

    # Hard-No-Market: am Ende kein weiterer Fallback
    logger.warning(f"IOC/POST_ONLY exit incomplete {symbol}: remaining={remaining}",
                   extra={'event_type':'LIMIT_EXIT_INCOMPLETE','symbol':symbol,'remaining':remaining})
    return None

def place_limit_ioc_sell_ladder(exchange, symbol: str, qty: float, best_bid: float) -> dict:
    """
    Optimierte Exit-Ladder: Versucht schrittweise IOC-Limits unterhalb Bid.
    Stoppt beim ersten vollständigen Fill.
    Gibt ein Ergebnis-Dict zurück (filled/avg/fee usw.) für Logging & PnL.
    """
    from loggingx import buy_limit_placed, sell_limit_placed
    
    result = {"filled": 0.0, "avg": None, "fee": 0.0, "orders": []}
    remaining = float(qty)
    
    for step, bps in enumerate(exit_ladder_bps, 1):
        # Preis berechnen
        limit = best_bid * (1.0 - bps/10000.0)
        limit = _price_to_precision(exchange, symbol, limit)
        
        # Min notional check at this price level
        min_cost = compute_min_cost(exchange, symbol)
        order_value = remaining * limit
        if order_value < min_cost:
            logger.debug(f"Exit ladder skip step {step} ({bps}bp) for {symbol}: order_value={order_value:.4f} < min_cost={min_cost:.4f}",
                        extra={'event_type': 'EXIT_LADDER_SKIP_MIN_COST', 'symbol': symbol, 
                              'step': step, 'step_bps': bps, 'order_value': order_value, 'min_cost': min_cost})
            continue
        
        # Order platzieren
        order = None
        client_oid = f"{RUN_ID}-{symbol}-{int(time.time()*1000)}-EL{bps}"
        try:
            order = exchange.create_order(symbol, "limit", "sell", remaining, limit, 
                                         {"timeInForce": "IOC", "clientOrderId": client_oid})
            sell_limit_placed(symbol, order.get("id","n/a"), client_oid, limit, remaining,
                            "IOC", False, False, f"best_bid-{bps}bp", "exit-ladder")
        except Exception as e:
            log_event("ORDER_PLACE_ERROR", level="ERROR", symbol=symbol, 
                     context={"side":"sell","step":step,"err":str(e)})
            continue
        
        # Kurz warten (optional für Performance)
        if exit_ladder_sleep_ms > 0:
            time.sleep(exit_ladder_sleep_ms / 1000.0)
        
        # Fill-Status prüfen
        try:
            o = exchange.fetch_order(order["id"], symbol)
            filled = float(o.get("filled") or 0.0)
            remain = float(o.get("remaining") or 0.0)
            avg = float(o.get("average") or 0.0) or result["avg"]
            fee = 0.0
            if o.get("fee"):
                fee = float(o["fee"].get("cost") or 0.0)
            
            result["orders"].append(o)
            
            if filled > 0:
                result["filled"] += filled
                result["avg"] = avg
                result["fee"] += fee
                remaining = max(0.0, remaining - filled)
                
                if remaining <= 1e-8:
                    on_order_filled("sell", symbol, order["id"], avg=result["avg"], 
                                  filled=result["filled"], fee=result["fee"], maker=False)
                    return result
                    
        except Exception as e:
            log_event("ORDER_FETCH_ERROR", level="ERROR", symbol=symbol, 
                     context={"side":"sell","step":step,"err":str(e)})
            continue
    
    # Wenn noch Rest übrig: als Warnung loggen
    if remaining > 0:
        logger.warning(f"Exit-Ladder incomplete {symbol}: remaining={remaining}",
                      extra={'event_type':'EXIT_LADDER_INCOMPLETE','symbol':symbol,'remaining':remaining})

def place_ioc_sell_with_depth_sweep(exchange, symbol, amount, tif="IOC"):
    """
    Aggressiver Limit-IOC Sell via Bid-Depth-Sweep (keine Market-Orders).
    Reprice-Loop mit Slippage-Kappe.
    """
    from config import (
        max_slippage_bps_exit, sweep_orderbook_levels,
        sweep_reprice_attempts, sweep_reprice_sleep_ms, RUN_ID
    )
    from utils import next_client_order_id
    
    remaining = float(amount or 0.0)
    last_order = None
    attempt = 0
    if remaining <= 0:
        return None

    while remaining > 0 and attempt < int(sweep_reprice_attempts):
        attempt += 1
        sweep = compute_sweep_limit_price(
            exchange, symbol, "SELL", remaining, max_slippage_bps_exit, levels=int(sweep_orderbook_levels)
        )
        if not sweep:
            break
        px = sweep["price"]
        coid = next_client_order_id(symbol, side="SELL", decision_id=f"SWEEP-A{attempt}")
        logger.info(
            f"SELL_ORDER_PLACED {symbol}: Price={px:.8f} | Amount={remaining:.8f}",
            extra={
                "event_type": "SELL_ORDER_PLACED",
                "symbol": symbol,
                "limit_price": px,
                "amount": remaining,
                "order_type": "LIMIT",
                "sweep": True,
                "slippage_bps": sweep.get("slippage_bps"),
                "cum_qty": sweep.get("cum_qty"),
                "had_enough_depth": sweep.get("had_enough_depth"),
            },
        )
        try:
            order = exchange.create_order(
                symbol, "limit", "sell", remaining, px,
                {"timeInForce": tif, "clientOrderId": coid}
            )
            last_order = order
            filled = float(order.get("filled") or 0.0)
            remaining = max(0.0, remaining - filled)
            if remaining <= 0.0:
                return order
        except Exception as e:
            logger.debug(
                f"LIMIT_IOC_SELL_REJECT {symbol}: {e}",
                extra={"event_type": "LIMIT_IOC_SELL_REJECT", "symbol": symbol, "error": str(e)},
            )
        if attempt < int(sweep_reprice_attempts):
            time.sleep(float(sweep_reprice_sleep_ms) / 1000.0)
    return last_order

# =============================================================================
# CLIENT ORDER ID TRADING FUNCTIONS
# =============================================================================

def place_market_ioc_buy_with_coid(exchange, symbol: str, quote_amount: float, coid: str):
    """Place IOC market buy order with clientOrderId (quote amount)"""
    # Market-Quote-Buy (wenn Börse es unterstützt; sonst Menge berechnen)
    return exchange.create_order(symbol, "market", "buy", None, None,
                                {"cost": quote_amount, "timeInForce": "IOC", "clientOrderId": coid})

    
    return result