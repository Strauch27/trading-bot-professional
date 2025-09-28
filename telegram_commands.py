"""
Telegram Command Server für Trading Bot
Robuster Command Router mit Auth-Checks und Confirmation-Flow
"""

import threading
import time
import json
import urllib.request
import urllib.error
import os
from adapters.retry import with_backoff
import glob
import math
import shutil
import locale
import html
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from telegram_notify import tg
from telegram_service_adapter import TelegramServiceAdapter
import re
import logging
import tempfile
logger = logging.getLogger(__name__)

# Thread-safe global references
_engine = None
_service_adapter = None
_command_thread = None
_stop_flag = False
_pending_conf = {}  # (chat_id, cmd) -> expiry_ts
_global_lock = threading.RLock()  # Reentrant lock for nested calls

# Network error types for retry
NETWORK_ERRORS = (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError, OSError)

@with_backoff(max_attempts=3, base_delay=0.3, total_time_cap_s=3.5, retry_on=NETWORK_ERRORS)
def _http_get(url, timeout_s=2.0):
    """Timeout-sichere HTTP GET-Anfrage mit Backoff-Mechanismus"""
    return urllib.request.urlopen(url, timeout=timeout_s).read()

# Command registry
COMMANDS = {}

# ------- Enhanced Error Handling -------
class TelegramCommandError(Exception):
    """Base exception for telegram command errors"""
    pass

class ValidationError(TelegramCommandError):
    """Parameter validation error"""
    pass

class ConfigurationError(TelegramCommandError):
    """Configuration management error"""
    pass

def _safe_execute(func, *args, error_msg="Operation failed", **kwargs):
    """Thread-safe wrapper for command execution with error handling"""
    with _global_lock:
        try:
            return func(*args, **kwargs)
        except TelegramCommandError:
            raise
        except Exception as e:
            logger.error(f"{error_msg}: {str(e)}", exc_info=True)
            raise TelegramCommandError(f"{error_msg}: {str(e)}")

# ------- Enhanced Parameter-Helper -------
def _parse_threshold(s: str, param_type: str = None) -> float:
    """
    Akzeptiert Faktor (1.006/0.995/0.98) oder Prozent (+0.6%/-0.5%/-2%).
    Enhanced mit Locale-Awareness und besserer Validierung.
    """
    if not s or not isinstance(s, str):
        raise ValidationError("Parameter darf nicht leer sein")
    
    s = s.strip().lower()
    
    try:
        if s.endswith("%"):
            # Remove % and handle locale-specific decimal separators
            val_str = s[:-1].strip()
            # Try both comma and dot as decimal separator
            if "," in val_str and "." not in val_str:
                val = float(val_str.replace(",", "."))
            else:
                val = float(val_str)
            return 1.0 + val / 100.0
        else:
            # Handle direct factor values with locale awareness
            if "," in s and "." not in s:
                return float(s.replace(",", "."))
            return float(s)
    except ValueError as e:
        raise ValidationError(f"Ungültiges Zahlenformat '{s}': {str(e)}")

def _validate_threshold(value: float, param_type: str) -> float:
    """Enhanced threshold validation with proper ranges"""
    if not isinstance(value, (int, float)):
        raise ValidationError(f"Parameter muss eine Zahl sein, erhalten: {type(value)}")
    
    value = float(value)
    
    # Enhanced range validation
    if param_type == "tp":
        if not (1.0001 <= value <= 1.10):  # Max 10% TP
            raise ValidationError(f"Take Profit außerhalb erlaubtem Bereich [1.0001, 1.10]: {value:.6f}")
    elif param_type == "sl":
        if not (0.85 <= value < 1.0):  # Max 15% SL
            raise ValidationError(f"Stop Loss außerhalb erlaubtem Bereich [0.85, 0.9999]: {value:.6f}")
    elif param_type == "dt":
        if not (0.85 <= value < 1.0):  # Max 15% Drop Trigger
            raise ValidationError(f"Drop Trigger außerhalb erlaubtem Bereich [0.85, 0.9999]: {value:.6f}")
    else:
        raise ValidationError(f"Unbekannter Parameter-Typ: {param_type}")
    
    return value


def _pct_str_from_factor(kind: str, f: float) -> str:
    if kind == "tp":
        return f"+{(f-1.0)*100:.2f}%"
    else:  # sl/dt
        return f"{(f-1.0)*100:.2f}%"

def register_command(name: str, handler, help_text: str):
    """Register a command handler"""
    COMMANDS[name] = {"fn": handler, "help": help_text}

def _is_authorized(chat_id: str) -> bool:
    """Check if chat_id is authorized"""
    return tg.is_authorized(chat_id)

def _reply(cid: str, text: str, escape: bool = False):
    """Reply to a specific chat

    Args:
        escape: If False, text contains HTML formatting. If True, text is plain.
    """
    # Try to send to the specific chat - NO fallback to prevent privacy leaks
    if not tg.send_to(cid, text, escape=escape):
        logger.warning(f"Reply to chat {cid} failed; NOT falling back to default")

def _send_chunked(cid: str, text: str, escape: bool = False, limit: int = 3900):
    """Sendet lange Antworten in Chunks (Telegram Hard-Limit ~4096)"""
    lines = text.split("\n")
    buf = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > limit:
            tg.send_to(cid, "\n".join(buf), escape=escape)
            buf, size = [], 0
        buf.append(ln); size += len(ln) + 1
    if buf: tg.send_to(cid, "\n".join(buf), escape=escape)

def _human_status() -> str:
    """Get human-readable status"""
    if not _service_adapter:
        return "⚠️ Service nicht verfügbar."

    try:
        # Get portfolio summary
        portfolio_summary = _service_adapter.get_portfolio_summary()

        # Get BTC info
        btc_info = _service_adapter.get_btc_info()

        # Get guard status
        guard_status = _service_adapter.get_guard_status()

        lines = [
            "<b>📊 STATUS</b>",
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Budget: {portfolio_summary.get('budget_usdt','?')} USDT | Slots frei: {portfolio_summary.get('available_slots','?')}",
            f"Open Orders: {portfolio_summary.get('open_buy_orders_count','?')} | Held: {portfolio_summary.get('held_assets_count','?')}",
            f"BTC: {btc_info.get('price_str','?')} ({btc_info.get('change_str','±0.00%')})"
        ]

        # Add guard status if available
        if guard_status:
            guard_icon = "🛡️" if guard_status.get('active', False) else "⚠️"
            guard_text = guard_status.get('status', 'Unknown')
            lines.append(f"Guards: {guard_icon} {guard_text}")

        return "\n".join(lines)

    except Exception as e:
        return f"⚠️ Status-Fehler: {str(e)}"

