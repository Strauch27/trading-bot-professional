"""
Telegram Notifier für Trading Bot
Robuste Implementation mit Escaping, Chunking, Rate-Limiting und De-Duplication
"""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


class TelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None, session_id: str = None):
        """Initialize Telegram notifier with token, chat ID and optional session ID"""
        self.bot_token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.allowed_chat_ids = {cid.strip() for cid in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS","").split(",") if cid.strip()}
        self.enabled = str(os.getenv("TELEGRAM_ENABLED", "0")).lower() in ("1", "true", "yes")
        self.silent = os.getenv("TELEGRAM_SILENT","0") == "1"
        self.rate_limit_s = float(os.getenv("TELEGRAM_RATE_LIMIT_S","0.7"))
        self.last_send_time = 0.0
        self._lock = threading.Lock()
        self.dedup_ttl_s = int(os.getenv("TELEGRAM_DEDUP_TTL_S","60"))
        self._dedup = {}  # key -> ts

        # Session ID (from main.py or environment)
        self.session_id = (session_id or os.environ.get("BOT_SESSION_ID") or "").strip()

        # Session stats for summary
        self.session_start = datetime.now()
        self.total_buys = 0
        self.total_sells = 0
        self.total_profit = 0.0

    def is_configured(self) -> bool:
        """Check if Telegram is properly configured"""
        return bool(self.bot_token and (self.chat_id or self.allowed_chat_ids) and self.enabled)

    def set_session_id(self, session_id: str):
        """Set or update the session ID"""
        self.session_id = (session_id or "").strip()

    def _session_footer(self) -> str:
        """Generate session footer for transaction messages"""
        if not self.session_id:
            return ""
        # Full session ID as code block for easy copying
        return f"\n\n🧾 Run: <code>{self.session_id}</code>"

    def is_authorized(self, chat_id: str) -> bool:
        """Check if chat_id is authorized"""
        if self.allowed_chat_ids:
            return str(chat_id) in self.allowed_chat_ids
        return str(chat_id) == self.chat_id if self.chat_id else False

    @staticmethod
    def escape_html(text: str) -> str:
        """Escape HTML special characters"""
        return (text or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def _request(self, method: str, payload: dict, *, max_retries: int = 3):
        """Internal request method with retry logic (shutdown-aware)"""
        if not self.is_configured():
            return None

        # Try to get shutdown coordinator (optional)
        shutdown_coordinator = None
        try:
            from services.shutdown_coordinator import get_shutdown_coordinator
            shutdown_coordinator = get_shutdown_coordinator()
        except ImportError:
            pass

        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        data = json.dumps(payload).encode('utf-8')
        headers = {"Content-Type": "application/json"}
        backoff = 1.0

        for attempt in range(max_retries):
            # Check shutdown before each attempt
            if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
                return None

            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=35) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                body = e.read().decode() if hasattr(e, "read") else ""
                retry_after = None
                try:
                    j = json.loads(body)
                    retry_after = j.get("parameters",{}).get("retry_after")
                except Exception:
                    pass
                if e.code == 429 and retry_after:
                    # Shutdown-aware wait for rate limit
                    if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=float(retry_after) + 0.2):
                        return None
                    elif not shutdown_coordinator:
                        time.sleep(float(retry_after) + 0.2)
                    continue
                if e.code >= 500:
                    # Shutdown-aware backoff for server errors
                    if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=backoff):
                        return None
                    elif not shutdown_coordinator:
                        time.sleep(backoff)
                    backoff *= 1.6
                    continue
                raise
            except Exception:
                # Shutdown-aware backoff for other errors
                if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=backoff):
                    return None
                elif not shutdown_coordinator:
                    time.sleep(backoff)
                backoff *= 1.6
        return None

    def _should_send(self, dedup_key: Optional[str]):
        """Check if message should be sent (rate limiting and deduplication, shutdown-aware)"""
        # Try to get shutdown coordinator (optional)
        shutdown_coordinator = None
        try:
            from services.shutdown_coordinator import get_shutdown_coordinator
            shutdown_coordinator = get_shutdown_coordinator()
        except ImportError:
            pass

        now = time.time()
        with self._lock:
            dt = now - self.last_send_time
            if dt < self.rate_limit_s:
                # Shutdown-aware rate limit wait
                if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=self.rate_limit_s - dt):
                    return False  # Abort send if shutdown requested
                elif not shutdown_coordinator:
                    time.sleep(self.rate_limit_s - dt)
            self.last_send_time = time.time()
            if dedup_key:
                ts = self._dedup.get(dedup_key)
                if ts and now - ts < self.dedup_ttl_s:
                    return False
                self._dedup[dedup_key] = now
                # Clean old entries
                self._dedup = {k: v for k, v in self._dedup.items() if now - v < self.dedup_ttl_s * 2}
        return True

    def send(self, message: str, *, parse_mode: str = "HTML",
             disable_notification: Optional[bool] = None,
             dedup_key: Optional[str] = None,
             escape: bool = True,
             reply_markup: dict | None = None) -> bool:
        """Send message to Telegram with chunking support

        Args:
            escape: If True (default), escapes HTML in message.
                   Set False for pre-formatted HTML templates.
        """
        if not self._should_send(dedup_key):
            return False

        text = self.escape_html(message) if (parse_mode == "HTML" and escape) else (message or "")
        CHUNK = 4000  # TG max ≈4096

        # Base payload
        payload_base = {
            "chat_id": self.chat_id,
            "parse_mode": parse_mode,
            "disable_notification": self.silent if disable_notification is None else disable_notification
        }
        if reply_markup:
            payload_base["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False, separators=(',', ':'))

        # Smart chunking for HTML to avoid breaking tags
        if not escape and parse_mode == "HTML" and len(text) > CHUNK:
            start = 0
            while start < len(text):
                end = min(start + CHUNK, len(text))
                # Try to split at newline to avoid breaking tags
                if end < len(text):
                    cut = text.rfind("\n", start, end)
                    if cut != -1 and cut > start + 200:  # Reasonable cut point
                        end = cut
                chunk = text[start:end] or "."
                payload = dict(payload_base)
                payload["text"] = chunk
                result = self._request("sendMessage", payload)
                if not result:
                    return False
                start = end
        else:
            # Simple send for short messages or escaped text
            for i in range(0, len(text) or 1, CHUNK):
                chunk = text[i:i+CHUNK] or "."
                payload = dict(payload_base)
                payload["text"] = chunk
                result = self._request("sendMessage", payload)
                if not result:
                    return False
        return True

    def send_to(self, chat_id: str, text: str, *, parse_mode: str = "HTML",
                escape: bool = True, reply_markup: dict | None = None) -> bool:
        """Send message to specific chat_id"""
        if not self.is_configured():
            return False

        # Apply escaping if HTML mode and escape=True
        if parse_mode == "HTML" and escape:
            text = self.escape_html(text)

        payload = {
            "chat_id": chat_id,
            "text": text or ".",
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False, separators=(',', ':'))

        result = self._request("sendMessage", payload)
        return bool(result)

    def edit_message(self, chat_id: str, message_id: int, text: str,
                     *, parse_mode: str = "HTML", escape: bool = True,
                     reply_markup: dict | None = None) -> bool:
        """Edit an existing message"""
        if not self.is_configured():
            return False

        if parse_mode == "HTML" and escape:
            text = self.escape_html(text)

        payload = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text or ".",
            "parse_mode": parse_mode
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False, separators=(',', ':'))

        result = self._request("editMessageText", payload)
        return bool(result)

    def answer_callback(self, callback_query_id: str, text: str | None = None, show_alert: bool = False) -> bool:
        """Answer a callback query"""
        if not self.is_configured():
            return False

        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True

        result = self._request("answerCallbackQuery", payload)
        return bool(result)

    def send_document(self, file_path: str, caption: str = None) -> bool:
        """Send document to Telegram"""
        if not (self.is_configured() and os.path.isfile(file_path)):
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        boundary = "----WebKitFormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()

        def _encode(k,v):
            return f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'

        body = _encode("chat_id", self.chat_id)
        if caption:
            body += _encode("caption", caption)

        try:
            with open(file_path, "rb") as f:
                file_content = f.read()

            body = body.encode() + \
                   f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{os.path.basename(file_path)}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + \
                   file_content + f'\r\n--{boundary}--\r\n'.encode()

            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            with urllib.request.urlopen(req, timeout=60):
                return True
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Telegram document send failed: {e}",
                         extra={'event_type': 'TELEGRAM_DOCUMENT_SEND_ERROR', 'error': str(e)})
            return False

    def notify_startup(self, mode: str, budget: float):
        """Send startup notification"""
        self.session_start = datetime.now()
        self.mode = (mode or '').upper()
        icon = "🚀" if mode == "LIVE" else "👀"
        msg = f"{icon} <b>Trading Bot gestartet</b>\n\n"
        msg += f"📊 Modus: <b>{mode}</b>\n"
        msg += f"💰 Budget: <b>${budget:.2f}</b>\n"
        msg += f"🕐 Zeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        self.send(msg, dedup_key=None, escape=False)  # Always send startup

    def notify_shutdown(self, summary: Dict[str, Any] = None):
        """Send shutdown notification with session summary"""
        duration = (datetime.now() - self.session_start).total_seconds() / 3600

        msg = "🛑 <b>Trading Bot beendet</b>\n\n"
        msg += f"⏱ Laufzeit: <b>{duration:.2f}h</b>\n"

        if summary:
            msg += f"📈 Trades: {summary.get('total_trades', 0)}\n"
            msg += f"💵 Realisiert: <b>${summary.get('realized_pnl', 0):.2f}</b>\n"
            msg += f"📊 Unrealisiert: ${summary.get('unrealized_pnl', 0):.2f}\n"
            if summary.get('error_count', 0) > 0:
                msg += f"⚠️ Fehler: {summary.get('error_count', 0)}\n"
        else:
            msg += f"📈 Käufe: {self.total_buys}\n"
            msg += f"📉 Verkäufe: {self.total_sells}\n"
            msg += f"💵 Profit: <b>${self.total_profit:.2f}</b>\n"

        msg += f"🕐 Zeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        msg += self._session_footer()
        self.send(msg, dedup_key=None, escape=False)  # Always send shutdown

    def notify_buy_filled(self, symbol: str, price: float, amount: float, fee: float = 0, order_id: str = "", **extra):
        """Send enriched BUY-filled notification (HTML)"""
        self.total_buys += 1
        notional = float(price) * float(amount)
        maker = bool(extra.get("maker", False))
        tif = str(extra.get("tif") or "").upper()
        order_type = str(extra.get("order_type") or "LIMIT").upper()
        reason = extra.get("reason")
        tp_mult = extra.get("tp_mult")
        sl_mult = extra.get("sl_mult")
        ref_px = extra.get("ref_price")
        extra.get("latency_ms")
        budget_before = extra.get("budget_before")
        budget_after = extra.get("budget_after")
        slot_after = extra.get("slot_after")
        slots_total = extra.get("slots_total")
        client_id = extra.get("client_order_id") or extra.get("clientOrderId") or ""
        fee_rate = (float(fee) / notional) if notional > 0 else 0.0
        breakeven = (notional + float(fee)) / float(amount) if amount else price
        slippage_bps = None
        if ref_px:
            try: slippage_bps = (float(price)/float(ref_px)-1.0)*10000.0
            except Exception: slippage_bps = None
        tp_px = float(price) * float(tp_mult) if tp_mult else None
        sl_px = float(price) * float(sl_mult) if sl_mult else None

        rows = [f"<b>✅ BUY FILLED</b> — {self.escape_html(symbol)}"]
        mode_txt = f" ({self.mode})" if getattr(self, "mode", None) else ""
        rows.append(f"💵 Price: <b>${price:.6f}</b>{mode_txt}")
        rows.append(f"📦 Qty: {amount:.8f}  •  Notional: ${notional:.2f}")
        if fee: rows.append(f"🏷 Fee: ${float(fee):.4f} ({'maker' if maker else 'taker'}, {fee_rate*100:.3f}%)")
        meta = []
        if order_type: meta.append(order_type)
        if tif: meta.append(f"TIF {tif}")
        if client_id: meta.append(f"CID <code>{self.escape_html(str(client_id)[-10:])}</code>")
        if meta: rows.append("⚙️ " + " • ".join(meta))
        if reason: rows.append(f"🧭 Reason: {self.escape_html(str(reason))}")
        if slippage_bps is not None: rows.append(f"↔️ Slippage: {slippage_bps:+.1f} bps vs best ask")
        if tp_px and sl_px:
            tp_pct = (float(tp_mult)-1.0)*100.0
            sl_pct = (float(sl_mult)-1.0)*100.0
            rows.append(f"🎯 TP: ${tp_px:.6f} ({tp_pct:+.2f}%)  •  🛑 SL: ${sl_px:.6f} ({sl_pct:+.2f}%)")
        if budget_before is not None and budget_after is not None:
            rows.append(f"💳 Budget: ${float(budget_before):.2f} → ${float(budget_after):.2f}")
        if slot_after and slots_total:
            rows.append(f"📦 Slot: {int(slot_after)}/{int(slots_total)}")
        rows.append(f"🔎 Breakeven (incl. fee): ${breakeven:.6f}")
        rows.append(f"🆔 Order: <code>{self.escape_html(str(order_id)[-12:])}</code>")
        msg = "\n".join(rows) + self._session_footer()
        self.send(msg, escape=False)

    def notify_sell_filled(self, symbol: str, price: float, amount: float, fee: float = 0, order_id: str = "", **extra):
        """Send enriched SELL-filled notification (HTML)"""
        self.total_sells += 1
        revenue = float(price) * float(amount)
        maker = bool(extra.get("maker", False))
        tif = str(extra.get("tif") or "").upper()
        order_type = str(extra.get("order_type") or "LIMIT").upper()
        client_id = extra.get("client_order_id") or extra.get("clientOrderId") or ""
        fee_rate = (float(fee) / revenue) if revenue > 0 else 0.0
        pnl_pct = extra.get("pnl_percentage")
        profit = extra.get("profit_usdt")
        duration_s = extra.get("duration_s")
        reason = extra.get("reason")
        rows = [f"<b>💱 SELL FILLED</b> — {self.escape_html(symbol)}"]
        rows.append(f"💵 Price: <b>${price:.6f}</b>  •  Qty: {amount:.8f}  •  Proceeds: ${revenue:.2f}")
        if fee: rows.append(f"🏷 Fee: ${float(fee):.4f} ({'maker' if maker else 'taker'}, {fee_rate*100:.3f}%)")
        meta = []
        if order_type: meta.append(order_type)
        if tif: meta.append(f"TIF {tif}")
        if client_id: meta.append(f"CID <code>{self.escape_html(str(client_id)[-10:])}</code>")
        if meta: rows.append("⚙️ " + " • ".join(meta))
        if reason:
            reason_lower = str(reason).lower()
            if reason_lower in ["tp", "take_profit"]:
                rows.append("🎯 Exit: TAKE PROFIT")
            elif reason_lower in ["sl", "stop_loss"]:
                rows.append("🛑 Exit: STOP LOSS")
            elif "panic" in reason_lower:
                rows.append("🚨 Exit: PANIC SELL")
            elif "ttl" in reason_lower:
                rows.append("⏱ Exit: TTL EXPIRED")
            elif "reset" in reason_lower:
                rows.append("🔄 Exit: PORTFOLIO RESET")
            else:
                rows.append(f"🧭 Exit: {self.escape_html(str(reason).upper())}")
        if pnl_pct is not None and profit is not None:
            rows.append(f"📈 P&L: {float(pnl_pct):+.2f}%  •  {float(profit):+.2f} USDT")
        if duration_s:
            try: rows.append(f"⏱ Hold: {str(timedelta(seconds=float(duration_s)))}")
            except Exception: pass
        rows.append(f"🆔 Order: <code>{self.escape_html(str(order_id)[-12:])}</code>")
        msg = "\n".join(rows) + self._session_footer()
        self.send(msg, escape=False)

    def notify_exit(self, symbol: str, reason: str, entry_price: float, exit_price: float,
                   amount: float, pnl_percentage: float, profit_usdt: float,
                   duration_s: float = 0, order_id: str = "", **extra):
        """Send enriched EXIT notification (TP/SL/PANIC/RESET)"""
        self.total_profit += profit_usdt
        reason_lower = (reason or "").lower()
        if reason_lower in ["tp", "take_profit"]:
            reason_display = "TAKE PROFIT ✅"
            icon = "🎯"
        elif reason_lower in ["sl", "stop_loss"]:
            reason_display = "STOP LOSS ❌"
            icon = "🛑"
        elif "panic" in reason_lower:
            reason_display = "PANIC SELL 🚨"
            icon = "⚠️"
        elif "reset" in reason_lower:
            reason_display = "PORTFOLIO RESET 🔄"
            icon = "🔄"
        elif "api" in reason_lower or "error" in reason_lower:
            reason_display = "ERROR EXIT ⚠️"
            icon = "⚠️"
        else:
            reason_display = reason.upper()
            icon = "📤"

        rows = [f"{icon} <b>{reason_display}</b> — {self.escape_html(symbol)}"]
        rows.append(f"💵 Entry: ${float(entry_price):.6f}  →  Exit: ${float(exit_price):.6f}")
        rows.append(f"📦 Qty: {float(amount):.8f}")

        # Enhanced PnL display
        if profit_usdt > 1:
            rows.append(f"💚 <b>P&L: {float(pnl_percentage):+.2f}%  •  +${float(profit_usdt):.2f}</b>")
        elif profit_usdt < -1:
            rows.append(f"💔 <b>P&L: {float(pnl_percentage):+.2f}%  •  ${float(profit_usdt):.2f}</b>")
        else:
            rows.append(f"📈 P&L: {float(pnl_percentage):+.2f}%  •  ${float(profit_usdt):+.2f}")

        if duration_s:
            try: rows.append(f"⏱ Hold: {str(timedelta(seconds=float(duration_s)))}")
            except Exception: pass

        exit_type = extra.get("exit_type")
        order_type = extra.get("order_type")
        if exit_type or order_type:
            meta = []
            if exit_type: meta.append(str(exit_type).upper())
            if order_type: meta.append(str(order_type).upper())
            rows.append("⚙️ " + " • ".join(meta))

        if order_id:
            rows.append(f"🆔 Order: <code>{self.escape_html(str(order_id)[-12:])}</code>")

        msg = "\n".join(rows) + self._session_footer()
        self.send(msg, escape=False)

    def notify_error(self, error_type: str, symbol: str = None, details: str = None):
        """Send error notification with deduplication"""
        dedup_key = f"ERROR_{error_type}_{symbol}" if symbol else f"ERROR_{error_type}"

        msg = f"⚠️ <b>FEHLER: {self.escape_html(error_type)}</b>\n\n"
        if symbol:
            msg += f"📊 Symbol: {self.escape_html(symbol)}\n"
        if details:
            msg += f"📝 Details: {self.escape_html(details)[:200]}\n"
        msg += f"🕐 Zeit: {datetime.now().strftime('%H:%M:%S')}"

        self.send(msg, disable_notification=False, dedup_key=dedup_key, escape=False)

    def notify_daily_summary(self, stats: Dict[str, Any]):
        """Send daily summary"""
        msg = "📊 <b>TAGES-ZUSAMMENFASSUNG</b>\n\n"
        msg += f"📈 Trades heute: {stats.get('trades_today', 0)}\n"
        msg += f"💵 Realisiert: <b>${stats.get('realized_pnl_today', 0):.2f}</b>\n"
        msg += f"📊 Unrealisiert: ${stats.get('unrealized_pnl', 0):.2f}\n"
        msg += f"💰 Gesamt PnL: <b>${stats.get('total_pnl', 0):.2f}</b>\n"
        msg += f"🎯 Win Rate: {stats.get('win_rate', 0):.1f}%\n"
        msg += f"📈 Beste: {stats.get('best_trade', 'N/A')}\n"
        msg += f"📉 Schlechteste: {stats.get('worst_trade', 'N/A')}"

        self.send(msg, escape=False)

# Global instance
tg = TelegramNotifier()

def init_telegram_from_config():
    """Initialize Telegram from environment/config"""
    global tg
    # Try to load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    tg = TelegramNotifier()

    import logging
    logger = logging.getLogger(__name__)

    if tg.is_configured():
        logger.info(f"Telegram notifier initialized (chat_id: {tg.chat_id})",
                   extra={'event_type': 'TELEGRAM_INITIALIZED', 'chat_id': tg.chat_id})
        return True
    else:
        logger.info("Telegram notifier disabled or not configured",
                   extra={'event_type': 'TELEGRAM_DISABLED'})
        return False
