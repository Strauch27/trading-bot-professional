# loggingx.py - Konsolidiertes Logging mit Rotation
import json
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional
from config import ORDER_UPDATE_MIN_INTERVAL_S, ORDER_UPDATE_MIN_DELTA_FILLED
try:
    from config import (ENABLE_PRETTY_TRADE_LOGS, USE_ANSI_COLORS, ICONS, 
                       PRICE_DECIMALS, QTY_DECIMALS, PNL_DECIMALS, 
                       SHOW_TIF, SHOW_MAKER, SHOW_DURATION_LONG)
except Exception:
    ENABLE_PRETTY_TRADE_LOGS, USE_ANSI_COLORS = True, True
    ICONS = {}
    PRICE_DECIMALS, QTY_DECIMALS, PNL_DECIMALS = 6, 6, 2
    SHOW_TIF, SHOW_MAKER, SHOW_DURATION_LONG = True, True, True

# =============================================================================
# ANSI COLOR HELPERS
# =============================================================================
RESET="\x1b[0m"; DIM="\x1b[2m"; BOLD="\x1b[1m"
RED="\x1b[31m"; GREEN="\x1b[32m"; YELLOW="\x1b[33m"; CYAN="\x1b[36m"

_ANSI = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}

def _c(txt, color):
    """Apply ANSI color to text if enabled"""
    if not USE_ANSI_COLORS:
        return txt
    return f"{_ANSI.get(color,'')}{txt}{_ANSI['reset']}"

def c(txt, color=None):
    """Enhanced color function using constants"""
    if not USE_ANSI_COLORS or not color:
        return str(txt)
    return f"{color}{txt}{RESET}"

def fmt_pnl_usdt(x):
    """Format PnL in USDT with color"""
    col = GREEN if x >= 0 else RED
    return c(f"{x:+.{PNL_DECIMALS}f}", col)

def fmt_pct(x):
    """Format percentage with color"""
    col = GREEN if x >= 0 else RED
    return c(f"{x:+.{PNL_DECIMALS}f}%", col)

def fmt_price(p): 
    """Format price"""
    return f"{p:.{PRICE_DECIMALS}f}"

def fmt_qty(q):
    """Format quantity"""
    return f"{q:.{QTY_DECIMALS}f}"

def _console_info(event_type: str, msg: str):
    """
    Send a readable line to root logger (console),
    in addition to JSON event in 'events' logger
    """
    if not ENABLE_PRETTY_TRADE_LOGS:
        return
    logging.getLogger(__name__).info(msg, extra={"event_type": event_type})

