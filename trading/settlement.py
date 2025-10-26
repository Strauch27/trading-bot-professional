#!/usr/bin/env python3
"""
Settlement & Balance Management Module

Contains functions for:
- Budget refresh from exchange
- Balance settlement tracking
- Order state synchronization

NOTE: Vollständige Implementierungen können aus trading_legacy.py (Zeilen 353-746) kopiert werden.
"""

import time
import logging
import ccxt
import threading
from typing import Optional, Tuple, Dict
from .helpers import get_free

logger = logging.getLogger(__name__)

# Budget refresh cache to prevent blocking main thread
_budget_cache = {"value": None, "timestamp": 0.0, "lock": threading.Lock()}


def sync_active_order_and_state(exchange, symbol, held_assets, my_budget, settlement_manager):
    """Update state from currently active order (TP/SL) to reflect partial/filled fills.
       Returns (status, order_dict) where status is one of None/'filled'/'partial'/'open'.
       Note: my_budget parameter is kept for compatibility but not modified internally.
    """
    from .helpers import base_currency
    from utils import save_trade_history

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
        actual_balance = get_free(exchange, base_currency(symbol))
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


def refresh_budget_from_exchange(exchange, my_budget, max_retries=3, delay=2.0) -> float:
    """
    Holt das aktuelle USDT-Budget von der Exchange mit Retry-Logik (shutdown-aware)
    """
    # Try to get shutdown coordinator (optional)
    shutdown_coordinator = None
    try:
        from services.shutdown_coordinator import get_shutdown_coordinator
        shutdown_coordinator = get_shutdown_coordinator()
    except ImportError:
        pass

    for attempt in range(int(max_retries)):
        # Check shutdown before each attempt
        if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
            return my_budget

        try:
            usdt_free = get_free(exchange, "USDT")
            old_budget = my_budget
            my_budget = usdt_free
            if abs(old_budget - my_budget) > 0.01:
                logger.info(f"Budget von Exchange aktualisiert: {old_budget:.2f} -> {my_budget:.2f} USDT",
                          extra={'event_type': 'BUDGET_REFRESHED', 'old': old_budget, 'new': my_budget})

            # Speichere Zeit des letzten Erfolgs
            refresh_budget_from_exchange.last_success = time.time()
            return my_budget
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Budget-Refresh fehlgeschlagen (Versuch {attempt+1}/{max_retries}), warte {delay}s...",
                             extra={'event_type': 'BUDGET_REFRESH_RETRY'})
                # Shutdown-aware delay
                if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=delay):
                    return my_budget
                elif not shutdown_coordinator:
                    time.sleep(delay)
            else:
                logger.error(f"Budget-Refresh endgültig fehlgeschlagen nach {max_retries} Versuchen",
                           extra={'event_type': 'BUDGET_REFRESH_FAILED'})

    return my_budget


def refresh_budget_from_exchange_safe(exchange, my_budget, timeout=5.0) -> float:
    """
    Timeout-protected wrapper for refresh_budget_from_exchange.

    Prevents main thread from blocking on slow exchange calls by:
    1. Running refresh in background thread with timeout
    2. Returning cached value if timeout occurs
    3. Updating cache on successful refresh

    Args:
        exchange: CCXT exchange instance
        my_budget: Current budget fallback value
        timeout: Maximum time to wait for refresh (default 5.0s)

    Returns:
        Refreshed budget from exchange, or cached/fallback value on timeout
    """
    global _budget_cache

    # Container for result from background thread
    result_container = {"value": my_budget, "success": False}

    def _refresh_worker():
        """Background worker that calls the blocking refresh function"""
        try:
            fresh_budget = refresh_budget_from_exchange(exchange, my_budget, max_retries=1, delay=0.5)
            result_container["value"] = fresh_budget
            result_container["success"] = True

            # Update cache
            with _budget_cache["lock"]:
                _budget_cache["value"] = fresh_budget
                _budget_cache["timestamp"] = time.time()

        except Exception as e:
            logger.warning(f"Budget refresh worker exception: {e}")

    # Start background worker thread
    worker = threading.Thread(target=_refresh_worker, daemon=True, name="BudgetRefreshWorker")
    worker.start()

    # Wait for completion with timeout
    worker.join(timeout=timeout)

    if worker.is_alive():
        # Timeout occurred - return cached value if available
        logger.warning(
            f"Budget refresh timeout after {timeout}s - using cached value",
            extra={"event_type": "BUDGET_REFRESH_TIMEOUT", "timeout_s": timeout}
        )

        with _budget_cache["lock"]:
            if _budget_cache["value"] is not None:
                cache_age_s = time.time() - _budget_cache["timestamp"]
                logger.debug(f"Using cached budget value (age: {cache_age_s:.1f}s)")
                return _budget_cache["value"]

        # No cache available - return fallback
        return my_budget

    elif result_container["success"]:
        # Refresh completed successfully
        return result_container["value"]

    else:
        # Refresh failed - return cached or fallback
        with _budget_cache["lock"]:
            if _budget_cache["value"] is not None:
                return _budget_cache["value"]
        return my_budget


