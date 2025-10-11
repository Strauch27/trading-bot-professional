#!/usr/bin/env python3
"""
Order Placement Module

Contains order placement functions:
- Limit/IOC orders with clientOrderId
- Precise limit buy logic
- Order ladder implementations
- Depth sweep order placement

NOTE: Diese Datei enthält Platzhalter-Implementierungen.
Die vollständigen Implementierungen können aus trading_legacy.py (Zeilen 30-1420) kopiert werden.
"""

import logging
from typing import Optional, Dict
from .helpers import sanitize_coid, price_to_precision, amount_to_precision

logger = logging.getLogger(__name__)


# =================================================================================
# Order-Funktionen mit clientOrderId Support
# =================================================================================

def place_limit_buy_with_coid(exchange, symbol, amount, price, coid):
    """Limit Buy Order mit clientOrderId"""
    coid = sanitize_coid(coid)
    return exchange.create_order(symbol, "limit", "buy", amount, price, {"clientOrderId": coid})


def place_limit_ioc_buy_with_coid(exchange, symbol, amount, price, coid):
    """Limit IOC Buy Order mit clientOrderId"""
    coid = sanitize_coid(coid)
    return exchange.create_order(symbol, "limit", "buy", amount, price, {"timeInForce": "IOC", "clientOrderId": coid})


def place_limit_ioc_sell_with_coid(exchange, symbol, amount, price, coid):
    """Limit IOC Sell Order mit clientOrderId"""
    coid = sanitize_coid(coid)
    try:
        return exchange.create_order(symbol, "limit", "sell", amount, price, {"timeInForce": "IOC", "clientOrderId": coid})
    except Exception as e:
        logger.debug(f"LIMIT_IOC_SELL_REJECT {symbol}: {e}",
                    extra={'event_type':'LIMIT_IOC_SELL_REJECT','symbol':symbol,'error':str(e)})
        raise


def place_market_ioc_sell_with_coid(exchange, symbol, amount, coid, held_assets=None, preise=None):
    """Market IOC Sell Order mit clientOrderId und safe amount calculation"""
    from .helpers import compute_safe_sell_amount
    import ccxt

    coid = sanitize_coid(coid)

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


def place_precise_limit_buy(exchange, symbol: str, quote_usdt: float, limit_price: float) -> Optional[Dict]:
    """
    V6 Buy-Logic: Platziert eine Limit-Buy-Order bei einem spezifizierten Preis.
    Wird von der Engine mit limit_price = last_price * predictive_buy_zone_pct aufgerufen.
    """
    from utils import get_symbol_limits, floor_to_step, next_client_order_id
    from config import min_order_buffer

    try:
        # Preis auf Exchange-Präzision
        px = price_to_precision(exchange, symbol, float(limit_price))
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
        amt = amount_to_precision(exchange, symbol, amt)

        # finale Sicherheitsprüfung
        if amt <= 0 or (amt * px) < min_cost:
            logger.warning(f"BUY_SKIP_UNDER_MIN {symbol}: amt={amt}, px={px}, val={amt*px:.4f} < min_cost={min_cost:.4f}",
                           extra={'event_type':'BUY_SKIP_UNDER_MIN','symbol':symbol,'amount':amt,'price':px,'min_cost':min_cost})
            return None

        # Order platzieren
        client_order_id = next_client_order_id(symbol, side="BUY")
        client_order_id = sanitize_coid(client_order_id)  # Doppelte Absicherung
        order = exchange.create_order(symbol, 'limit', 'buy', amt, px, {'clientOrderId': client_order_id})
        logger.info(f"BUY_LIMIT_PLACED {symbol}: limit={px:.8f}, amt={amt:.8f}, val={amt*px:.4f}",
                    extra={'event_type':'BUY_LIMIT_PLACED','symbol':symbol,'price':px,'amount':amt,'order_value':amt*px,'client_order_id':client_order_id})
        return order

    except Exception as e:
        msg = str(e)
        logger.error(f"BUY_LIMIT_ERROR {symbol}: {msg}",
                     extra={'event_type':'BUY_LIMIT_ERROR','symbol':symbol,'error':msg})
        return None


def place_limit_ioc_buy(exchange, symbol, amount, reference_price, price_buffer_pct=0.10, tif="IOC"):
    """
    Limit-IOC Buy mit Depth-Sweep (synthetic market) und Reprice-Loop.
    Fällt automatisch auf Best-Ask-Pricing zurück, wenn USE_DEPTH_SWEEP=False.
    """
    import time
    from config import (
        use_depth_sweep, sweep_orderbook_levels, max_slippage_bps_entry,
        sweep_reprice_attempts, sweep_reprice_sleep_ms, RUN_ID,
        ENTRY_LIMIT_OFFSET_BPS, ENTRY_ORDER_TIF
    )
    from utils import next_client_order_id
    from .orderbook import compute_sweep_limit_price, fetch_top_of_book

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
            px = price_to_precision(exchange, symbol, px)
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
            px = price_to_precision(exchange, symbol, px)
            try:
                coid = next_client_order_id(symbol, side="BUY")
                order = exchange.create_order(symbol, 'limit', 'buy', amount, px, {'timeInForce': tif, 'clientOrderId': coid})
                if order:
                    if str(order.get('status','')).lower() in ('filled','closed') or float(order.get('filled') or 0.0) > 0.0:
                        return order
            except Exception:
                pass
        return None