# =============================================================================
# TIME HELPERS
# =============================================================================
def _utc_iso() -> str:
    """Get current UTC timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()

# =============================================================================
# JSON SERIALIZATION HELPER
# =============================================================================
def _json_default(o):
    """JSON fallback for non-serializable types"""
    # Sets as sorted lists
    if isinstance(o, (set, frozenset)):
        return sorted(list(o))
    # Datetime objects as ISO format
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    # Decimal as string for precision
    if isinstance(o, Decimal):
        return str(o)
    # Path objects as string
    if isinstance(o, Path):
        return str(o)
    # Try to convert other iterables to list
    try:
        if hasattr(o, "__iter__") and not isinstance(o, (dict, list, tuple, str, bytes)):
            return list(o)
    except Exception:
        pass
    # Last fallback: string representation
    try:
        return str(o)
    except Exception:
        return None

# =============================================================================
# ROTATING LOGGER SETUP
# =============================================================================
def setup_rotating_logger(logger_name: str, log_file: str = None,
                          max_bytes: int = 50_000_000, backups: int = 5):
    """Setup logger with rotating file handler (dedupliziert & propagierend)"""
    from config import LOG_FILE
    import os

    target = os.path.abspath(log_file or LOG_FILE)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = True  # wichtig für Split-Logs am Root

    # keinen zweiten identischen Rotating-Handler an denselben Pfad hängen
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(getattr(h, "baseFilename", "")) == target:
                    return logger
            except Exception:
                pass

    os.makedirs(os.path.dirname(target), exist_ok=True)
    h = RotatingFileHandler(target, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True)
    h.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(h)
    return logger

# =============================================================================
# METRICS & EVENT LOGGING
# =============================================================================
def log_metric(name: str, value: Any, labels: Optional[Dict] = None):
    """Log a metric with value and optional labels"""
    from config import METRICS_LOG
    row = {
        "ts": _utc_iso(),
        "metric": name,
        "value": value,
        "labels": labels or {}
    }
    try:
        with open(METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
    except Exception:
        pass

def exit_placed(symbol: str, *args, **kwargs):
    """
    Kompatibel zu:
      - exit_placed(symbol, order_id, price, amount, reason)   [legacy]
      - exit_placed(symbol, reason, ctx_dict)                  [engine ctx-style]
      - exit_placed(symbol, reason=..., ctx={...})             [keyword]
    """
    if len(args) >= 4:
        order_id, price, amount, reason = args[:4]
        payload = {"order_id": order_id, "price": price, "amount": amount, "reason": reason}
    else:
        reason = args[0] if args else kwargs.pop("reason", None)
        ctx = args[1] if len(args) > 1 else kwargs.pop("ctx", {}) or {}
        payload = {"reason": reason, **ctx}
    log_event("EXIT_PLACED", symbol=symbol, context=payload)

def exit_filled(symbol: str, *args, **kwargs):
    """
    Kompatibel zu:
      - exit_filled(symbol, order_id, avg_fill_price, amount, pnl)   [legacy]
      - exit_filled(symbol, reason, fees=..., ctx={...})             [engine ctx-style]
    """
    if len(args) >= 4 and isinstance(args[0], str) and not kwargs:
        order_id, avg_fill_price, amount, pnl = args[:4]
        payload = {"order_id": order_id, "avg_price": avg_fill_price, "amount": amount, "pnl": pnl}
    else:
        reason = args[0] if args else kwargs.pop("reason", None)
        fees = kwargs.pop("fees", None)
        ctx = kwargs.pop("ctx", {}) or {}
        payload = {"reason": reason, "fees": fees, **ctx}
    log_event("EXIT_FILLED", symbol=symbol, context=payload)

    # Soft-Event für Runtime-Hooks (Engine/Notifier)
    try:
        from event_bus import emit
        emit("EXIT_FILLED", symbol=symbol, **payload)
    except Exception:
        # Keine harte Abhängigkeit und niemals hier crashen
        pass

# =============================================================================
# GUARD & DECISION REPORTING (V7 compatibility)
# =============================================================================
def report_guards(symbol: str, guards: list) -> bool:
    """Report guard evaluation results"""
    decision = "PASSED" if all(g.get("passed") for g in guards) else "BLOCKED"
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "GUARD_EVAL",
        "symbol": symbol,
        "guards": guards,
        "decision": decision
    }))
    return decision == "PASSED"

def report_buy_sizing(symbol: str, ctx: dict):
    """Report buy sizing details"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "BUY_SIZING_FINAL",
        "symbol": symbol,
        "context": ctx
    }))

def report_order_progress(symbol: str, order_id: str, ctx: dict):
    """Report order progress"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "ORDER_PROGRESS",
        "symbol": symbol,
        "order_id": order_id,
        "context": ctx
    }))

def report_decision(symbol: str, side: str, taken: bool, reason: str, ctx: dict = None):
    """Report trading decision"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "DECISION",
        "symbol": symbol,
        "side": side,
        "taken": taken,
        "reason": reason,
        "context": ctx or {}
    }))

def emit_metric(name: str, value: Any, extra: dict = None):
    """Emit metric (wrapper for compatibility)"""
    log_metric(name, value, extra)

# =============================================================================
# ERROR LOGGING
# =============================================================================
def log_detailed_error(logger, error_tracker,
                       error_type: str,
                       symbol: str = None,
                       error: Exception = None,
                       context: dict = None,
                       my_budget: float = None,
                       held_assets: dict = None,
                       open_buy_orders: dict = None,
                       settlement_manager = None):
    """Log detailed error with context"""
    error_data = {
        "ts": _utc_iso(),
        "error_type": error_type,
        "symbol": symbol,
        "error": str(error) if error else None,
        "context": context or {},
        "state": {
            "budget": my_budget,
            "held_count": len(held_assets) if held_assets else 0,
            "open_buys_count": len(open_buy_orders) if open_buy_orders else 0
        }
    }
    
    if settlement_manager and hasattr(settlement_manager, 'pending_settlements'):
        error_data["state"]["pending_settlements"] = len(settlement_manager.pending_settlements)
    
    logger.error(json.dumps(error_data))
    
    # Track error if tracker provided
    if error_tracker and hasattr(error_tracker, 'log_error'):
        error_tracker.log_error(error_type, symbol, error, context)

# =============================================================================
# MEXC ORDER & TRADE LOGGING
# =============================================================================
def log_mexc_order(order_data: dict):
    """Log MEXC order creation"""
    logging.getLogger("mexc").info(json.dumps({
        "ts": _utc_iso(),
        "event": "MEXC_ORDER_CREATED",
        "order_id": order_data.get("id"),
        "symbol": order_data.get("symbol"),
        "side": order_data.get("side"),
        "type": order_data.get("type"),
        "price": order_data.get("price"),
        "amount": order_data.get("amount"),
        "status": order_data.get("status")
    }))