def wait_for_balance_settlement(my_budget, expected_increase, settlement_manager, refresh_func,
                                max_wait=15, check_interval=1.0) -> bool:
    """Wartet bis eine erwartete Gutschrift auf der Exchange angekommen ist (shutdown-aware)"""
    # Try to get shutdown coordinator (optional)
    shutdown_coordinator = None
    try:
        from services.shutdown_coordinator import get_shutdown_coordinator
        shutdown_coordinator = get_shutdown_coordinator()
    except ImportError:
        pass

    initial_budget = my_budget
    target_budget = initial_budget + (expected_increase * 0.98)  # 2% Toleranz für Fees

    logger.info(f"Warte auf Gutschrift von ~{expected_increase:.2f} USDT (Ziel: {target_budget:.2f})",
              extra={'event_type': 'WAITING_FOR_SETTLEMENT', 'expected': expected_increase})

    # Nutze Settlement-Manager-Statistiken für bessere Wartezeit
    avg_settlement_time = settlement_manager.get_average_settlement_time()
    adjusted_max_wait = max(max_wait, avg_settlement_time * 2)  # Mindestens 2x durchschnittliche Zeit

    for i in range(int(adjusted_max_wait / check_interval)):
        # Shutdown-aware sleep
        if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=check_interval):
            logger.info("Shutdown requested during settlement wait")
            return False
        elif not shutdown_coordinator:
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


def poll_order_canceled(exchange, symbol, order_id, timeout_s=6.0):
    """Wartet auf Bestätigung der Order-Stornierung (shutdown-aware)"""
    from loggingx import log_mexc_update
    from utils import with_backoff

    # Try to get shutdown coordinator (optional)
    shutdown_coordinator = None
    try:
        from services.shutdown_coordinator import get_shutdown_coordinator
        shutdown_coordinator = get_shutdown_coordinator()
    except ImportError:
        pass

    t0 = time.time()
    while time.time() - t0 <= timeout_s:
        # Check shutdown
        if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
            return None

        try:
            st = with_backoff(exchange.fetch_order, order_id, symbol)
            # Log MEXC update
            try:
                log_mexc_update(order_id, st or {})
            except Exception:
                # Logging is optional, don't fail order polling
                pass
            if st and st.get("status") in ("canceled", "closed"):
                return st
        except (ccxt.OrderNotFound, ccxt.BadRequest):
            # Consider it gone
            return None

        # Shutdown-aware sleep
        if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=0.4):
            return None
        elif not shutdown_coordinator:
            time.sleep(0.4)
    return None


def place_safe_market_sell(exchange, symbol, desired_amount, held_assets, preise, active_order_id=None):
    """Cancel existing order (optional), poll, then market sell only what is actually sellable."""
    from .helpers import compute_safe_sell_amount

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
        # Try to get shutdown coordinator (optional)
        shutdown_coordinator = None
        try:
            from services.shutdown_coordinator import get_shutdown_coordinator
            shutdown_coordinator = get_shutdown_coordinator()
        except ImportError:
            pass

        for _ in range(8):
            # Shutdown-aware sleep
            if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=0.4):
                return None
            elif not shutdown_coordinator:
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
        # Retry once after short wait with a fresh balance check (shutdown-aware)
        logger.warning(f"SAFE_SELL_RETRY {symbol}: first attempt failed: {e}",
                      extra={"event_type":"SAFE_SELL_RETRY","symbol":symbol})

        # Try to get shutdown coordinator (optional)
        shutdown_coordinator = None
        try:
            from services.shutdown_coordinator import get_shutdown_coordinator
            shutdown_coordinator = get_shutdown_coordinator()
        except ImportError:
            pass

        # Shutdown-aware sleep
        if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=0.6):
            return None
        elif not shutdown_coordinator:
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