def place_limit_ioc_sell(exchange, symbol, amount, reference_price, held_assets, preise,
                         price_buffer_pct=0.10, active_order_id=None, tif="IOC"):
    """
    Versucht nacheinander IOC-Limits mit steigendem Abschlag unter Best-Bid,
    z.B. 0.10% → 0.25% → 0.50%. Falls alles nicht füllt, finaler Market (mit Preis).
    """
    from utils import next_client_order_id
    from .orderbook import fetch_top_of_book

    # Best-Bid mit neuer Funktion bestimmen
    best_bid, _ = fetch_top_of_book(exchange, symbol, depth=5)
    if not best_bid:
        best_bid = reference_price

    # Mehrere Versuche mit steigendem Buffer (in Prozent)
    attempts = [price_buffer_pct, price_buffer_pct * 2.5, price_buffer_pct * 5.0]  # in %

    for buf_pct in attempts:
        px = best_bid * (1.0 - buf_pct / 100.0)
        px = price_to_precision(exchange, symbol, px)

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
        px = price_to_precision(exchange, symbol, best_bid)
        client_order_id = next_client_order_id(symbol, side="SELL")
        return exchange.create_order(symbol, 'market', 'sell', amount, px, {'clientOrderId': client_order_id})
    except Exception as e:
        logger.error(f"FINAL_SELL_FALLBACK_FAILED {symbol}: {e}",
                     extra={'event_type':'FINAL_SELL_FALLBACK_FAILED','symbol':symbol,'error':str(e)})
        return None


#=================================================================================
# FEHLENDE FUNKTIONEN - BITTE AUS trading_legacy.py KOPIEREN
#=================================================================================
#
# Die folgenden Funktionen sind als Platzhalter implementiert.
# Kopiere die vollständigen Implementierungen aus trading_legacy.py:
#
# 1. safe_create_limit_sell_order
#    Zeilen: 569-609 (41 Zeilen)
#    Beschreibung: Cancel (optional) with polling, then place a limit sell
#
# 2. place_ioc_ladder_no_market
#    Zeilen: 1305-1419 (115 Zeilen)
#    Beschreibung: Mehrstufige IOC-Leiter ohne Market-Fallback
#
# 3. place_limit_ioc_sell_ladder
#    Zeilen: 1421-1494 (74 Zeilen)
#    Beschreibung: Optimierte Exit-Ladder mit IOC-Limits
#
# 4. place_ioc_sell_with_depth_sweep
#    Zeilen: 1496-1553 (58 Zeilen)
#    Beschreibung: Aggressiver Limit-IOC Sell via Bid-Depth-Sweep
#
# 5. place_market_ioc_buy_with_coid
#    Zeilen: 1559-1563 (5 Zeilen)
#    Beschreibung: Place IOC market buy order with clientOrderId
#
#=================================================================================


def safe_create_limit_sell_order(exchange, symbol, desired_amount, price, held_assets, preise,
                                 active_order_id=None, extra_params=None):
    """Cancel (optional) with polling, then place a limit sell using balance-aware amount & precision."""
    from .settlement import poll_order_canceled, sync_active_order_and_state
    from .helpers import compute_safe_sell_amount, price_to_precision
    from utils import next_client_order_id
    import ccxt

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

    px = price_to_precision(exchange, symbol, price)
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


def place_ioc_ladder_no_market(exchange, symbol, amount, reference_price,
                               held_assets, preise, active_order_id=None, tif="IOC"):
    """
    Mehrstufige IOC-Leiter ohne Market-Fallback.
    - Aktualisiert vor jedem Schritt Best-Bid
    - Reduziert Menge bei Partial-Fills
    - Rest: kurzer POST_ONLY-GTC mit Auto-Reprice
    """
    import time
    from config import RUN_ID, ioc_price_buffers_bps, ioc_retry_sleep_s, post_only_rest_ttl_s, post_only_undershoot_bps
    from loggingx import sell_limit_placed, order_replaced
    from .settlement import poll_order_canceled
    from .orderbook import fetch_top_of_book
    from .helpers import price_to_precision, compute_min_cost

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
        px  = price_to_precision(exchange, symbol, bid * (1.0 - bps/10000.0))

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
        px  = price_to_precision(exchange, symbol, bid * (1.0 - post_only_undershoot_bps/10000.0))

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
    import time
    from config import RUN_ID, exit_ladder_bps, exit_ladder_sleep_ms
    from loggingx import sell_limit_placed, log_event, on_order_filled
    from .helpers import price_to_precision, compute_min_cost

    result = {"filled": 0.0, "avg": None, "fee": 0.0, "orders": []}
    remaining = float(qty)

    for step, bps in enumerate(exit_ladder_bps, 1):
        # Preis berechnen
        limit = best_bid * (1.0 - bps/10000.0)
        limit = price_to_precision(exchange, symbol, limit)

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

    return result


def place_ioc_sell_with_depth_sweep(exchange, symbol, amount, tif="IOC"):
    """
    Aggressiver Limit-IOC Sell via Bid-Depth-Sweep (keine Market-Orders).
    Reprice-Loop mit Slippage-Kappe.
    """
    import time
    from config import (
        max_slippage_bps_exit, sweep_orderbook_levels,
        sweep_reprice_attempts, sweep_reprice_sleep_ms, RUN_ID
    )
    from utils import next_client_order_id
    from .orderbook import compute_sweep_limit_price

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


def place_market_ioc_buy_with_coid(exchange, symbol: str, quote_amount: float, coid: str):
    """
    Place IOC market buy order with clientOrderId (quote amount)
    """
    return exchange.create_order(symbol, "market", "buy", None, None,
                                {"cost": quote_amount, "timeInForce": "IOC", "clientOrderId": coid})


# Export note: Vollständige Signaturen sind in trading/__init__.py definiert