def log_mexc_trade(trade_data: dict):
    """Log MEXC trade execution"""
    logging.getLogger("mexc").info(json.dumps({
        "ts": _utc_iso(),
        "event": "MEXC_TRADE_EXECUTED",
        "trade_id": trade_data.get("id"),
        "order_id": trade_data.get("order"),
        "symbol": trade_data.get("symbol"),
        "side": trade_data.get("side"),
        "price": trade_data.get("price"),
        "amount": trade_data.get("amount"),
        "cost": trade_data.get("cost")
    }))

def log_mexc_update(order_id: str, update_data: dict,
                    min_interval_s: int | None = None,
                    min_delta_filled: float | None = None):
    """
    Loggt MEXC_ORDER_UPDATE nur bei Änderungen oder nach Ablauf einer Minimalperiode.
    min_interval_s / min_delta_filled kommen standardmäßig aus config.py.
    """
    global _order_last
    if '_order_last' not in globals():
        _order_last = {}

    if min_interval_s is None:
        min_interval_s = ORDER_UPDATE_MIN_INTERVAL_S
    if min_delta_filled is None:
        min_delta_filled = ORDER_UPDATE_MIN_DELTA_FILLED

    now = time.time()
    status = str(update_data.get("status") or "").lower()
    try:
        filled = float(update_data.get("filled") or 0.0)
    except Exception:
        filled = 0.0

    prev = _order_last.get(order_id)
    if prev:
        same_status = (status == prev.get("status"))
        same_fill   = (abs(filled - prev.get("filled", 0.0)) < min_delta_filled)
        too_soon    = (now - prev.get("ts", 0.0) < min_interval_s)
        if same_status and same_fill and too_soon:
            return  # Nichts Neues → kein Log

    _order_last[order_id] = {"status": status, "filled": filled, "ts": now}

    logging.getLogger("mexc").info(json.dumps({
        "ts": _utc_iso(),
        "event": "ORDER_UPDATE",
        "event_type": "MEXC_ORDER_UPDATE",
        "order_id": order_id,
        "status": update_data.get("status"),
        "filled": update_data.get("filled"),
        "remaining": update_data.get("remaining"),
        "average": update_data.get("average"),
        "trades": update_data.get("trades", [])
    }))

def log_mexc_cancel(order_id: str, symbol: str, reason: str = None):
    """Log MEXC order cancellation"""
    logging.getLogger("mexc").info(json.dumps({
        "ts": _utc_iso(),
        "event": "ORDER_CANCELLED",
        "event_type": "MEXC_ORDER_CANCELLED",
        "order_id": order_id,
        "symbol": symbol,
        "reason": reason
    }))

# =============================================================================
# COMPATIBILITY FUNCTIONS
# =============================================================================
def on_order_placed(side: str, symbol: str, order_id: str,
                   limit: float, amount: float, tif="GTC", clientOrderId="", **kwargs):
    """Log order placement (compatibility)"""
    parts = [ICONS.get("placed", "⌛"), f"{side.upper()} PLACED {symbol}"]
    if SHOW_TIF: 
        parts.append(f"TimeInForce={tif}")
    parts += [f"Limit @ {fmt_price(limit)}", f"Amount {fmt_qty(amount)}"]
    txt = " | ".join(parts)
    logger = logging.getLogger(__name__)
    logger.info(txt, extra={'event_type': f'{side.upper()}_ORDER_PLACED','symbol':symbol})
    
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": f"{side.upper()}_LIMIT_PLACED",
        "symbol": symbol,
        "order_id": order_id,
        "limit": limit,
        "amount": amount,
        "tif": tif,
        "clientOrderId": clientOrderId,
        **kwargs
    }))
    
    # Audit trail
    log_audit_event(f"{side.upper()}_ORDER_PLACED", {
        "symbol": symbol,
        "order_id": order_id,
        "side": side,
        "type": "limit",
        "amount": amount,
        "limit": limit,
        "tif": kwargs.get("timeInForce", "GTC"),
        "clientOrderId": kwargs.get("clientOrderId")
    })
    
    # Pretty console output
    side_u = (side or "").lower()
    if side_u == "buy":
        icon = "⌛"
        msg = f"{icon} {_c('BUY PLACED','yellow')} {symbol} @ {float(limit):.8f} | Qty={float(amount):.8f} | id={order_id[:8]}..."
        _console_info("BUY_PLACED", msg)
    elif side_u == "sell":
        icon = "⌛"
        msg = f"{icon} {_c('SELL PLACED','yellow')} {symbol} @ {float(limit):.8f} | Qty={float(amount):.8f} | id={order_id[:8]}..."
        _console_info("SELL_PLACED", msg)

