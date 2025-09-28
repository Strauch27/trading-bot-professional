#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Exchange-Trace-Extractor (kompakt & robust für MEXC/ccxt-Logs)

- Nimmt Ordner oder Datei(en) (inkl. *.gz)
- Liest rotierten Trace: exchange_trace_YYYY...jsonl(.gz)
- Filtert nach Methoden (snake_case), Symbolen und Zeitfenster
- Dünnt fetch_order_book via Sampling aus
- Extrahiert:
    * orders.csv  (create/fetch/cancel/open_orders)
    * tickers.csv (last/bid/ask)
    * errors.csv  (min_notional/min_qty/precision/insufficient/429 – Beispiele)
    * filtered_exchange_trace.jsonl(.gz) (Originalzeilen gefiltert)
    * summary.json (Statistik)
Nur Standardbibliothek.
"""

import os, sys, json, csv, re, gzip
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from glob import glob
from pathlib import Path
from typing import Optional, Iterable, List

# =========================
#           CONFIG
# =========================
CONFIG = {
    # Eingabe: Datei, Wildcard oder ORDNER. Leer => Auto-Discovery der neuesten Session/Datei.
    "INPUT_PATH": r"C:\01_Programmieren\03_Trading_Bot_Professional\v11\sessions\session_20250928_102159\logs",

    # Ausgabeordner
    "OUTDIR": r"C:\01_Programmieren\03_Trading_Bot_Professional\v11\sessions\session_20250928_102159\logs\slice_out",

    # Zeitfenster:
    # a) letzte N Minuten (über ALLE Inputs ermittelt)
    "LAST_MINUTES": 60,       # None = aus
    # b) oder absolute Grenzen (ISO-8601, 'Z' oder Offset), überschreiben LAST_MINUTES falls gesetzt
    "FROM_ISO": None,         # z.B. "2025-09-28T12:10:00Z"
    "TO_ISO":   None,         # z.B. "2025-09-28T12:45:00Z"

    # Methoden (snake_case passend zu deinen Traces)
    "METHODS": [
        "create_order", "cancel_order", "fetch_order", "fetch_open_orders",
        "fetch_ticker", "fetch_order_book"
    ],

    # Orderbook-Ausdünnung (jede N-te Zeile behalten)
    "EVERY_ORDERBOOK": 50,

    # Symbol-Whitelist (leer/None = alle)
    "SYMBOLS": [],

    # Hartlimit auf Zeilenzahl (0 = aus)
    "MAX_LINES": 0,

    # Outputs zusätzlich komprimieren (.gz)
    "GZIP_OUTPUTS": True,
}

# =========================
#      Hilfsfunktionen
# =========================

ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
MIN_ERR_PATTERNS = [
    ("min_notional", re.compile(r"(minimum transaction volume|Min.*Notional|min.*cost)", re.I)),
    ("min_qty",      re.compile(r"(min(imum)? amount precision|min.*qty|amount .* must be greater)", re.I)),
    ("precision",    re.compile(r"(price precision|amount precision|scale)", re.I)),
    ("insufficient", re.compile(r"(insufficient|not enough|balance)", re.I)),
    ("throttle",     re.compile(r"(Too Many Requests|429|rate limit)", re.I)),
]

def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

def open_in(path: str, mode: str = "r"):
    if path.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8", newline="")
    return open(path, mode, encoding="utf-8", newline="")

def open_out(path: str, mode: str = "w", gzip_on: bool = False):
    if gzip_on:
        return gzip.open(path + ".gz", mode + "t", encoding="utf-8", newline="")
    return open(path, mode, encoding="utf-8", newline="")

def write_csv(path: str, rows: List[dict], fieldnames: List[str], gzip_on: bool = False) -> None:
    f = open_out(path, "w", gzip_on)
    with f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

def parse_iso_or_epoch(v) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        s = v/1000 if v > 1e12 else v
        return datetime.fromtimestamp(s, tz=timezone.utc)
    s = str(v).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if ISO_RE.match(s):
            return datetime.fromisoformat(s)
    except Exception:
        pass
    try:
        x = float(s)
        return parse_iso_or_epoch(x)
    except Exception:
        return None

def pick_ts(obj: dict) -> Optional[datetime]:
    # typische Felder in deinen Traces: ts (ISO Z), ts_ms (epoch), datetime
    for k in ("ts", "ts_ms", "timestamp", "time", "datetime"):
        if k in obj:
            dt = parse_iso_or_epoch(obj[k])
            if dt:
                return dt
    return None

def norm_method(obj: dict) -> str:
    m = (obj.get("fn") or obj.get("method") or obj.get("call") or "").strip()
    return m.lower().replace("-", "_")

def discover_latest_trace() -> Optional[str]:
    # finde neueste Session mit exchange_trace*.jsonl(.gz)
    sessions = sorted(glob(os.path.join("sessions", "session_*")), reverse=True)
    for s in sessions:
        logs = os.path.join(s, "logs")
        cand = []
        cand += glob(os.path.join(logs, "exchange_trace.jsonl"))
        cand += glob(os.path.join(logs, "exchange_trace_*.jsonl"))
        cand += glob(os.path.join(logs, "exchange_trace.jsonl.gz"))
        cand += glob(os.path.join(logs, "exchange_trace_*.jsonl.gz"))
        if cand:
            cand.sort(key=lambda p: (os.path.getmtime(p), os.path.getsize(p)), reverse=True)
            return cand[0]
    return None

def expand_inputs(path_or_dir: str) -> List[str]:
    p = (path_or_dir or "").strip()
    if not p:
        found = discover_latest_trace()
        return [found] if found else []
    if os.path.isdir(p):
        files = glob(os.path.join(p, "exchange_trace*.jsonl")) + \
                glob(os.path.join(p, "exchange_trace*.jsonl.gz"))
        return sorted(files, key=os.path.getmtime)
    if any(ch in p for ch in "*?[]"):
        files = glob(p)
        return sorted(files, key=os.path.getmtime)
    return [p]

# =========================
#        Hauptprogramm
# =========================

def main():
    cfg = CONFIG.copy()
    inputs = expand_inputs(cfg["INPUT_PATH"])
    if not inputs:
        print("[ERR] Keine Eingabedateien gefunden (INPUT_PATH leer/ungültig).", file=sys.stderr)
        sys.exit(2)

    print("[INFO] Inputs:", *inputs, sep="\n  ")

    outdir = cfg["OUTDIR"]; ensure_dir(outdir)
    methods = set(cfg["METHODS"] or [])
    sym_whitelist = set(cfg["SYMBOLS"]) if cfg["SYMBOLS"] else None
    every_ob = max(1, int(cfg["EVERY_ORDERBOOK"] or 1))
    max_lines = int(cfg["MAX_LINES"] or 0)
    gzip_out = bool(cfg["GZIP_OUTPUTS"])

    # Zeitfenster bestimmen
    t_from = parse_iso_or_epoch(cfg["FROM_ISO"]) if cfg["FROM_ISO"] else None
    t_to   = parse_iso_or_epoch(cfg["TO_ISO"])   if cfg["TO_ISO"]   else None

    if cfg["LAST_MINUTES"] and not (t_from or t_to):
        # Ende über alle Inputs
        last_ts = None
        for path in inputs:
            with open_in(path, "r") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                        ts = pick_ts(obj)
                        if ts:
                            last_ts = ts
                    except Exception:
                        continue
        if last_ts:
            t_from = last_ts - timedelta(minutes=int(cfg["LAST_MINUTES"]))
            print(f"[INFO] Zeitfenster: last {cfg['LAST_MINUTES']} min → from {t_from.isoformat()}")
        else:
            print("[WARN] Keine Zeitstempel gefunden; LAST_MINUTES ignoriert.")

    def in_window(ts: Optional[datetime]) -> bool:
        if t_from and ts and ts < t_from: return False
        if t_to   and ts and ts > t_to:   return False
        return True

    def symbol_ok(sym: Optional[str]) -> bool:
        if not sym_whitelist: return True
        return sym in sym_whitelist

    # Ausgabepfade
    out_jsonl_path   = os.path.join(outdir, "filtered_exchange_trace.jsonl")
    orders_csv_path  = os.path.join(outdir, "orders.csv")
    tickers_csv_path = os.path.join(outdir, "tickers.csv")
    errors_csv_path  = os.path.join(outdir, "errors.csv")
    summary_json_path= os.path.join(outdir, "summary.json")

    # Stats/Container
    method_counts = Counter()
    symbol_counts = Counter()
    err_counts    = Counter()
    err_examples  = defaultdict(list)
    minmax_ts     = [None, None]
    unique_orders = set()
    order_rows    = []
    ticker_rows   = []
    error_rows    = []
    ob_ctr        = 0
    out_lines     = 0

    print("[INFO] Filtering gestartet …")
    with open_out(out_jsonl_path, "w", gzip_out) as fout:
        for in_path in inputs:
            with open_in(in_path, "r") as fin:
                for line in fin:
                    # JSON parse
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    ts = pick_ts(obj)
                    if not in_window(ts):
                        continue

                    method = norm_method(obj)
                    if methods and method not in methods:
                        continue

                    # Symbol extrahieren
                    symbol = (
                        obj.get("symbol")
                        or (obj.get("args") or {}).get("symbol")
                        or (obj.get("params") or {}).get("symbol")
                        or obj.get("market")
                        or obj.get("pair")
                    )
                    if not symbol_ok(symbol):
                        continue

                    # Orderbook-Sampling
                    if method == "fetch_order_book":
                        ob_ctr += 1
                        if ob_ctr % every_ob != 0:
                            continue

                    # Zeile in gefiltertes jsonl kopieren
                    fout.write(line)
                    out_lines += 1
                    if max_lines and out_lines >= max_lines:
                        break

                    # --- Stats & Extraktionen ---
                    method_counts[method] += 1
                    if symbol: symbol_counts[symbol] += 1
                    if ts:
                        if minmax_ts[0] is None or ts < minmax_ts[0]:
                            minmax_ts[0] = ts
                        if minmax_ts[1] is None or ts > minmax_ts[1]:
                            minmax_ts[1] = ts

                    payload = json.dumps(obj, ensure_ascii=False)
                    for key, pat in MIN_ERR_PATTERNS:
                        if pat.search(payload):
                            err_counts[key] += 1
                            if len(err_examples[key]) < 5:
                                err_examples[key].append(payload[:800])

                    # Orders
                    if method in {"create_order","fetch_order","cancel_order","fetch_open_orders"}:
                        a = obj.get("args") or {}
                        r = obj.get("resp") or {}
                        side    = a.get("side")   or r.get("side")
                        price   = a.get("price")  or r.get("price")
                        amount  = a.get("amount") or r.get("amount")
                        cost    = r.get("cost")
                        oid     = r.get("id") or r.get("orderId") or r.get("clientOrderId") or a.get("clientOrderId")
                        status  = r.get("status")
                        if oid: unique_orders.add(oid)
                        order_rows.append({
                            "time": ts.isoformat() if ts else "",
                            "method": method,
                            "symbol": symbol or "",
                            "side": side or "",
                            "price": price if price is not None else "",
                            "amount": amount if amount is not None else "",
                            "cost": cost if cost is not None else "",
                            "order_id": oid or "",
                            "status": status or ""
                        })

                    # Ticker
                    elif method == "fetch_ticker":
                        r = obj.get("resp") or {}
                        last = r.get("last", r.get("close"))
                        bid  = r.get("bid")
                        ask  = r.get("ask")
                        ticker_rows.append({
                            "time": ts.isoformat() if ts else "",
                            "symbol": symbol or "",
                            "last": last if last is not None else "",
                            "bid": bid if bid is not None else "",
                            "ask": ask if ask is not None else ""
                        })

            if max_lines and out_lines >= max_lines:
                break

    # Fehlerbeispiele in CSV schreiben
    for k, _ in MIN_ERR_PATTERNS:
        for sample in err_examples.get(k, []):
            error_rows.append({"pattern": k, "sample": sample})

    # CSVs
    write_csv(orders_csv_path,  order_rows,
              ["time","method","symbol","side","price","amount","cost","order_id","status"],
              gzip_on=gzip_out)
    write_csv(tickers_csv_path, ticker_rows, ["time","symbol","last","bid","ask"], gzip_on=gzip_out)
    write_csv(errors_csv_path,  error_rows,  ["pattern","sample"],                gzip_on=gzip_out)

    # Summary
    summary = {
        "inputs": inputs,
        "outdir": outdir,
        "time_from": minmax_ts[0].isoformat() if minmax_ts[0] else None,
        "time_to":   minmax_ts[1].isoformat() if minmax_ts[1] else None,
        "methods_kept": sorted(list(methods)) if methods else None,
        "every_orderbook": every_ob,
        "lines_out": out_lines,
        "method_counts": dict(method_counts),
        "top_symbols": dict(symbol_counts.most_common(20)),
        "unique_orders_seen": len([x for x in unique_orders if x]),
        "errors_counts": dict(err_counts),
        "symbols_filter": sorted(list(sym_whitelist)) if sym_whitelist else None,
        "max_lines_cap": max_lines or None,
        "gzip_outputs": gzip_out,
    }
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("[OK] Geschrieben:")
    print(" ", os.path.abspath(out_jsonl_path) + (".gz" if gzip_out else ""))
    print(" ", os.path.abspath(orders_csv_path)  + (".gz" if gzip_out else ""))
    print(" ", os.path.abspath(tickers_csv_path) + (".gz" if gzip_out else ""))
    print(" ", os.path.abspath(errors_csv_path)  + (".gz" if gzip_out else ""))
    print(" ", os.path.abspath(summary_json_path))


if __name__ == "__main__":
    main()