# ---------- Menü-Bausteine ----------
def _positions_text() -> str:
    """Erzeugt eine kompakte Positionsliste als HTML."""
    if not _service_adapter:
        return "📦 <b>Positions</b>\nEngine nicht bereit."
    try:
        positions = _service_adapter.get_open_positions()
        pos = {}
        for position in positions:
            pos[position.symbol] = {
                "amount": position.quantity,
                "buy_price": position.buy_price,
                "current_price": position.current_price,
                "unrealized_pnl": position.unrealized_pnl
            }
    except:
        pos = {}
    if not pos:
        return "📦 <b>Positions</b>\nKeine offenen Positionen."
    lines = ["📦 <b>Positions</b>"]
    for sym, d in pos.items():
        amt = float(d.get("amount") or 0.0)
        px  = float(d.get("buy_price") or d.get("current_price") or 0.0)
        pnl = float(d.get("unrealized_pnl") or 0.0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        lines.append(f"• <code>{sym}</code> — {amt:.8f} @ ${px:.6f} ({pnl_str})")
    return "\n".join(lines)

def _menu_text() -> str:
    """Zeigt aktuelle Parameter + Kurzanleitung"""
    return (
        "🛠 <b>Konfigurations-Menü</b>\n"
        "Wähle unten eine Aktion. Werte wirken sofort; <i>Save Config</i> macht sie dauerhaft.\n\n"
        + _format_params_msg()
    )

def _kb_main():
    return {
        "inline_keyboard": [
            [{"text": "📊 Status", "callback_data": "MENU_STATUS"},
             {"text": "⚙️ Params", "callback_data": "MENU_PARAMS"},
             {"text": "📦 Positions", "callback_data": "MENU_POS"}],
            [{"text": "📉 Top Drops", "callback_data": "MENU_TOP_DROPS"},
             {"text": "🎯 Signale", "callback_data": "MENU_SIGNALS"}],
            [{"text": "🎯 TP", "callback_data": "MENU_TP"},
             {"text": "🛑 SL", "callback_data": "MENU_SL"},
             {"text": "📉 DT", "callback_data": "MENU_DT"}],
            [{"text": "🔄 Retarget TP", "callback_data": "RETARGET:tp"},
             {"text": "🔄 Retarget SL", "callback_data": "RETARGET:sl"},
             {"text": "🔄 Retarget Aktiver", "callback_data": "RETARGET:both"}],
            [{"text": "💾 Save Config", "callback_data": "SAVE_CONFIG"},
             {"text": "🚨 Panic Sell", "callback_data": "CONFIRM:PANIC"},
             {"text": "🛑 Stop", "callback_data": "CONFIRM:STOP"}],
            [{"text": "🔁 Refresh", "callback_data": "MENU_MAIN"}]
        ]
    }

def _kb_tp():
    # absolute TP-Levels in % (positiv)
    row1 = [{"text": x, "callback_data": f"SET_TP={x}"} for x in ("+0.3%", "+0.6%", "+1.0%")]
    row2 = [{"text": x, "callback_data": f"SET_TP={x}"} for x in ("+1.5%", "+2.0%")]
    return {"inline_keyboard": [row1, row2, [{"text":"⬅️ Zurück", "callback_data":"MENU_MAIN"}]]}

def _kb_sl():
    # absolute SL-Levels in % (negativ)
    row1 = [{"text": x, "callback_data": f"SET_SL={x}"} for x in ("-0.3%", "-0.5%", "-1.0%")]
    row2 = [{"text": x, "callback_data": f"SET_SL={x}"} for x in ("-1.5%", "-2.0%", "-3.5%")]
    return {"inline_keyboard": [row1, row2, [{"text":"⬅️ Zurück", "callback_data":"MENU_MAIN"}]]}

def _kb_dt():
    # Drop Trigger Presets (negativ)
    row1 = [{"text": x, "callback_data": f"SET_DT={x}"} for x in ("-1.0%", "-1.5%", "-2.0%")]
    row2 = [{"text": x, "callback_data": f"SET_DT={x}"} for x in ("-3.0%", "-5.0%", "-8.5%")]
    return {"inline_keyboard": [row1, row2, [{"text":"⬅️ Zurück", "callback_data":"MENU_MAIN"}]]}

def _percent_to_factor(s: str) -> float:
    # "+0.6%" -> 1.006 ; "-0.5%" -> 0.995
    s = s.strip().replace(",", ".")
    assert s.endswith("%")
    return 1.0 + float(s[:-1]) / 100.0

def _create_config_backup(config_path: str) -> str:
    """Create timestamped backup of config file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{config_path}.backup_{timestamp}"
    try:
        shutil.copy2(config_path, backup_path)
        return backup_path
    except Exception as e:
        logger.warning(f"Failed to create config backup: {e}")
        return None

def _persist_thresholds_atomic() -> bool:
    """
    Enhanced atomic config persistence with backup and validation.
    Returns: bool indicating success
    """
    try:
        import config as C
        config_path = C.__file__
        
        # Create backup before modification
        backup_path = _create_config_backup(config_path)
        
        # Read current config
        with open(config_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        # Prepare new values with validation
        new_values = {
            'TAKE_PROFIT_THRESHOLD': float(C.TAKE_PROFIT_THRESHOLD),
            'STOP_LOSS_THRESHOLD': float(C.STOP_LOSS_THRESHOLD),
            'DROP_TRIGGER_VALUE': float(C.DROP_TRIGGER_VALUE)
        }
        
        # Validate ranges before writing
        _validate_threshold(new_values['TAKE_PROFIT_THRESHOLD'], 'tp')
        _validate_threshold(new_values['STOP_LOSS_THRESHOLD'], 'sl')
        _validate_threshold(new_values['DROP_TRIGGER_VALUE'], 'dt')
        
        # Apply regex replacements
        modified_content = original_content
        for param, value in new_values.items():
            pattern = rf"^{param}\s*=\s*[0-9\.\-]+"
            replacement = f"{param} = {value}"
            new_content = re.sub(pattern, replacement, modified_content, count=1, flags=re.MULTILINE)
            
            # Verify replacement worked
            if new_content == modified_content:
                raise ConfigurationError(f"Failed to update {param} in config file")
            modified_content = new_content
        
        # Atomic write with temporary file
        temp_path = config_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(modified_content)
            
            # Atomic move
            if os.name == 'nt':  # Windows
                if os.path.exists(config_path):
                    os.remove(config_path)
            os.rename(temp_path, config_path)
            
            logger.info(f"CONFIG_SAVED thresholds successfully (backup: {backup_path})", 
                       extra={"event_type": "CONFIG_SAVED", "backup_path": backup_path,
                              "values": new_values})
            return True
            
        except Exception as e:
            # Cleanup temp file on error
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ConfigurationError(f"Failed to write config file: {e}")
            
    except Exception as e:
        logger.error(f"Config persistence failed: {e}", exc_info=True)
        raise ConfigurationError(f"Configuration save failed: {e}")

def _on_callback(cid: str, mid: int, cb_id: str, data: str):
    try:
        if data == "MENU_MAIN":
            tg.edit_message(cid, mid, _menu_text(), reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_STATUS":
            # Status kann lang sein - verwende edit_message direkt da es keine chunked Version gibt
            tg.edit_message(cid, mid, _human_status()[:3900], reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_PARAMS":
            tg.edit_message(cid, mid, _format_params_msg(), reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_POS":
            # Positions kann lang sein - kürze für edit_message
            tg.edit_message(cid, mid, _positions_text()[:3900], reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_TP":
            tg.edit_message(cid, mid, "🎯 <b>TP-Presets</b>\nWähle ein Ziel:", reply_markup=_kb_tp(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_SL":
            tg.edit_message(cid, mid, "🛑 <b>SL-Presets</b>\nWähle ein Ziel:", reply_markup=_kb_sl(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_DT":
            tg.edit_message(cid, mid, "📉 <b>DT-Presets</b>\nWähle ein Ziel:", reply_markup=_kb_dt(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_TOP_DROPS":
            drops = _get_top_drops(15)
            message = _format_top_drops_msg(drops)
            tg.edit_message(cid, mid, message, reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "MENU_SIGNALS":
            signals = _get_current_signals()
            message = _format_signals_msg(signals)
            tg.edit_message(cid, mid, message, reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id)
            return

        if data.startswith("RETARGET:"):
            mode = data.split(":",1)[1]
            placed = _service_adapter.retarget_positions(mode=mode) if _service_adapter else 0
            tg.edit_message(cid, mid, f"🔄 Retarget ({mode}) fertig. Geänderte Orders: {placed}\n\n" + _menu_text(),
                            reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id, text=f"Retarget: {mode}")
            return
        
        if data == "SAVE_CONFIG":
            try:
                success = _persist_thresholds_atomic()
                if success:
                    tg.edit_message(cid, mid, "💾 <b>Gespeichert</b> — Änderungen sind nun dauerhaft.\n\n" + _menu_text(),
                                    reply_markup=_kb_main(), escape=False)
                    tg.answer_callback(cb_id, text="Config erfolgreich gespeichert")
                else:
                    tg.answer_callback(cb_id, text="Config-Speicherung fehlgeschlagen", show_alert=True)
            except ConfigurationError as e:
                tg.answer_callback(cb_id, text=f"Config-Fehler: {str(e)}", show_alert=True)
            except Exception as e:
                tg.answer_callback(cb_id, text=f"Unerwarteter Fehler: {str(e)}", show_alert=True)
            return

        if data == "CONFIRM:PANIC":
            # kleine Button-Bestätigung
            kb = {"inline_keyboard":[
                [{"text":"🚨 Ja, alles schließen", "callback_data":"PANIC:DO"},
                 {"text":"↩️ Zurück", "callback_data":"MENU_MAIN"}]
            ]}
            tg.edit_message(cid, mid, "⚠️ <b>Panic Sell</b>\nAlle Positionen werden sofort geschlossen.\nFortfahren?",
                            reply_markup=kb, escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "PANIC:DO":
            # Check confirmation timeout
            with _global_lock:
                exp = _pending_conf.get((cid, "panic"), 0)
                if time.time() > exp:
                    tg.answer_callback(cb_id, text="⌛ Bestätigung abgelaufen", show_alert=True)
                    return
                _pending_conf.pop((cid, "panic"), None)
            try:
                success, message = _execute_panic_sell("PANIC_SELL_MENU")
                if success:
                    tg.edit_message(cid, mid, f"🚨 <b>Panic Sell ausgelöst</b>\n{message}",
                                    reply_markup=_kb_main(), escape=False)
                    tg.answer_callback(cb_id, text="Panic Sell abgeschlossen")
                else:
                    tg.edit_message(cid, mid, f"⚠️ <b>Panic Sell Problem</b>\n{message}",
                                    reply_markup=_kb_main(), escape=False)
                    tg.answer_callback(cb_id, text="Panic Sell teilweise fehlgeschlagen", show_alert=True)
            except TelegramCommandError as e:
                tg.answer_callback(cb_id, text=f"Fehler: {e}", show_alert=True)
            return

        if data == "CONFIRM:STOP":
            kb = {"inline_keyboard":[
                [{"text":"🛑 Ja, beenden", "callback_data":"STOP:DO"},
                 {"text":"↩️ Zurück", "callback_data":"MENU_MAIN"}]
            ]}
            tg.edit_message(cid, mid, "🛑 <b>Bot stoppen?</b>\nDie Trading-Engine fährt sauber herunter.",
                            reply_markup=kb, escape=False)
            tg.answer_callback(cb_id)
            return
        if data == "STOP:DO":
            # Check confirmation timeout
            with _global_lock:
                exp = _pending_conf.get((cid, "stop"), 0)
                if time.time() > exp:
                    tg.answer_callback(cb_id, text="⌛ Bestätigung abgelaufen", show_alert=True)
                    return
                _pending_conf.pop((cid, "stop"), None)
            try:
                if _engine:
                    _engine.request_shutdown(reason="telegram_menu")
                logger.info("BOT_SHUTDOWN_CONFIRMED", extra={"event_type":"BOT_SHUTDOWN_CONFIRMED","by":"menu"})
                tg.edit_message(cid, mid, "🛑 <b>Shutdown angefordert</b>\nDie Engine fährt herunter.",
                                reply_markup=_kb_main(), escape=False)
                tg.answer_callback(cb_id, text="Beende…")
            except Exception as e:
                tg.answer_callback(cb_id, text=f"Fehler: {e}", show_alert=True)
            return

        if data.startswith("SET_TP="):
            val = data.split("=",1)[1]     # z.B. "+0.6%"
            # Log the change
            import config as C
            old_tp = C.TAKE_PROFIT_THRESHOLD
            _apply_runtime_params(tp=_percent_to_factor(val))
            logger.info(
                f"PARAM_SET TP: {old_tp:.6f} -> {_percent_to_factor(val):.6f} ({val})",
                extra={"event_type":"PARAM_SET","param":"tp","old":float(old_tp),"new":float(_percent_to_factor(val))}
            )
            tg.edit_message(cid, mid, "✅ TP übernommen\n\n" + _menu_text(), reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id, text=f"TP {val}")
            return
        if data.startswith("SET_SL="):
            val = data.split("=",1)[1]     # z.B. "-0.5%"
            # Log the change
            import config as C
            old_sl = C.STOP_LOSS_THRESHOLD
            _apply_runtime_params(sl=_percent_to_factor(val))
            logger.info(
                f"PARAM_SET SL: {old_sl:.6f} -> {_percent_to_factor(val):.6f} ({val})",
                extra={"event_type":"PARAM_SET","param":"sl","old":float(old_sl),"new":float(_percent_to_factor(val))}
            )
            tg.edit_message(cid, mid, "✅ SL übernommen\n\n" + _menu_text(), reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id, text=f"SL {val}")
            return
        if data.startswith("SET_DT="):
            val = data.split("=",1)[1]     # z.B. "-2.0%"
            # Log the change
            import config as C
            old_dt = C.DROP_TRIGGER_VALUE
            _apply_runtime_params(dt=_percent_to_factor(val))
            logger.info(
                f"PARAM_SET DT: {old_dt:.6f} -> {_percent_to_factor(val):.6f} ({val})",
                extra={"event_type":"PARAM_SET","param":"dt","old":float(old_dt),"new":float(_percent_to_factor(val))}
            )
            tg.edit_message(cid, mid, "✅ DT übernommen\n\n" + _menu_text(), reply_markup=_kb_main(), escape=False)
            tg.answer_callback(cb_id, text=f"DT {val}")
            return

        tg.answer_callback(cb_id, text="Unbekannte Aktion")
    except Exception as e:
        tg.answer_callback(cb_id, text=f"Fehler: {e}", show_alert=True)

# Signal preview helper functions
def _compute_signal_preview(n: int = 5) -> Tuple[str, str]:
    """
    Liefert (drops_line, next_line) als HTML-Strings.
    drops_line z.B.: '📉 Drops: MKR:-1.9% BERA:-1.2% …'
    next_line  z.B.: '🔔 Next Signal: MKR/USDT (Drop -1.9%, ΔDT +0.8pp)'
    """
    try:
        if not _service_adapter:
            return "", ""

        # Get top drops from the service adapter
        drop_signals = _service_adapter.get_top_drops(limit=n)

        if not drop_signals:
            return "", ""

        # Format drops line
        drops_line = "📉 Drops: " + " ".join(
            f"{signal.symbol.replace('/USDT','')}:{signal.drop_percentage:.1f}%"
            for signal in drop_signals
        )

        # Next signal is the strongest drop
        strongest = drop_signals[0]
        gap_str = f"{strongest.gap_to_trigger:+.1f}pp"
        next_line = f"🔔 Next Signal: <code>{strongest.symbol}</code> (Drop {strongest.drop_percentage:.1f}%, ΔDT {gap_str})"

        return drops_line, next_line

    except Exception:
        return "", ""

# Command handlers
def _cmd_help(cid: str, args: list):
    """Show help message"""
    rows = ["<b>🤖 Befehle</b>"]
    for k, v in sorted(COMMANDS.items()):
        rows.append(f"/{k} – {v['help']}")
    _reply(cid, "\n".join(rows), escape=False)

def _cmd_status(cid: str, args: list):
    """Show bot status"""
    text = _human_status()
    drops_line, next_line = _compute_signal_preview(n=5)
    if drops_line:
        text += "\n" + drops_line
    if next_line:
        text += "\n" + next_line
    _send_chunked(cid, text, escape=False)

def _cmd_positions(cid: str, args: list):
    """Show open positions"""
    if not _service_adapter:
        _reply(cid, "📦 Service nicht verfügbar.")
        return

    try:
        positions = _service_adapter.get_open_positions()

        if not positions:
            _reply(cid, "📦 Keine Positionen.")
            return

        lines = ["<b>📦 Positionen</b>"]

        for pos in positions:
            # Icon based on profit
            icon = "🟢" if pos.unrealized_pnl > 0 else "🔴" if pos.unrealized_pnl < 0 else "⚪"

            lines.append(f"{icon} {pos.symbol}: {pos.quantity:.4f} @ ${pos.buy_price:.6f}")
            lines.append(f"   Current: ${pos.current_price:.6f} | PnL: ${pos.unrealized_pnl:.2f} ({pos.pnl_percentage:+.2f}%)")

    except Exception as e:
        _reply(cid, f"📦 Fehler beim Laden der Positionen: {e}")
        return

    _send_chunked(cid, "\n".join(lines), escape=False)

def _execute_panic_sell(reason: str = "PANIC_SELL_TELEGRAM") -> Tuple[bool, str]:
    """
    Unified panic sell implementation with atomicity.
    Returns: (success: bool, message: str)
    """
    with _global_lock:
        if not _service_adapter:
            return False, "Service nicht verfügbar"

        try:
            logger.info(f"PANIC_SELL_INITIATED via {reason}",
                       extra={"event_type": "PANIC_SELL_INITIATED", "reason": reason})

            success, message = _service_adapter.execute_panic_sell(reason)

            if success:
                logger.info(f"PANIC_SELL_COMPLETED via {reason}",
                           extra={"event_type": "PANIC_SELL_COMPLETED", "reason": reason})
            else:
                logger.warning(f"PANIC_SELL_FAILED via {reason}: {message}",
                              extra={"event_type": "PANIC_SELL_FAILED", "reason": reason})

            return success, message

        except Exception as e:
            logger.error(f"Panic sell execution failed: {e}", exc_info=True)
            return False, f"❌ PANIC SELL ERROR: {str(e)}"

def _cmd_panic_sell(cid: str, args: list):
    """Initiate panic sell with confirmation"""
    with _global_lock:
        _pending_conf[(cid, "panic")] = time.time() + 30
    _reply(cid, "⚠️ <b>Bestätigen?</b>\n\nAntworte innerhalb von 30s mit <code>/confirm panic</code>", escape=False)

def _cmd_confirm(cid: str, args: list):
    """Confirm a pending action"""
    if not args or args[0] != "panic":
        _reply(cid, "ℹ️ Nichts zu bestätigen.")
        return
    
    with _global_lock:
        exp = _pending_conf.get((cid, "panic"), 0)
        if time.time() > exp:
            _reply(cid, "⌛ Bestätigungsfenster abgelaufen.")
            return
        
        # Clear confirmation before execution
        _pending_conf.pop((cid, "panic"), None)
    
    # Execute panic sell
    _reply(cid, "🚨 <b>PANIC SELL WIRD AUSGEFÜHRT</b>", escape=False)
    
    try:
        success, message = _execute_panic_sell("PANIC_SELL_TELEGRAM")
        _reply(cid, f"<b>{message}</b>", escape=False)
    except TelegramCommandError as e:
        _reply(cid, f"❌ <b>PANIC SELL FEHLER:</b> {html.escape(str(e))}", escape=False)

def _cmd_logs(cid: str, args: list):
    """Send latest log file"""
    try:
        # Find latest log file
        log_patterns = [
            "v9/sessions/*/logs/*.log",
            "V9/sessions/*/logs/*.log",
            "sessions/*/logs/*.log",
            "*.log"
        ]
        
        files = []
        for pattern in log_patterns:
            files.extend(glob.glob(pattern))
        
        if files:
            # Sort by modification time and get latest
            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            latest_log = files[0]
            
            # Send document
            if tg.send_document(latest_log, caption="Letztes Log"):
                _reply(cid, f"📄 Log gesendet: {os.path.basename(latest_log)}")
            else:
                _reply(cid, "⚠️ Log-Versand fehlgeschlagen")
        else:
            _reply(cid, "📄 Kein Log gefunden.")
    except Exception as e:
        _reply(cid, f"⚠️ Log-Versand fehlgeschlagen: {str(e)}")

# --- Parameter adjustment helpers ---

def _parse_set_command(text: str) -> Tuple[str, str]:
    """
    Enhanced parsing for /set command with unified logic.
    Accepts: '/set dt -0.5%', '/set dt-0.5%', '/set tp 1.006'
    Returns: (param_name, value_string)
    """
    if not text or not isinstance(text, str):
        raise ValidationError("Kommando darf nicht leer sein")
    
    t = text.strip()
    
    # Enhanced regex pattern for flexible parsing
    pattern = r"^/set\s+(tp|sl|dt)\s*([+\-]?\d+(?:[.,]\d+)?%?)\s*$"
    m = re.match(pattern, t, re.I)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    
    # Fallback parsing with better error messages
    parts = t.split(maxsplit=2)
    if len(parts) < 2:
        raise ValidationError("Syntax: /set tp|sl|dt <wert>  (z.B. /set tp 1.006, /set sl -0.5%)")
    elif len(parts) >= 3:
        param = parts[1].lower().strip()
        if param not in ("tp", "sl", "dt"):
            raise ValidationError(f"Parameter '{param}' unbekannt. Erlaubt: tp, sl, dt")
        return param, parts[2].strip()
    else:
        raise ValidationError("Wert fehlt. Syntax: /set tp|sl|dt <wert>")

def _apply_runtime_params(tp=None, sl=None, dt=None):
    """
    Live patch: config (UPPER + lowercase aliases) and imported module globals.
    Recomputes trailing parameters if needed.
    """
    import importlib, sys
    import config as C
    
    changed = {}
    
    if tp is not None:
        C.TAKE_PROFIT_THRESHOLD = float(tp)
        C.take_profit_threshold = float(tp)
        # Patch already imported module variables
        try:
            import engine as E
            E.take_profit_threshold = float(tp)
        except Exception: pass
        try:
            import utils as U
            U.take_profit_threshold = float(tp)
        except Exception: pass
        try:
            import trading as T
            T.take_profit_threshold = float(tp)
        except Exception: pass
        changed["tp"] = float(tp)
    
    if sl is not None:
        C.STOP_LOSS_THRESHOLD = float(sl)
        C.stop_loss_threshold = float(sl)
        try:
            import engine as E
            E.stop_loss_threshold = float(sl)
        except Exception: pass
        try:
            import utils as U
            U.stop_loss_threshold = float(sl)
        except Exception: pass
        try:
            import trading as T
            T.stop_loss_threshold = float(sl)
        except Exception: pass
        changed["sl"] = float(sl)
    
    if dt is not None:
        C.DROP_TRIGGER_VALUE = float(dt)
        C.drop_trigger_value = float(dt)
        # Patch the imported module-level bindings in engine and utils
        try:
            import engine as E
            E.DROP_TRIGGER_VALUE = float(dt)
            E.drop_trigger_value = float(dt)
        except Exception:
            pass
        try:
            import utils as U
            U.DROP_TRIGGER_VALUE = float(dt)
            U.drop_trigger_value = float(dt)
        except Exception:
            pass
        changed["dt"] = float(dt)
    
    # Recalculate derived trailing parameters if needed
    try:
        if getattr(C, "USE_RELATIVE_TRAILING", False):
            C._derive_trailing_from_relatives()
            # Update aliases in modules
            for name in [
                "TRAILING_STOP_ACTIVATION_PCT", "TRAILING_STOP_DISTANCE_PCT",
                "TRAILING_TP_ACTIVATION_PCT", "TRAILING_TP_STEP_BP", "TRAILING_TP_UNDER_HIGH_BP"
            ]:
                val = getattr(C, name, None)
                if val is not None:
                    try:
                        import engine as E
                        setattr(E, name.lower(), val)
                    except Exception: pass
                    try:
                        import utils as U
                        setattr(U, name.lower(), val)
                    except Exception: pass
    except Exception:
        pass
    
    return changed

def _format_params_msg():
    """Format current parameters for display"""
    try:
        import config as C
        dt_pct = (1.0 - float(C.DROP_TRIGGER_VALUE)) * 100.0
        sl_pct = (1.0 - float(C.STOP_LOSS_THRESHOLD)) * 100.0
        tp_pct = (float(C.TAKE_PROFIT_THRESHOLD) - 1.0) * 100.0
        return (f"⚙️ <b>PARAMS</b>\n"
                f"📉 Drop Trigger: -{dt_pct:.2f}% ({float(C.DROP_TRIGGER_VALUE):.4f})\n"
                f"🛑 Stop Loss:    -{sl_pct:.2f}% ({float(C.STOP_LOSS_THRESHOLD):.4f})\n"
                f"🎯 Take Profit:  +{tp_pct:.2f}% ({float(C.TAKE_PROFIT_THRESHOLD):.4f})")
    except Exception as e:
        return f"⚠️ Params nicht verfügbar: {str(e)}"

def _get_top_drops(limit: int = 10):
    """
    Holt die größten Drops von Drop-Anchors und berechnet potenzielle Signale
    Returns: List of drop data dictionaries for compatibility
    """
    if not _service_adapter:
        return []

    try:
        drop_signals = _service_adapter.get_top_drops(limit=limit)

        # Convert to legacy format for compatibility
        drops = []
        for signal in drop_signals:
            drops.append({
                'symbol': signal.symbol,
                'current_price': signal.current_price,
                'anchor_price': signal.anchor_price,
                'drop_pct': signal.drop_percentage,
                'signal_strength': signal.signal_strength,
                'is_signal': signal.is_signal_ready,
                'is_holding': signal.is_holding,
                'has_buy_order': signal.has_buy_order,
                'anchor_timestamp': signal.anchor_timestamp
            })

        return drops

    except Exception as e:
        logger.error(f"Error getting top drops: {e}")
        return []

def _format_top_drops_msg(drops: list):
    """Format top drops für Telegram display"""
    if not drops:
        return "📊 <b>TOP DROPS</b>\n\n❌ Keine Drop-Daten verfügbar oder keine Drops gefunden."
    
    lines = ["📉 <b>TOP DROPS & SIGNALE</b>\n"]
    
    try:
        import config as C
        trigger_pct = (1.0 - float(C.DROP_TRIGGER_VALUE)) * 100.0
        lines.append(f"🎯 Drop-Trigger: <b>-{trigger_pct:.1f}%</b>\n")
    except:
        pass
    
    signal_count = 0
    for i, drop in enumerate(drops[:10], 1):
        symbol = drop['symbol']
        drop_pct = drop['drop_pct']
        signal_strength = drop['signal_strength']
        is_signal = drop['is_signal']
        is_holding = drop['is_holding']
        has_buy_order = drop['has_buy_order']
        
        # Icon basierend auf Status
        if is_holding:
            status_icon = "💰"  # Already holding
        elif has_buy_order:
            status_icon = "🔄"  # Buy order pending
        elif is_signal:
            status_icon = "🔥"  # Strong signal
        elif signal_strength >= 0.7:
            status_icon = "⚡"  # Medium signal
        else:
            status_icon = "📉"  # Just a drop
            
        # Format signal strength
        strength_bar = "🟩" * min(int(signal_strength * 5), 5)
        if len(strength_bar) < 5:
            strength_bar += "⬜" * (5 - len(strength_bar))
            
        lines.append(
            f"{i:2d}. {status_icon} <b>{symbol}</b> {drop_pct:+.1f}%\n"
            f"    💪 {strength_bar} {signal_strength:.1f}x\n"
            f"    💰 {drop['current_price']:.6f} ← {drop['anchor_price']:.6f}"
        )
        
        if is_signal:
            signal_count += 1
    
    if signal_count > 0:
        lines.insert(-len(drops), f"\n🎯 <b>{signal_count} AKTIVE SIGNALE</b>\n")
    
    lines.append(f"\n🕐 Letzte Aktualisierung: {datetime.now().strftime('%H:%M:%S')}")
    return "\n".join(lines)

def _get_current_signals():
    """Holt aktuelle Buy-Signale basierend auf Drop-Triggern"""
    if not _engine:
        return []
        
    try:
        drops = _get_top_drops(20)  # Mehr Daten für Signal-Analyse
        signals = []
        
        for drop in drops:
            if drop['is_signal'] and not drop['is_holding'] and not drop['has_buy_order']:
                # Add additional signal metrics
                signal = drop.copy()
                signal['potential_profit'] = abs(drop['drop_pct']) * 0.5  # Geschätzte Erholung
                signal['risk_score'] = min(drop['signal_strength'], 3.0)  # Risk-basiert auf Stärke
                signals.append(signal)
        
        # Sortiere nach Signal-Stärke
        signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        return signals[:8]  # Top 8 Signale
        
    except Exception as e:
        logger.error(f"Error getting current signals: {e}")
        return []

def _format_signals_msg(signals: list):
    """Format aktuelle Signale für Telegram"""
    if not signals:
        return "🎯 <b>AKTUELLE SIGNALE</b>\n\n✅ Keine aktiven Buy-Signale gefunden.\nAlle Trigger-Bedingungen erfüllt oder bereits investiert."
    
    lines = ["🎯 <b>AKTUELLE BUY-SIGNALE</b>\n"]
    
    for i, signal in enumerate(signals[:8], 1):
        symbol = signal['symbol']
        drop_pct = signal['drop_pct']
        strength = signal['signal_strength']
        potential = signal['potential_profit']
        risk = signal['risk_score']
        
        # Risk-Level
        if risk <= 1.5:
            risk_icon = "🟢"
            risk_text = "LOW"
        elif risk <= 2.5:
            risk_icon = "🟡"
            risk_text = "MED"
        else:
            risk_icon = "🔴"
            risk_text = "HIGH"
            
        lines.append(
            f"{i}. 🔥 <b>{symbol}</b> {drop_pct:+.1f}%\n"
            f"   📈 Potential: +{potential:.1f}% | {risk_icon} {risk_text}\n"
            f"   💪 Stärke: {strength:.1f}x | 💰 {signal['current_price']:.6f}"
        )
    
    lines.append(f"\n💡 Bot kauft automatisch bei weiterem Drop")
    lines.append(f"🕐 Update: {datetime.now().strftime('%H:%M:%S')}")
    
    return "\n".join(lines)

def _cmd_params(cid: str, args: list):
    """Show current trading parameters"""
    _reply(cid, _format_params_msg(), escape=False)

def _cmd_menu(cid: str, args: list):
    """Show configuration menu with buttons"""
    tg.send_to(cid, _menu_text(), reply_markup=_kb_main(), escape=False)

def _cmd_set(cid: str, args: list):
    """Set trading parameters at runtime with enhanced validation"""
    
    try:
        # Enhanced parameter parsing with better error handling
        if len(args) >= 1 and re.match(r'^(tp|sl|dt)[+\-]?\d', args[0], re.I):
            # Format: dt-0.5% or tp+1%
            full_text = f"/set {' '.join(args)}"
            k, v = _parse_set_command(full_text)
        elif len(args) >= 2:
            # Normal format: /set dt -0.5%
            k = args[0].lower().strip()
            if k not in ("tp", "sl", "dt"):
                raise ValidationError(f"Parameter '{k}' unbekannt. Erlaubt: tp, sl, dt")
            v = args[1]
        else:
            raise ValidationError("Syntax: /set tp|sl|dt <wert>\nBeispiele: /set tp +0.6%, /set sl -0.5%, /set dt -2%")
        
        # Get before values for comparison
        import config as C
        before_values = {
            "tp": C.TAKE_PROFIT_THRESHOLD,
            "sl": C.STOP_LOSS_THRESHOLD, 
            "dt": C.DROP_TRIGGER_VALUE
        }
        
        # Parse and validate the new value
        new_val = _parse_threshold(v, k)
        validated_val = _validate_threshold(new_val, k)
        
        # Capture before state for display
        before = _format_params_msg()
        
        # Apply runtime parameters with thread safety
        with _global_lock:
            changed = _apply_runtime_params(
                tp=validated_val if k == "tp" else None,
                sl=validated_val if k == "sl" else None,
                dt=validated_val if k == "dt" else None
            )
        
        # Capture after state for display
        after = _format_params_msg()
        
        # Enhanced logging with more context
        old_val = before_values[k]
        logger.info(
            f"PARAM_SET {k.upper()}: {old_val:.6f} -> {validated_val:.6f} ({_pct_str_from_factor(k, validated_val)})",
            extra={
                "event_type": "PARAM_SET",
                "param": k,
                "old": float(old_val),
                "new": float(validated_val),
                "changed_by": "telegram"
            }
        )
        
        # Enhanced success message
        change_pct = _pct_str_from_factor(k, validated_val)
        param_name = {"tp": "Take Profit", "sl": "Stop Loss", "dt": "Drop Trigger"}[k]
        
        _reply(cid, f"✅ <b>{param_name} erfolgreich geändert</b>\n\n"
                    f"<b>Neuer Wert:</b> {change_pct} ({validated_val:.6f})\n\n"
                    f"<b>Aktuelle Parameter:</b>\n{after}\n\n"
                    f"ℹ️ <i>Änderungen gelten für neue Trades. Nutze /retarget für offene Positionen.</i>", 
               escape=False)
               
    except ValidationError as e:
        _reply(cid, f"❌ <b>Validierungsfehler:</b> {html.escape(str(e))}", escape=False)
    except TelegramCommandError as e:
        _reply(cid, f"❌ <b>Kommando-Fehler:</b> {html.escape(str(e))}", escape=False)
    except Exception as e:
        logger.error(f"Unexpected error in _cmd_set: {e}", exc_info=True)
        _reply(cid, f"❌ <b>Unerwarteter Fehler:</b> {html.escape(str(e))}", escape=False)

def _cmd_retarget(cid: str, args: list):
    """Retarget open positions with new TP/SL values"""
    if not _service_adapter:
        _reply(cid, "⚠️ Service nicht verfügbar.")
        return

    # /retarget          -> both
    # /retarget tp|sl    -> nur TP oder nur SL
    mode = args[0].lower() if args else "both"
    if mode not in ("tp", "sl", "both"):
        mode = "both"

    try:
        updated = _service_adapter.retarget_positions(mode=mode)

        mode_txt = "TP & SL" if mode == "both" else mode.upper()
        _reply(cid, f"🔄 <b>Retarget abgeschlossen</b>\n\n"
                    f"Modus: {mode_txt}\n"
                    f"Geänderte Orders: {updated}", escape=False)
    except Exception as e:
        _reply(cid, f"❌ Retarget-Fehler: {html.escape(str(e))}")

def _cmd_save_config(cid: str, args: list):
    """Save current parameters to config.py with enhanced safety"""
    try:
        success = _persist_thresholds_atomic()
        if success:
            _reply(cid, "💾 <b>Konfiguration gespeichert</b>\n\n"
                        "✅ Parameter in config.py persistiert\n"
                        "✅ Backup erstellt\n"
                        "✅ Wirkt nach Neustart dauerhaft\n\n"
                        "ℹ️ <i>Aktuelle Werte bleiben im Speicher aktiv</i>", escape=False)
        else:
            _reply(cid, "❌ <b>Speicherung fehlgeschlagen</b>\n\n"
                        "Config konnte nicht persistiert werden.", escape=False)
    except ConfigurationError as e:
        _reply(cid, f"❌ <b>Konfigurationsfehler:</b>\n{html.escape(str(e))}", escape=False)
    except Exception as e:
        logger.error(f"Unexpected error in save_config: {e}", exc_info=True)
        _reply(cid, f"❌ <b>Unerwarteter Fehler:</b>\n{html.escape(str(e))}", escape=False)

def _cmd_stop(cid: str, args: list):
    """Stop the bot with confirmation"""
    # Auth check
    if not _is_authorized(cid):
        return _reply(cid, "⛔ Nicht autorisiert.")
    
    # Set confirmation requirement (30s validity)
    _pending_conf[(cid, "stop")] = time.time() + 30
    
    kb = {
        "inline_keyboard": [
            [{"text": "🛑 Ja, beenden", "callback_data": "CONFIRM:STOP"},
             {"text": "❌ Abbrechen", "callback_data": "MENU_MAIN"}]
        ]
    }
    tg.send_to(cid, "⚠️ <b>Bot jetzt beenden?</b>\nDies stoppt die Trading-Engine sauber.",
               reply_markup=kb, escape=False)

def _do_stop(cid: str):
    """Execute the bot shutdown"""
    try:
        if _engine:
            _engine.request_shutdown(reason="telegram")
        tg.send_to(cid, "🛑 <b>Shutdown angefordert</b>\nDie Engine fährt sauber herunter.", escape=False)
        logger.info("BOT_SHUTDOWN_CONFIRMED", extra={"event_type":"BOT_SHUTDOWN_CONFIRMED","by":"telegram"})
    except Exception as e:
        tg.send_to(cid, f"❌ Shutdown-Fehler: {e}")

def _cmd_pnl(cid: str, args: list):
    """Show detailed PnL information"""
    if not _service_adapter:
        _reply(cid, "⚠️ Service nicht verfügbar.")
        return

    try:
        # Get PnL summary from service adapter
        pnl_summary = _service_adapter.get_pnl_summary()

        lines = ["<b>💰 PnL ÜBERSICHT</b>"]
        lines.append("")
        lines.append(f"📈 Unrealisiert: ${pnl_summary['total_unrealized']:.2f}")
        lines.append(f"✅ Realisiert (Session): ${pnl_summary['session_realized']:.2f}")
        lines.append(f"💵 <b>Gesamt: ${pnl_summary['total_pnl']:.2f}</b>")

        # Add position details if any
        positions = _service_adapter.get_open_positions()
        if positions:
            lines.append("")
            lines.append("<b>Offene Positionen:</b>")
            for pos in positions:
                icon = "🟢" if pos.unrealized_pnl > 0 else "🔴" if pos.unrealized_pnl < 0 else "⚪"
                lines.append(f"{icon} {pos.symbol}: ${pos.unrealized_pnl:.2f} ({pos.pnl_percentage:+.2f}%)")

        _reply(cid, "\n".join(lines), escape=False)
    except Exception as e:
        _reply(cid, f"⚠️ PnL-Berechnung fehlgeschlagen: {str(e)}")

def _cmd_signals(cid: str, args: list):
    """Show top drops and next signal"""
    drops_line, next_line = _compute_signal_preview(n=10)
    if not drops_line and not next_line:
        return _reply(cid, "📉 Keine Daten verfügbar.")
    _reply(cid, (drops_line + ("\n" + next_line if next_line else "")), escape=False)

# Register all commands
register_command("help", _cmd_help, "Zeigt diese Hilfe")
register_command("status", _cmd_status, "Gesamtstatus")
register_command("signals", _cmd_signals, "Zeigt Top-Drops & nächstes Signal")
register_command("positions", _cmd_positions, "Offene Positionen")
register_command("pnl", _cmd_pnl, "Detaillierte PnL-Übersicht")
register_command("params", _cmd_params, "Zeigt aktuelle Trading-Parameter")
register_command("set", _cmd_set, "Ändert Trading-Parameter")
register_command("menu", _cmd_menu, "Konfigurations-Menü (Buttons)")
register_command("retarget", _cmd_retarget, "Passt offene Orders an neue Parameter an")
register_command("save_config", _cmd_save_config, "Speichert Parameter in config.py")
register_command("panic_sell", _cmd_panic_sell, "Alle Positionen schließen (mit Bestätigung)")
register_command("panic", _cmd_panic_sell, "Alias für panic_sell")
register_command("stop", _cmd_stop, "Bot beenden (mit Bestätigung)")
register_command("confirm", _cmd_confirm, "Bestätigt eine Aktion")
register_command("logs", _cmd_logs, "Letztes Log senden")

def start_telegram_command_server(engine):
    """Start the Telegram command polling server with thread safety"""
    global _engine, _command_thread, _stop_flag
    
    with _global_lock:
        if not tg.is_configured():
            return False
        
        if _command_thread and _command_thread.is_alive():
            logger.warning("Telegram command server already running")
            return True
            
        _engine = engine
        _service_adapter = TelegramServiceAdapter(engine)
        _stop_flag = False
        
        # Start polling thread (daemon=False for proper cleanup)
        _command_thread = threading.Thread(target=_poll_commands, daemon=False, name="TelegramCommands")
        _command_thread.start()
        
        logger.info("Telegram command server started", extra={"event_type": "TELEGRAM_SERVER_STARTED"})
        print("[OK] Telegram command server started")
        return True

def stop_telegram_command_server():
    """Stop the command server with proper cleanup"""
    global _stop_flag, _command_thread
    
    with _global_lock:
        if not _command_thread:
            return
            
        _stop_flag = True
        logger.info("Stopping Telegram command server", extra={"event_type": "TELEGRAM_SERVER_STOPPING"})
    
    # Join outside of lock to avoid deadlock
    if _command_thread:
        _command_thread.join(timeout=10)
        if _command_thread.is_alive():
            logger.warning("Telegram command thread did not stop gracefully")
        else:
            logger.info("Telegram command server stopped", extra={"event_type": "TELEGRAM_SERVER_STOPPED"})

def _poll_commands():
    """Enhanced polling for Telegram commands with better error handling"""
    offset = 0
    error_count = 0
    max_errors = 10
    
    # Try to load saved offset
    offset_file = Path(".telegram_offset")
    try:
        if offset_file.exists():
            offset = int(offset_file.read_text().strip())
            logger.info(f"Loaded Telegram offset: {offset}")
    except Exception as e:
        logger.warning(f"Could not load Telegram offset: {e}")
    
    logger.info("Starting Telegram command polling loop")
    
    while not _stop_flag:
        try:
            # Heartbeat for telegram polling
            from services.shutdown_coordinator import get_shutdown_coordinator
            co = get_shutdown_coordinator()
            co.beat("telegram_poll")

            # Check stop flag frequently
            if _stop_flag:
                break
                
            # Get updates from Telegram with timeout-safe HTTP
            url = f"https://api.telegram.org/bot{tg.bot_token}/getUpdates"
            params = f"?offset={offset}&timeout=30"

            try:
                raw = _http_get(url + params, timeout_s=35.0)
                data = json.loads(raw.decode())

                if not data.get("ok"):
                    logger.error(f"Telegram API error: {data}")
                    time.sleep(5)
                    continue
            except NETWORK_ERRORS as e:
                logger.warning("TELEGRAM_POLL_ERROR", extra={"err": str(e)[:200]})
                time.sleep(2)
                continue
                
                updates = data.get("result", [])
                if updates:
                    logger.debug(f"Processing {len(updates)} Telegram updates")
                
                for update in updates:
                    if _stop_flag:
                        break
                        
                    try:
                        offset = update["update_id"] + 1
                        
                        # Save offset atomically
                        try:
                            with _global_lock:
                                tmp_file = offset_file.with_suffix(".tmp")
                                tmp_file.write_text(str(offset))
                                tmp_file.replace(offset_file)
                        except Exception as e:
                            logger.warning(f"Failed to save offset: {e}")
                        
                        # Process message commands
                        message = update.get("message", {})
                        if message:
                            co.beat("telegram_process_message")
                            chat = message.get("chat", {}) or {}
                            cid = str(chat.get("id", ""))
                            text = (message.get("text") or "").strip()

                            if text and text.startswith("/"):
                                co.beat("telegram_command_received")
                                # Auth check
                                if not _is_authorized(cid):
                                    _reply(cid, "⛔ Nicht autorisiert.")
                                    continue
                                
                                # Parse command and args
                                parts = text[1:].split()
                                cmd = parts[0].lower() if parts else ""
                                args = parts[1:] if len(parts) > 1 else []
                                
                                logger.info(f"Processing command: /{cmd} from {cid}", 
                                           extra={"event_type": "TELEGRAM_COMMAND", "command": cmd, "chat_id": cid})
                                
                                # Handle command with error handling
                                if cmd in COMMANDS:
                                    try:
                                        COMMANDS[cmd]["fn"](cid, args)
                                    except TelegramCommandError as e:
                                        _reply(cid, f"⚠️ <b>Fehler:</b> {html.escape(str(e))}", escape=False)
                                    except Exception as e:
                                        logger.error(f"Command {cmd} failed: {e}", exc_info=True)
                                        _reply(cid, f"⚠️ <b>Systemfehler:</b> {html.escape(str(e))}", escape=False)
                                else:
                                    logger.info(f"Unknown command: {cmd}")
                                    _cmd_help(cid, [])
                                continue
                        
                        # Process callback queries
                        cbq = update.get("callback_query")
                        if cbq:
                            m = cbq.get("message") or {}
                            mid = m.get("message_id") or 0
                            c_cid = str(m.get("chat", {}).get("id", ""))
                            cb_data = cbq.get("data", "")
                            cbid = cbq.get("id")
                            
                            if not _is_authorized(c_cid):
                                tg.answer_callback(cbid, text="⛔ Nicht autorisiert", show_alert=True)
                                continue
                            
                            logger.debug(f"Processing callback: {cb_data} from {c_cid}")
                            try:
                                _on_callback(c_cid, mid, cbid, cb_data)
                            except Exception as e:
                                logger.error(f"Callback {cb_data} failed: {e}", exc_info=True)
                                tg.answer_callback(cbid, text=f"Fehler: {str(e)}", show_alert=True)
                            continue
                    
                    except Exception as e:
                        logger.error(f"Error processing update {update.get('update_id', 'unknown')}: {e}", exc_info=True)
                        continue
                
                # Reset error counter on successful poll
                error_count = 0
                            
        except Exception as e:
            co.beat("telegram_poll_major_error")
            error_count += 1
            logger.error(f"Command polling error ({error_count}): {e}", exc_info=True)

            # Never permanently stop - use backoff + jitter for robustness
            backoff = min(60, 2 ** min(error_count, 6))
            jitter = backoff * 0.1 * (0.5 + time.time() % 1)  # Add jitter
            sleep_time = backoff + jitter

            logger.warning(f"Retrying after {sleep_time:.1f}s (error #{error_count})")
            time.sleep(sleep_time)
            continue
        finally:
            co.beat("telegram_poll_cycle_end")
            # Nur gelegentlich loggen um nicht zu spammen
            import random
            if random.randint(1, 10) == 1:  # 10% Chance
                logger.info("HEARTBEAT - Telegram poll cycle completed",
                           extra={"event_type": "HEARTBEAT"})
            
        time.sleep(1)  # Small delay between successful polls
    
    logger.info("Telegram command polling stopped")