def on_order_filled(side: str, symbol: str, order_id: str,
                   avg: float, filled: float, remaining: float,
                   fee: float, maker: bool, clientOrderId="", **kwargs):
    """Log order fill (compatibility)"""
    parts = [ICONS.get("buy", "✅") if side=="buy" else ICONS.get("sell_filled", "✅"),
             f"{side.upper()} FILLED {symbol} @ {fmt_price(avg)}",
             f"Filled {fmt_qty(filled)}", f"Fee {fee:.4f}"]
    if SHOW_MAKER: 
        parts.append(f"Maker={bool(maker)}")
    txt = " | ".join(parts)
    logging.getLogger(__name__).info(txt, extra={'event_type': f'{side.upper()}_FILLED','symbol':symbol})
    
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "ORDER_FILLED",
        "side": side,
        "symbol": symbol,
        "order_id": order_id,
        "avg": avg,
        "filled": filled,
        "remaining": remaining,
        "fee": fee,
        "maker": maker,
        "clientOrderId": clientOrderId,
        **kwargs
    }))
    
    # Pretty console output
    if ENABLE_PRETTY_TRADE_LOGS:
        side_upper = side.upper()
        if side_upper == "BUY":
            icon = "✅" if USE_ANSI_COLORS else "[BUY]"
            color = "\033[92m" if USE_ANSI_COLORS else ""  # Green
        else:
            icon = "✅" if USE_ANSI_COLORS else "[SELL]"
            color = "\033[93m" if USE_ANSI_COLORS else ""  # Yellow
        reset = "\033[0m" if USE_ANSI_COLORS else ""
        
        msg = f"{color}{icon} {side_upper} FILLED: {symbol} @ {avg:.8f} x {filled:.8f} | Fee: {fee:.4f} USDT{reset}"
        _console_info("ORDER_FILLED", msg)
    
    # Audit trail
    log_audit_event("ORDER_FILLED", {
        "symbol": symbol,
        "order_id": order_id,
        "side": side,
        "avg_price": avg,
        "filled": filled,
        "remaining": remaining,
        "fee": fee,
        "maker": maker,
        "clientOrderId": kwargs.get("clientOrderId")
    })
    
    # Pretty console output
    side_u = (side or "").lower()
    if side_u == "buy":
        icon = "✅"
        msg = f"{icon} {_c('BUY FILLED','green')}  {symbol} @ {float(avg):.8f} | Qty={float(filled):.8f} | Fee={float(fee):.6f} | id={order_id[:8]}..."
        _console_info("BUY_FILLED", msg)
    elif side_u == "sell":
        icon = "✅"
        msg = f"{icon} {_c('SELL FILLED','green')} {symbol} @ {float(avg):.8f} | Qty={float(filled):.8f} | Fee={float(fee):.6f} | id={order_id[:8]}..."
        _console_info("SELL_FILLED", msg)
    
    # --- Telegram Alert ---
    try:
        from telegram_notify import tg
        side_u = (side or "").lower()
        if side_u == "buy":
            tg.notify_buy_filled(
                symbol, float(avg), float(filled), float(fee or 0.0), str(order_id or ""),
                maker=bool(maker),
                client_order_id=str(clientOrderId or kwargs.get("clientOrderId","") or ""),
                order_type=kwargs.get("order_type", "LIMIT"),
                tif=kwargs.get("tif", "GTC")
            )
        elif side_u == "sell":
            tg.notify_sell_filled(
                symbol, float(avg), float(filled), float(fee or 0.0), str(order_id or ""),
                maker=bool(maker),
                client_order_id=str(clientOrderId or kwargs.get("clientOrderId","") or ""),
                order_type=kwargs.get("order_type", "LIMIT"),
                tif=kwargs.get("tif", "IOC"),
                pnl_percentage=kwargs.get("pnl_percentage"),
                profit_usdt=kwargs.get("profit_usdt"),
                duration_s=kwargs.get("duration_s"),
                reason=kwargs.get("reason")
            )
    except Exception:
        pass

