import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def _hash(x: str) -> str:
    return hashlib.sha256(x.encode("utf-8")).hexdigest()[:16]

class _JSONLWriter:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # append, line-buffered
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()

    def write(self, obj: dict):
        line = json.dumps(obj, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")

    def close(self):
        try:
            self._fh.flush(); self._fh.close()
        except Exception:
            pass

def _scrub_payload(obj, max_arglen=2000, scrub_ids=True):
    # flache Kopie, nur simple Scrubs (keine API-Keys werden hier erwartet)
    def _maybe_shorten(v):
        if isinstance(v, str) and len(v) > max_arglen:
            return v[:max_arglen] + f"...<{len(v)-max_arglen}b more>"
        return v
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if scrub_ids and k in ("id", "clientOrderId", "client_order_id"):
                if isinstance(v, str):
                    out[k] = f"anon_{_hash(v)}"
                    continue
            out[k] = _maybe_shorten(v)
        return out
    return obj

class TracedExchange:
    """
    Wrappt einen bestehenden Exchange-Adapter (deinen CCXT-Adapter) und
    loggt jeden Call als JSONL: ts, fn, args, resp/error, latency_ms.
    """
    def __init__(self, real_exchange, config, logger):
        self._x = real_exchange
        self._cfg = config
        self._logger = logger
        base_dir = getattr(config, "SESSION_DIR", os.getcwd())
        path = getattr(config, "EXCHANGE_TRACE_PATH", None)
        if not path:
            path = os.path.join(base_dir, "logs", "exchange_trace.jsonl")
        self._writer = _JSONLWriter(path)
        self._levels = int(getattr(config, "EXCHANGE_TRACE_ORDERBOOK_LEVELS", 10))
        self._max_arglen = int(getattr(config, "EXCHANGE_TRACE_MAX_ARGLEN", 2000))
        self._scrub_ids = bool(getattr(config, "EXCHANGE_TRACE_SCRUB_IDS", True))
        self._logger.info(f"EXCHANGE_TRACER enabled → {path} (levels={self._levels})")

    # ---------- Hilfsfunktion für RPC-Calls ----------
    def _trace_call(self, fn_name: str, args: dict, call):
        ts = _utc_now_iso()
        t0 = time.perf_counter()
        error = None
        resp = None
        try:
            resp = call()
            return resp
        except Exception as e:
            error = {
                "type": type(e).__name__,
                "msg": str(e),
            }
            raise
        finally:
            dt_ms = int((time.perf_counter() - t0) * 1000)
            rec = {
                "ts": ts,
                "fn": fn_name,
                "args": _scrub_payload(args, self._max_arglen, self._scrub_ids),
                "resp": _scrub_payload(self._shrink_resp(fn_name, resp), self._max_arglen, self._scrub_ids) if resp is not None else None,
                "latency_ms": dt_ms,
                "error": error,
            }
            self._writer.write(rec)

    def _shrink_resp(self, fn_name: str, resp):
        # Für Orderbuch nur Top-N Ebenen speichern
        if fn_name == "fetch_order_book" and isinstance(resp, dict):
            out = dict(resp)
            if "bids" in out:
                out["bids"] = (out["bids"] or [])[: self._levels]
            if "asks" in out:
                out["asks"] = (out["asks"] or [])[: self._levels]
            return out
        return resp

    # ---------- hier die Methoden proxien, die dein Bot nutzt ----------
    def load_markets(self, reload=False):
        return self._trace_call("load_markets", {"reload": reload}, lambda: self._x.load_markets(reload))

    def fetch_ticker(self, symbol):
        return self._trace_call("fetch_ticker", {"symbol": symbol}, lambda: self._x.fetch_ticker(symbol))

    def fetch_tickers(self, symbols=None):
        return self._trace_call("fetch_tickers", {"symbols": symbols}, lambda: self._x.fetch_tickers(symbols))

    def fetch_order_book(self, symbol, limit=None):
        return self._trace_call("fetch_order_book", {"symbol": symbol, "limit": limit}, lambda: self._x.fetch_order_book(symbol, limit))

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        return self._trace_call("create_order", {
            "symbol": symbol, "type": type, "side": side, "amount": amount, "price": price, "params": params
        }, lambda: self._x.create_order(symbol, type, side, amount, price, params or {}))

    def cancel_order(self, id, symbol=None, params=None):
        scrub_id = f"anon_{_hash(str(id))}" if self._scrub_ids and isinstance(id, str) else id
        return self._trace_call("cancel_order", {"id": scrub_id, "symbol": symbol, "params": params},
                                lambda: self._x.cancel_order(id, symbol, params or {}))

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        return self._trace_call("fetch_open_orders", {"symbol": symbol, "since": since, "limit": limit, "params": params},
                                lambda: self._x.fetch_open_orders(symbol, since, limit, params or {}))

    def fetch_balance(self, params=None):
        return self._trace_call("fetch_balance", {"params": params}, lambda: self._x.fetch_balance(params or {}))

    def fetch_order(self, id, symbol=None, params=None):
        scrub_id = f"anon_{_hash(str(id))}" if self._scrub_ids and isinstance(id, str) else id
        return self._trace_call("fetch_order", {"id": scrub_id, "symbol": symbol, "params": params},
                                lambda: self._x.fetch_order(id, symbol, params or {}))

    def fetch_trades(self, symbol, since=None, limit=None, params=None):
        return self._trace_call("fetch_trades", {"symbol": symbol, "since": since, "limit": limit, "params": params},
                                lambda: self._x.fetch_trades(symbol, since, limit, params or {}))

    def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        return self._trace_call("fetch_my_trades", {"symbol": symbol, "since": since, "limit": limit, "params": params},
                                lambda: self._x.fetch_my_trades(symbol, since, limit, params or {}))

    def load_time_difference(self):
        return self._trace_call("load_time_difference", {}, lambda: self._x.load_time_difference())

    def set_sandbox_mode(self, sandbox):
        return self._trace_call("set_sandbox_mode", {"sandbox": sandbox}, lambda: self._x.set_sandbox_mode(sandbox))

    # ggf. weitere Methoden, die dein Bot nutzt:
    def __getattr__(self, item):
        # Fallback: alles andere direkt durchreichen (z.B. .markets, .timeframes, .has, etc.)
        return getattr(self._x, item)

    def __del__(self):
        # Cleanup beim Garbage Collection
        try:
            self._writer.close()
        except Exception:
            pass