def on_order_replaced(symbol: str, old_order_id: str, new_order_id: str, **kwargs):
    """Log order replacement (compatibility)"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "ORDER_REPLACED",
        "symbol": symbol,
        "old_order_id": old_order_id,
        "new_order_id": new_order_id,
        **kwargs
    }))

def on_exit_executed(symbol: str, reason: str, entry_price=0, exit_price=0, 
                    amount=0, pnl_percentage=0, profit_usdt=0, order_id="", 
                    duration_s=0, **kwargs):
    """Log exit execution (compatibility)"""
    icon = ICONS.get("sell_tp", "🟢") if reason=="tp" else ICONS.get("sell_sl", "🔴") if reason=="sl" else ICONS.get("sell_filled", "✅")
    dur = (time.strftime("%H:%M:%S", time.gmtime(duration_s)) if SHOW_DURATION_LONG else f"{int(duration_s)}s")
    txt = (f"{icon} {reason.upper()} EXECUTED {symbol} | "
           f"Profit {fmt_pnl_usdt(profit_usdt)} ({fmt_pct(pnl_percentage*100)}) | Dauer {dur}")
    logging.getLogger(__name__).info(txt, extra={'event_type':'EXIT_EXECUTED','symbol':symbol})
    
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "EXIT_EXECUTED",
        "symbol": symbol,
        "reason": reason,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "amount": amount,
        "pnl_percentage": pnl_percentage,
        "profit_usdt": profit_usdt,
        "order_id": order_id,
        "duration_s": duration_s,
        **kwargs
    }))
    
    # Pretty console output with TP/SL indicators
    if ENABLE_PRETTY_TRADE_LOGS:
        
        if reason.lower() in ["tp", "take_profit"]:
            icon = "🟢" if USE_ANSI_COLORS else "[TP]"
            color = "\033[92m" if USE_ANSI_COLORS else ""  # Green
            reason_text = "TP"
        elif reason.lower() in ["sl", "stop_loss"]:
            icon = "🔴" if USE_ANSI_COLORS else "[SL]"
            color = "\033[91m" if USE_ANSI_COLORS else ""  # Red
            reason_text = "SL"
        else:
            icon = "✅" if USE_ANSI_COLORS else "[EXIT]"
            color = "\033[93m" if USE_ANSI_COLORS else ""  # Yellow
            reason_text = reason.upper()
        
        reset = "\033[0m" if USE_ANSI_COLORS else ""
        pnl_pct = pnl_percentage * 100  # Convert to percentage
        msg = f"{color}{icon} {reason_text} {symbol}: Entry={entry_price:.8f} → Exit={exit_price:.8f} | P&L={pnl_pct:+.2f}% | {profit_usdt:+.2f} USDT{reset}"
        _console_info("EXIT_EXECUTED", msg)
    
    # Audit trail
    log_audit_event("EXIT_EXECUTED", {
        "symbol": symbol,
        "reason": reason,
        "entry_price": kwargs.get("entry_price"),
        "exit_price": kwargs.get("exit_price"),
        "amount": kwargs.get("amount"),
        "pnl_percentage": kwargs.get("pnl_percentage"),
        "profit_usdt": kwargs.get("profit_usdt"),
        "order_id": kwargs.get("order_id"),
        "duration_s": kwargs.get("duration_s")
    })
    
    # --- Telegram Exit Alert ---
    try:
        from telegram_notify import tg
        tg.notify_exit(
            symbol=symbol,
            reason=str(reason),
            entry_price=float(entry_price or 0.0),
            exit_price=float(exit_price or 0.0),
            amount=float(amount or 0.0),
            pnl_percentage=float(pnl_percentage or 0.0),
            profit_usdt=float(profit_usdt or 0.0),
            duration_s=float(duration_s or 0.0),
            order_id=str(order_id or ""),
            exit_type=str(kwargs.get("exit_type","") or ""),
            order_type=str(kwargs.get("order_type","") or "")
        )
    except Exception:
        pass

# =============================================================================
# MISSING FUNCTIONS FOR COMPATIBILITY
# =============================================================================
def log_drop_trigger_minute(symbol: str, **ctx):
    """Schreibt 1 Zeile pro Symbol/Minute mit allen DropTrigger-Werten."""
    from config import LOG_SCHEMA_VERSION, RUN_ID
    payload = {
        "ts": _utc_iso(),
        "schema": LOG_SCHEMA_VERSION,
        "run_id": RUN_ID,
        "event_type": "DROP_TRIGGER_MINUTE",
        "symbol": symbol,
        "context": ctx or {}
    }
    logging.getLogger("drop").info(json.dumps(payload, ensure_ascii=False, default=_json_default))

def log_event(event_type: str, *,
              level: str = "INFO",
              symbol: str = None,
              message: str = None,
              context: dict = None,
              **kwargs):
    """
    Log structured event - CRASH-SAFE version.
    NEVER allows logger failures to crash the application.
    """
    try:
        from config import LOG_SCHEMA_VERSION
        payload = {
            "ts": _utc_iso(),
            "schema": LOG_SCHEMA_VERSION,
            "event_type": event_type,
            "symbol": symbol,
            "message": message,
            "context": context or {}
        }
        # Add any extra kwargs to context
        if kwargs:
            payload["context"].update(kwargs)

        logger = logging.getLogger("events")
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.log(log_level, json.dumps(payload, ensure_ascii=False, default=_json_default))

    except Exception as e:
        # CRITICAL: Logger must NEVER crash the application
        try:
            # Minimal fallback logging to console
            timestamp = datetime.now().isoformat()
            print(f"[{timestamp}] LOGGER_ERROR: {str(e)[:200]} (event_type: {event_type})")

            # Try to log the error through basic logger (last resort)
            try:
                error_logger = logging.getLogger("logger_errors")
                error_logger.error(f"log_event failed: {e}", extra={
                    "original_event_type": event_type,
                    "error": str(e)[:200]
                })
            except:
                # If even basic logging fails, continue silently
                pass
        except:
            # Ultimate fallback - continue execution without logging
            pass

def log_audit_event(event_type: str, details: dict, level: str = "INFO"):
    """Log audit event with immutable trail - CRASH-SAFE version"""
    try:
        from config import LOG_SCHEMA_VERSION, RUN_ID
        payload = {
            "ts_utc": _utc_iso(),
            "schema": LOG_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "event_type": event_type,
            "details": details or {},
        }
        logging.getLogger("audit").log(
            getattr(logging, level.upper(), logging.INFO),
            json.dumps(payload, ensure_ascii=False, default=_json_default)
        )
    except Exception as e:
        # CRITICAL: Audit logger must NEVER crash the application
        try:
            timestamp = datetime.now().isoformat()
            print(f"[{timestamp}] AUDIT_LOGGER_ERROR: {str(e)[:200]} (event_type: {event_type})")
        except:
            # Silent continuation if even console output fails
            pass

def get_run_summary():
    """Get run summary (stub for compatibility)"""
    return {
        "start_time": _utc_iso(),
        "events_logged": 0,
        "metrics_logged": 0
    }

def config_snapshot(config_dict: dict):
    """Log configuration snapshot"""
    payload = {
        "ts": _utc_iso(),
        "event": "CONFIG_SNAPSHOT",
        "config": config_dict
    }
    logging.getLogger("events").info(
        json.dumps(payload, ensure_ascii=False, default=_json_default)
    )

def session_start(trigger: str):
    """Log session start"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "SESSION_START",
        "trigger": trigger
    }))

def session_end(trigger: str = "manual", summary: dict = None):
    """Log session end"""
    logging.getLogger("events").info(json.dumps({
        "ts": _utc_iso(),
        "event": "SESSION_END",
        "trigger": trigger,
        "summary": summary or {}
    }))

def heartbeat(**kwargs):
    """Log heartbeat"""
    log_metric("HEARTBEAT", 1, kwargs)

# =============================================================================
# SNAPSHOT WRITER (Parquet mit Fallback)
# =============================================================================
import threading
from typing import Iterable, List, Dict
from pathlib import Path

try:
    import pandas as _pd
    _HAS_PQ = True
except Exception:
    _pd, _HAS_PQ = None, False

class SnapshotWriter:
    """Puffert Marktsnapshots (bid/ask/last/indicators) und schreibt periodisch in EINE Parquet-Datei pro Bot-Run."""
    def __init__(self, path: str, flush_every: int = 500):
        self.path = path
        self.flush_every = flush_every
        self._buf: List[Dict] = []
        self._lock = threading.Lock()
        self._wrote_header = False
        self._all_snapshots: List[Dict] = []  # Sammelt alle Snapshots für den Bot-Run
        self._session_file = None  # Eine Datei pro Bot-Session
        self._csv_mode = False  # Falls Parquet nicht verfügbar

    def write(self, rows: Iterable[Dict]):
        from config import RUN_ID
        with self._lock:
            for r in rows:
                # Pflichtfelder sicherstellen
                r.setdefault("timestamp", _utc_iso())
                r.setdefault("run_id", RUN_ID)
                self._buf.append(r)
            if len(self._buf) >= self.flush_every:
                self.flush()

    def flush(self):
        if not self._buf:
            return
        rows = self._buf
        self._buf = []
        
        try:
            from config import RUN_TIMESTAMP_LOCAL
            
            # Zielverzeichnis aus self.path ableiten
            p = Path(self.path)
            out_dir = p if p.is_dir() else p.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # Eine Datei pro Bot-Session (wird nur beim ersten Mal gesetzt)
            if not self._session_file:
                self._session_file = out_dir / f"market_snapshots_{RUN_TIMESTAMP_LOCAL}.parquet"
                # Bei CSV-Mode direkt CSV-Endung setzen
                if not _HAS_PQ:
                    self._session_file = self._session_file.with_suffix('.csv')
                    self._csv_mode = True
            
            # Alle Snapshots sammeln
            self._all_snapshots.extend(rows)
            
            if _HAS_PQ and not self._csv_mode:
                try:
                    # Alle bisherigen Snapshots in EINE Datei schreiben (überschreibt vorherige Version)
                    df_all = _pd.DataFrame(self._all_snapshots)
                    df_all.to_parquet(self._session_file, index=False, engine="pyarrow", compression="zstd")
                    
                    log_event("SNAPSHOT_APPENDED", level="DEBUG",
                             context={"new_rows": len(rows), "total_rows": len(self._all_snapshots), 
                                    "path": str(self._session_file)})
                    log_metric("SNAPSHOT_FLUSH_OK", len(rows))
                    
                except Exception as parquet_error:
                    # Bei Fehler auf CSV wechseln
                    log_event("SNAPSHOT_PARQUET_ERROR", level="ERROR",
                             message=f"{type(parquet_error).__name__}: {parquet_error}",
                             context={"switching_to_csv": True})
                    self._csv_mode = True
                    self._session_file = self._session_file.with_suffix('.csv')
            
            # CSV-Mode (Fallback oder wenn Pandas nicht verfügbar)
            if self._csv_mode or not _HAS_PQ:
                try:
                    # CSV append mode
                    write_header = not self._session_file.exists() or self._session_file.stat().st_size == 0
                    with self._session_file.open('a', encoding="utf-8") as f:
                        if write_header and rows:
                            f.write(",".join(rows[0].keys()) + "\n")
                        for r in rows:
                            f.write(",".join(str(r.get(k, "")) for k in rows[0].keys()) + "\n")
                    log_event("SNAPSHOT_CSV_APPENDED", level="DEBUG", 
                             context={"rows": len(rows), "path": str(self._session_file)})
                except Exception as csv_error:
                    log_event("SNAPSHOT_CSV_ERROR", level="ERROR",
                             message=f"{type(csv_error).__name__}: {csv_error}",
                                 context={"rows_lost": len(rows)})
                
        except Exception as e:
            log_event("SNAPSHOT_FLUSH_ERROR", level="ERROR", 
                     message=f"{type(e).__name__}: {e}",
                     context={"path": self.path, "rows_lost": len(rows)})
    
    def final_flush(self):
        """Flush beim Beenden des Bots aufrufen, um alle verbleibenden Daten zu schreiben"""
        with self._lock:
            if self._buf:
                self.flush()
            if self._all_snapshots and self._session_file:
                log_event("SNAPSHOT_FINAL_FLUSH", level="INFO",
                         context={"total_snapshots": len(self._all_snapshots),
                                 "file": str(self._session_file),
                                 "size_mb": self._session_file.stat().st_size / 1024 / 1024 if self._session_file.exists() else 0})

# globale Instanz (von engine initialisiert)
_snapshot_writer = None

def init_snapshot_writer(path: str):
    """Initialize snapshot writer"""
    global _snapshot_writer
    _snapshot_writer = SnapshotWriter(path)
    log_event("SNAPSHOT_WRITER_INIT", message=f"Snapshot writer initialized: {path}")

def write_snapshot(row: dict):
    """Einzelnen Snapshot bequem wegschreiben (engine ruft das je Tick/Symbol)."""
    from config import WRITE_SNAPSHOTS
    if not WRITE_SNAPSHOTS or _snapshot_writer is None:
        return
    try:
        _snapshot_writer.write([row])
    except Exception as e:
        log_event("SNAPSHOT_WRITE_ERROR", level="ERROR", message=str(e))

def flush_snapshots():
    """Flush Snapshot-Buffer (beim Shutdown)"""
    if _snapshot_writer:
        _snapshot_writer.flush()


# =============================================================================
# ADDITIONAL COMPATIBILITY FUNCTIONS
# =============================================================================
def market_snapshot_data(**kwargs):
    """Log market snapshot data"""
    log_event("MARKET_SNAPSHOT", context=kwargs)

def market_snapshot(**kwargs):
    """Log market snapshot"""
    log_event("MARKET_SNAPSHOT", context=kwargs)

def guard_eval(symbol: str, guards: list):
    """Log guard evaluation"""
    log_event("GUARD_EVAL", symbol=symbol, context={"guards": guards})

def decision_eval(symbol: str, decision: str, context: dict = None):
    """Log decision evaluation"""
    log_event("DECISION_EVAL", symbol=symbol, context={"decision": decision, **(context or {})})

def exit_executed(symbol: str, reason: str, **kwargs):
    """Log exit execution"""
    log_event("EXIT_EXECUTED", symbol=symbol, context={"reason": reason, **kwargs})

def log_exception(exc_type: str, exc: Exception, context: dict = None):
    """Log exception"""
    log_event(exc_type, level="ERROR", message=str(exc), context=context)

def trailing_activated(symbol: str, **kwargs):
    """Log trailing stop activation"""
    log_event("TRAILING_ACTIVATED", symbol=symbol, context=kwargs)

def guard_block(symbol: str, guard_type: str, ctx=None, **kwargs):
    """Log guard block - tolerant to both dict and keyword arguments"""
    payload = {"guard_type": guard_type}
    if isinstance(ctx, dict):
        payload.update(ctx)
    payload.update(kwargs)
    log_event("GUARD_BLOCK", symbol=symbol, context=payload)

def guard_pass(symbol: str, guard_type: str, **kwargs):
    """Log guard pass - less noise, only DEBUG level"""
    log_event("GUARD_PASS", symbol=symbol, level="DEBUG", 
              context={"guard_type": guard_type, **kwargs})

def trailing_bump(symbol: str, **kwargs):
    """Log trailing stop bump"""
    log_event("TRAILING_BUMP", symbol=symbol, context=kwargs)

def trailing_hit(symbol: str, **kwargs):
    """Log trailing stop hit"""
    log_event("TRAILING_HIT", symbol=symbol, context=kwargs)

def sl_breakeven_bump(symbol: str, **kwargs):
    """Log stop loss breakeven bump"""
    log_event("SL_BREAKEVEN_BUMP", symbol=symbol, context=kwargs)

def tp_bump(symbol: str, **kwargs):
    """Log take profit bump"""
    log_event("TP_BUMP", symbol=symbol, context=kwargs)

def sl_trail_update(symbol: str, **kwargs):
    """Log stop loss trail update"""
    log_event("SL_TRAIL_UPDATE", symbol=symbol, context=kwargs)

def tp_trail_update(symbol: str, **kwargs):
    """Log take profit trail update"""
    log_event("TP_TRAIL_UPDATE", symbol=symbol, context=kwargs)

def metric_timer(name: str, labels: dict = None):
    """Context manager for timing operations (stub)"""
    from contextlib import contextmanager
    @contextmanager
    def timer():
        import time
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            log_metric(name, duration * 1000, labels)
    return timer()

# ---------------------------------------------------------------------------
# Decision Trace Helpers (neue Funktionen)
# ---------------------------------------------------------------------------
import uuid
from typing import Optional
try:
    from config import DECISION_LOG_PASS_LEVEL, DECISION_LOG_BLOCK_LEVEL
except Exception:
    DECISION_LOG_PASS_LEVEL, DECISION_LOG_BLOCK_LEVEL = "DEBUG", "INFO"

def new_decision_id() -> str:
    return uuid.uuid4().hex[:10]

def decision_start(decision_id: str, *, symbol: str, side: str,
                   strategy: Optional[str] = None, price: float = None,
                   quote: float = None, ctx: dict = None):
    log_event("DECISION_START", symbol=symbol, context={
        "decision_id": decision_id, "side": side, "strategy": strategy,
        "price": price, "quote": quote, **(ctx or {})
    })

def decision_gate(decision_id: str, *, symbol: str, side: str,
                  gate: str, passed: bool, reason: str = None,
                  values: dict = None, thresholds: dict = None,
                  level: str = None):
    lvl = level or (DECISION_LOG_PASS_LEVEL if passed else DECISION_LOG_BLOCK_LEVEL)
    log_event("DECISION_GATE_PASS" if passed else "DECISION_GATE_BLOCK",
              level=lvl, symbol=symbol, context={
                  "decision_id": decision_id, "side": side, "gate": gate,
                  "passed": passed, "reason": reason,
                  "values": values or {}, "thresholds": thresholds or {}
              })

def decision_end(decision_id: str, *, symbol: str, side: str,
                 outcome: str, reason: str = None, order_id: str = None, ctx: dict = None):
    log_event("DECISION_END", symbol=symbol, context={
        "decision_id": decision_id, "side": side, "outcome": outcome,
        "reason": reason, "order_id": order_id, **(ctx or {})
    })