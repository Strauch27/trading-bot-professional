# Root Cause Analysis: Session 20251026_103545

**Analyst:** Claude Code
**Analysis Date:** 2025-10-26
**Session:** session_20251026_103545
**Duration:** ~15 minutes (10:36 - 10:51)
**Termination:** Timeout/Crash at 30 seconds

---

## Executive Summary

**CRITICAL FINDING:** The trading bot experienced a complete order execution failure. Despite identifying 53 buy opportunities and successfully reserving budget, **ZERO trades were executed**. All buy intents resulted in budget release with reasons "order_router_release" and "stale_intent_cleanup". The bot ultimately crashed with a timeout in the main thread.

---

## Update: Enhanced Analysis with User Feedback

**Date:** 2025-10-26 (Updated)
**Feedback Provider:** User
**Assessment:** ⭐⭐⭐⭐⭐ **Excellent - Technically Precise**

The user provided outstanding additional evidence that significantly strengthened this analysis:

### Key Contributions:

1. **Discovered `orders.jsonl`** - Definitive proof: 67 ORDER_SENT, 0 ORDER_FILLED
2. **Identified State Cleanup Bug** - Failed intents accumulate in `pending_buy_intents` without cleanup
3. **Precise File References** - Exact line numbers for config issues (config_backup.py:295-308)
4. **Verification Command** - `rg -n "ERROR"` proving zero error logging
5. **Concrete Solutions** - Detailed, actionable fixes with code examples

This feedback transformed the analysis from hypothesis-based to **evidence-proven** and added critical implementation details.

---

## 1. Session Overview

### Timeline
- **10:35:45** - Bot started
- **10:36:16** - Portfolio reset completed, 154.86 USDT available
- **10:36:28** - Engine started, trading began
- **10:37-10:50** - Bot ran normally, heartbeats every ~60 seconds
- **10:51:09** - Last trace activity
- **~10:51** - **Crash:** Timeout (30 seconds) in main thread

### Key Statistics
- **Buy Signals Detected:** 53
- **Buy Intents Created:** 53
- **Trades Executed:** 0 ❌
- **Positions Opened:** 0 ❌
- **Budget Reserved Events:** 67
- **Budget Released Events:** 81
- **Log Entries:** 24,960 (mostly TRACE_STEP)

---

## 2. Critical Issues Identified

### 2.1 Order Execution Failure (SEVERITY: CRITICAL)

**Problem:**
All buy intents were created but never converted into actual executed orders.

**Smoking Gun Evidence:**
```bash
# logs/jsonl/orders.jsonl analysis:
ORDER_SENT events: 67
ORDER_FILLED events: 0  ❌

# Every order sent, ZERO filled!
```

**File References:**
- `logs/jsonl/orders.jsonl:1` - First ORDER_SENT (OPNODE/USDT)
- `logs/jsonl/orders.jsonl:67` - Last ORDER_SENT (COAI/USDT)
- `logs/events_20251026_103545.jsonl:1-148` - 81 BUDGET_RELEASED events
- `state/engine_transient.json:2-35` - 53 pending_buy_intents at crash

**Affected Symbols:**
- MBG/USDT (multiple attempts)
- COAI/USDT (multiple attempts)
- OPNODE/USDT (early attempts)

**Budget Release Reasons:**
1. `order_router_release` - Order router released budget (order failed/rejected)
2. `stale_intent_cleanup` - Intent marked as stale and cleaned up (timeout)

**Order Flow Pattern:**
```
ORDER_SENT → [No Fill] → BUDGET_RELEASED (order_router_release) → Intent remains in pending_buy_intents ❌
```

### 2.2 State Cleanup Failure (SEVERITY: CRITICAL)

**Problem:**
Failed order attempts are **not being removed** from `pending_buy_intents`, causing identical signals to be retried infinitely.

**Evidence:**
```json
state/engine_transient.json:2-35
{
  "pending_buy_intents": {
    "1761475485690-MBGUSDT-dc2eab05746d8aab": {...},
    "1761475494090-MBGUSDT-75665d28bdef7671": {...},
    "1761475501345-MBGUSDT-b0c85932b6e33902": {...},
    // 53 total intents - ALL FAILED but still pending!
  },
  "positions": {}  // Zero positions despite 53 intents
}
```

**Analysis:**
- Budget was released (81 BUDGET_RELEASED events)
- But intents were **NOT removed** from pending_buy_intents
- Engine doesn't persist that failed orders were processed
- Same symbols retried constantly (MBG/USDT: 4+ attempts, COAI/USDT: 8+ attempts)
- Creates infinite retry loop on same failing conditions

### 2.3 Stale Intent Pattern

**Configuration:**
- `INTENT_STALE_THRESHOLD_S`: 60 seconds
- `STALE_INTENT_CHECK_ENABLED`: true

**Analysis:**
Buy intents are being created, but they're timing out after 60 seconds without being converted to filled orders. This suggests the order placement/execution pipeline is either:
1. Not placing orders at all
2. Placing orders that are never filled
3. Encountering errors that prevent order placement

**However:** Even stale cleanup doesn't remove intents from the state file (state persistence bug).

### 2.4 Ultra-Strict IOC Configuration (SEVERITY: HIGH)

**File Reference:** `config_backup.py:295-308`

**Settings:**
```python
# Lines 295-299: All escalation steps are IOC with max_attempts=1
BUY_ESCALATION_STEPS = [
    {"tif": "IOC", "premium_bps": 10, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 30, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 60, "max_attempts": 1},
]

# Line 300: Ultra-short order lifetime
IOC_ORDER_TTL_MS = 600  # Only 0.6 seconds!

# Line 302
ENTRY_ORDER_TIF = "IOC"

# Line 313: Tight slippage tolerance
MAX_SLIPPAGE_BPS_ENTRY = 15  # Only 0.15% allowed
```

**Analysis:**
- **ALL orders use IOC** (Immediate-Or-Cancel) - no GTC fallback
- Orders have only **600ms** to execute or they're canceled
- Only **1 attempt per escalation step** (3 total max)
- **15 bps slippage tolerance** (0.15%) - extremely tight for volatile altcoins
- On illiquid altcoins → **guaranteed immediate cancellation**
- Explains the constant `order_router_release` pattern

**Why This Fails:**
IOC orders require perfect market conditions:
- Exact price match within 0.6 seconds
- Sufficient liquidity at that exact moment
- Spread within 15 bps
- On volatile altcoins with thin orderbooks, this is nearly impossible

### 2.4 Application Crash

**Stack Trace Analysis:**

Main Thread (Blocked):
```
File: main.py:599 in main
  -> portfolio.py:454 in perform_startup_reset
  -> settlement.py:149 in refresh_budget_from_exchange
  -> exchange_tracer.py:112 in _trace_call (fetch_balance)
  -> ccxt throttle (API rate limiting)
```

**Findings:**
1. **Main thread blocked** during logging operation (line 44: logging/__init__.py:999)
2. **Multiple threads running:**
   - MemoryManager (waiting on threading.py:373)
   - HeartbeatMonitor (waiting on shutdown_coordinator.py:451)
   - UIFallbackFeed (waiting on engine.py:1530)
   - TradingEngine-Main (blocked on logging)

3. **Timeout triggered** after 30 seconds of inactivity

**Root Cause:** Likely a deadlock in the logging subsystem or threading coordination.

---

## 3. Root Cause Analysis

### Primary Root Cause: IOC Order Rejection Loop

**Hypothesis:**
1. Bot identifies buy signals (53 total)
2. Creates buy intents with budget reservation
3. Attempts to place IOC orders on exchange
4. **IOC orders are immediately rejected** (insufficient liquidity or price moved)
5. Budget is released with "order_router_release"
6. Intent becomes stale after 60s and is cleaned up

**Supporting Evidence:**
- 67 budget reservations vs 81 budget releases (more releases = failed orders)
- Zero positions despite 53 buy signals
- Configuration uses aggressive IOC with only 600ms TTL
- Multiple attempts on same symbols (MBG/USDT had 4+ attempts)

### Secondary Root Cause: Logging/Threading Deadlock

**Hypothesis:**
The crash was triggered by a deadlock in the logging system during high-volume trace activity (24,960 log entries).

**Supporting Evidence:**
- Main thread blocked at logging/__init__.py:999 in format
- Multiple threads waiting on various synchronization primitives
- Crash occurred after sustained trace logging activity

---

## 4. Detailed Findings

### 4.1 Budget Management

**Timeline Example (OPNODE/USDT):**
```
10:38:16.487 - BUDGET_RESERVED: 25.0 USDT (symbol: OPNODE/USDT, price: 0.000233)
10:38:19.378 - BUDGET_RELEASED: 25.0 USDT (reason: order_router_release)

10:39:26.385 - BUDGET_RESERVED: 25.0 USDT (symbol: OPNODE/USDT, price: 0.000233)
10:39:28.888 - BUDGET_RELEASED: 25.0 USDT (reason: order_router_release)
10:39:33.752 - BUDGET_RELEASED: 25.0 USDT (reason: stale_intent_cleanup)
```

**Pattern:**
- Budget reserved
- Order attempted (~2-3 seconds later)
- **Immediate failure** (order_router_release)
- Stale cleanup confirms order never filled

### 4.2 Trading Configuration Issues

**Potentially Problematic Settings:**

1. **Too Aggressive IOC:**
   - `IOC_ORDER_TTL_MS: 600` (only 0.6 seconds)
   - May not be enough time in volatile markets

2. **Limited Escalation:**
   - Only 3 total attempts with IOC
   - No fallback to GTC (Good-Til-Canceled) orders

3. **Spread/Slippage Constraints:**
   - `MAX_SPREAD_BPS_ENTRY: 10` (0.1%)
   - `MAX_SLIPPAGE_BPS_ENTRY: 15` (0.15%)
   - May be too tight for volatile altcoins

4. **Minimum Notional:**
   - `ROUTER_MIN_NOTIONAL: 5.0`
   - `MIN_ORDER_VALUE: 5.1`
   - May exclude some valid trades

### 4.3 Silent Failure - Zero Error Logging (SEVERITY: CRITICAL)

**Verification:**
```bash
$ rg -n "ERROR" sessions/session_20251026_103545/logs/
# Result: 0 matches
```

**Major Concern:**
Despite **67 ORDER_SENT and 0 ORDER_FILLED** (100% failure rate), there are:
- **ZERO ERROR events** in any log file
- **ZERO WARNING events** in system logs
- **ZERO CRITICAL events** anywhere
- **NO exchange rejection reasons** logged
- **NO explanation** for order_router_release

**Implication:**
The order router is **completely swallowing errors**. The bot has no observability into why orders fail:
- Exchange error codes not captured
- IOC partial fills not logged
- Minimum notional violations not reported
- Network problems not surfaced

**This is a fundamental operational failure** - you cannot debug what you cannot see.

---

## 5. Performance Metrics

### Logging Volume
- Total log entries: ~25,000
- Primary event type: TRACE_STEP (debug tracing)
- System log (compressed): 5.0 MB
- Debug log (compressed): 4.9 MB
- Trades log: 11 MB (24,960 lines)

### CPU/Memory
- Memory Manager thread running
- No obvious memory issues from logs

### API Usage
- 137 tradeable coins detected
- Batch polling enabled (MD_BATCH_SIZE: 13)
- Rate limiting enabled (800 RPM cap)

---

## 6. Recommended Actions (Based on User Feedback)

### IMMEDIATE (Critical - Required for Bot to Function)

#### 1. **Fix Order Router Observability** 🔴
**Problem:** Complete silent failure on all 67 orders.

**Solution:**
```python
# In order router, on EVERY budget release:
def release_budget(symbol, amount, reason):
    if reason in ["order_router_release", "stale_intent_cleanup"]:
        # Log at WARN or ERROR level with details:
        logger.error(
            "ORDER_FAILED",
            symbol=symbol,
            reason=reason,
            exchange_error=exchange_error_code,  # Capture this!
            ioc_partial=partial_fill_amount,
            min_notional_met=notional_check,
            network_issue=network_status
        )
```

**File to modify:** Order router / budget manager

#### 2. **Fix State Cleanup Bug** 🔴
**Problem:** Failed intents stay in `pending_buy_intents` forever, causing infinite retries.

**Solution:**
```python
# Immediately after order_router_release or stale_intent_cleanup:
def cleanup_failed_intent(intent_id):
    # Remove from pending_buy_intents
    if intent_id in self.pending_buy_intents:
        del self.pending_buy_intents[intent_id]
        self.persist_state()  # Critical!
        logger.info(f"Intent {intent_id} removed from pending")
```

**Verification:** After fix, `engine_transient.json` should show `pending_buy_intents: {}`

#### 3. **Relax IOC Configuration** 🔴
**File:** `config_backup.py:295-313`

**Changes:**
```python
# Line 300: Increase order lifetime
IOC_ORDER_TTL_MS = 2000  # Was: 600ms → Now: 2 seconds

# Lines 295-299: Add GTC fallback after IOC attempts
BUY_ESCALATION_STEPS = [
    {"tif": "IOC", "premium_bps": 10, "max_attempts": 2},  # Try IOC twice
    {"tif": "IOC", "premium_bps": 30, "max_attempts": 2},  # Try IOC twice
    {"tif": "GTC", "premium_bps": 50, "max_attempts": 1},  # Fallback to GTC
]

# Line 313: Widen slippage tolerance for volatile pairs
MAX_SLIPPAGE_BPS_ENTRY = 30  # Was: 15 → Now: 30 (0.3%)
MAX_SPREAD_BPS_ENTRY = 20    # Was: 10 → Now: 20 (0.2%)
```

**Rationale:** Illiquid altcoins need flexible execution, not ultra-strict IOC.

#### 4. **Decouple Budget Refresh from Main Thread** 🔴
**Problem:** `refresh_budget_from_exchange` blocks main thread in CCXT, causing crash.

**Solution:**
```python
# Move to separate worker thread with timeout
class BudgetRefreshWorker(Thread):
    def refresh_with_timeout(self):
        with timeout(5):  # Hard 5-second limit
            try:
                balance = exchange.fetch_balance()
                # Mirror result to engine asynchronously
            except TimeoutError:
                logger.error("Budget refresh timeout")
                # Use last known balance
```

**Files:** `settlement.py:149`, `portfolio.py:454`

### HIGH PRIORITY

#### 5. **Stabilize Logging System** 🟠
**Problem:** Logging deadlock after ~25k TRACE entries.

**Solution:**
```python
# Use async/buffered logging with ringbuffer
import logging.handlers

handler = logging.handlers.QueueHandler(queue)
handler.setFormatter(TimeoutFormatter(timeout_ms=100))  # Fallback on slow format

# In formatter
def format(self, record):
    try:
        with timeout(0.1):  # 100ms max
            return super().format(record)
    except TimeoutError:
        return f"[TIMEOUT] {record.msg}"  # Simplified fallback
```

**File:** Logging configuration / `main.py` setup

#### 6. **Dynamic Slippage Per Symbol** 🟠
**Problem:** Fixed 15 bps doesn't work for all coins.

**Solution:**
```python
# Determine slippage dynamically from orderbook
def get_dynamic_slippage(symbol, orderbook):
    spread_bps = calculate_spread_bps(orderbook)

    if spread_bps > 50:  # Wide spread (illiquid)
        return 30  # Allow 0.3% slippage
    elif spread_bps > 20:
        return 20
    else:
        return 15  # Tight for liquid pairs
```

### MEDIUM PRIORITY

#### 7. **Regression Test Framework** 🟡
After implementing fixes, run paper trading session and verify:

```bash
# Success criteria:
✓ At least 1 intent: ORDER_SENT → ORDER_FILLED
✓ engine_transient.json shows positions > 0
✓ events_*.jsonl shows real budget consumption
✓ No pending_buy_intents for filled orders
✓ ERROR logs explain any failures
```

**Test Duration:** 1 hour minimum
**Expected:** At least 1-2 successful fills on liquid pairs (BTC/USDT, ETH/USDT)

---

## 7. Questions for Further Investigation

1. **Are orders reaching the exchange at all?**
   - Check MEXC API logs
   - Verify API key permissions

2. **What is the exact IOC rejection reason?**
   - Insufficient liquidity?
   - Price moved?
   - Minimum notional not met?

3. **Why is error logging silent?**
   - Exception handling swallowing errors?
   - Log level too high?
   - Logging system failing?

4. **Is the order router even being called?**
   - Add entry/exit tracing to `route_buy`
   - Verify intent → order conversion

---

## 8. Conclusion

The trading bot is **fundamentally broken** in its current state. While it successfully:
- Detects market opportunities (53 buy signals)
- Manages budget reservations
- Runs without crashing for 15 minutes

It **completely fails** at its core function: executing trades.

### Root Causes Identified (Revised)

1. **Silent Failure (Critical):** Order router swallows ALL errors - zero observability into 67 failed orders
2. **State Corruption (Critical):** Failed intents never cleaned from `pending_buy_intents` - infinite retry loop
3. **Execution Impossibility (Critical):** Ultra-strict IOC config (600ms, 15 bps) guaranteed to fail on illiquid altcoins
4. **Thread Safety (High):** Budget refresh and logging both block main thread, causing crash

### Key Evidence (Based on Detailed Analysis)

**Smoking Gun:**
- `logs/jsonl/orders.jsonl`: 67 ORDER_SENT, **0 ORDER_FILLED** (100% failure rate)
- `rg -n "ERROR" sessions/*/logs`: **0 matches** (complete silence)
- `state/engine_transient.json`: 53 failed intents still pending
- `config_backup.py:295-313`: Impossibly strict IOC configuration

**Bottom Line:**
The bot **cannot execute a single trade** in its current configuration. Four critical bugs compound to create complete failure:
1. No error visibility (can't debug)
2. No state cleanup (infinite loops)
3. No execution flexibility (IOC too strict)
4. No thread safety (crashes)

All four must be fixed before the bot can function.

---

## Appendix: Key Files & Evidence

### Critical Evidence Files:
- **Orders Log:** `logs/jsonl/orders.jsonl` (67 ORDER_SENT, 0 ORDER_FILLED)
- **State File:** `state/engine_transient.json:2-35` (53 pending intents)
- **Config Backup:** `config_backup.py:295-313` (IOC configuration)
- **Events Log:** `logs/events_20251026_103545.jsonl:1-148` (budget cycles)

### Supporting Files:
- **Stackdump:** `stackdump.txt` (crash analysis)
- **System Log:** `logs/system_20251026_103545_20251026_114127.jsonl.gz` (5 MB compressed)
- **Debug Log:** `logs/debug_full_20251026_103545_20251026_114125.jsonl.gz` (4.9 MB compressed)
- **Trades Log:** `logs/trades_20251026_103545.jsonl` (24,960 TRACE_STEP entries)

### Verification Commands:
```bash
# Verify zero fills:
grep "ORDER_FILLED" sessions/session_20251026_103545/logs/jsonl/orders.jsonl
# Result: (empty)

# Verify zero errors:
rg -n "ERROR" sessions/session_20251026_103545/logs/
# Result: (empty)

# Count orders sent:
grep "ORDER_SENT" sessions/session_20251026_103545/logs/jsonl/orders.jsonl | wc -l
# Result: 67

# Check pending intents:
jq '.pending_buy_intents | length' sessions/session_20251026_103545/state/engine_transient.json
# Result: 53

# Check positions:
jq '.positions | length' sessions/session_20251026_103545/state/engine_transient.json
# Result: 0
```

---

## 9. Implementierungsplan (alle Findings)

### 9.1 Schritt 1 – Order-Router-Observability herstellen (Critical)
- **Ziel:** Jede abgelehnte Order muss mit Ursache, Exchange-Fehlercode und Intent-ID sichtbar werden.
- **Umsetzung:**
  1. In `services/order_router.py` (Klasse `OrderRouter`) `route_buy` und `route_sell` erweitern:
     - Exceptions aus `exchange.create_order*` abfangen und ein strukturiertes `logger.error` mit Feldern `symbol`, `side`, `intent_id`, `order_config`, `exchange_error`, `latency_ms` schreiben.
     - Rückgabewert so anpassen, dass die aufrufende Stelle zwischen „nicht gesendet“, „teilgefüllt“, „vollständig abgelehnt“ unterscheiden kann.
  2. In `core/portfolio/portfolio.py:1180-1215` (`release`) zusätzliche Parameter (`exchange_error`, `intent_id`) entgegennehmen und an das Logging weiterreichen; Grund `order_router_release` nur noch nach dem neuen Fehlerrückgabepfad setzen.
  3. In `logs/jsonl/orders.jsonl`-Writer (wahrscheinlich `engine/buy_decision.py:700ff`) ein neues Event `ORDER_FAILED` hinzufügen, das direkt vom Order-Router-Result gespeist wird.
- **Akzeptanzkriterien:** `rg "ORDER_FAILED"` liefert für jede Budgetfreigabe einen Log-Eintrag mit Fehlercode; Budget-Events in `events_*.jsonl` besitzen das Feld `reason_detail`.

### 9.2 Schritt 2 – Pending-Intent-Cleanup reparieren (Critical)
- **Ziel:** Fehlgeschlagene Intents dürfen nicht in `engine.pending_buy_intents` verbleiben.
- **Umsetzung:**
  1. In `engine/buy_decision.py` dort, wo `pending_buy_intents[intent_id]` gesetzt wird (ca. Zeile 530), nachvollziehen, welche Pfade bei `order_router_release` enden, und eine Helper-Funktion `clear_intent(intent_id, reason)` der Engine aufrufen.
  2. In `engine/engine.py` eine Methode `def clear_intent(self, intent_id, reason):` implementieren, die
     - den Intent aus `self.pending_buy_intents` entfernt,
     - `self.persist_state()` aufruft,
     - ein `INTENT_CLEARED`-Event inkl. Grund schreibt.
  3. Den bestehenden Stale-Intent-Cron (siehe `engine/engine.py:471-500`) darauf umrüsten, dieselbe Helper-Funktion zu verwenden, damit kein doppelter Code entsteht.
- **Akzeptanzkriterien:** Nach einer Session mit mindestens einer fehlgeschlagenen Order zeigt `state/engine_transient.json` keine alten Einträge; es existiert pro Intent genau ein `INTENT_CREATED` und optional ein `INTENT_CLEARED`.

### 9.3 Schritt 3 – Execution-Config flexibilisieren (Critical)
- **Ziel:** IOC-Only-Konfiguration so erweitern, dass Orders eine Chance auf Fill haben.
- **Umsetzung:**
  1. In der echten Konfigurationsquelle (`config.py` bzw. `config/production.py`) die in Abschnitt 6 erwähnten Werte übernehmen, nicht nur im Session-Backup:
     - `IOC_ORDER_TTL_MS = 2000`
     - `BUY_ESCALATION_STEPS` wie vorgeschlagen ergänzen (zweimal IOC, dann GTC).
     - `MAX_SPREAD_BPS_ENTRY = 20`, `MAX_SLIPPAGE_BPS_ENTRY = 30`.
  2. Falls Werte zur Laufzeit aus `config_module.Settings` gelesen werden (`main.py:635ff`), sicherstellen, dass neue Defaults dort verankert sind.
  3. Für GTC-Fallback muss der Order-Router GTC-spezifische Pfade beherrschen (z. B. Cancel, wenn Intent verfällt); hierzu `services/order_router.py` erweitern und `BUY_GTC_WAIT_SECS` respektieren.
- **Akzeptanzkriterien:** Neue Config steht in Versionskontrolle, Unit-Test deckt ab, dass `Settings().BUY_ESCALATION_STEPS` die drei Stufen liefert; Paper-Trading zeigt mindestens einen `ORDER_FILLED`.

### 9.4 Schritt 4 – Budget-Refresh entkoppeln (Critical)
- **Ziel:** CCXT-Latenzen dürfen Hauptthread nicht blockieren.
- **Umsetzung:**
  1. In `trading/settlement.py:149` die Funktion `refresh_budget_from_exchange` in zwei Teile trennen: (a) Worker-Thread, der `exchange.fetch_balance()` mit `asyncio.wait_for` oder eigener Timeout-Logik ausführt; (b) Cache-Layer, der das letzte erfolgreiche Ergebnis liefert.
  2. In `core/portfolio/portfolio.py:412ff` `perform_startup_reset` und `refresh_budget` so umbauen, dass sie den Worker nur triggern oder auf dessen Cache zugreifen; beim Überschreiten des Timeouts soll ein `logger.warning("Budget refresh skipped")` kommen, kein Crash.
  3. Optional Telemetrie (`metrics/budget_refresh_latency_ms`) hinzufügen, um spätere Engpässe erkennen zu können.
- **Akzeptanzkriterien:** Bewusste Blockade des Exchanges (z. B. mit Mock) führt nicht mehr zum Timeout im Main-Thread; Stackdump zeigt keine Threads, die länger als Timeout warten.

### 9.5 Schritt 5 – Logging subsystems absichern (High)
- **Ziel:** Hohe Trace-Volumen ohne Deadlocks verarbeiten.
- **Umsetzung:**
  1. In der zentralen Logging-Konfiguration (`main.py` oder `logging_config.py`) einen `QueueHandler` + `QueueListener` einsetzen; Worker-Threads loggen ausschließlich in die Queue.
  2. Custom-Formatter `TimeoutFormatter` implementieren (z. B. `utils/logging_utils.py`), der `format` in <100 ms ausführt und bei Überschreiten auf ein minimalistisches Format zurückfällt.
  3. TRACE-Noise reduzieren, indem `TRACE_STEP`-Emitter (`engine/buy_decision.py`, `engine/signals/*`) einen Sampling-Schalter respektieren (`TRACE_SAMPLE_RATE` in Config).
- **Akzeptanzkriterien:** Neue Session mit aktivem TRACE produziert keine `Timeout (0:00:30)!`-Stackdumps; Loggröße sinkt messbar oder bleibt stabil ohne Deadlocks.

### 9.6 Schritt 6 – Dynamische Slippage/Spread-Steuerung implementieren (High)
- **Ziel:** Illiquide Paare erhalten großzügigere Toleranzen, liquide behalten enge.
- **Umsetzung:**
  1. In `services/order_router.py` oder neuem Modul `trading/execution_controls.py` Funktion `determine_entry_constraints(symbol, orderbook)` implementieren, die Spread in Bps berechnet und `slippage_bps`, `spread_bps_limit`, `tif`-Präferenz zurückliefert.
  2. `engine/buy_decision.py` vor dem Erstellen eines Intents das Orderbuch (bereits vorhandene `coin_data`) übergeben und das Ergebnis der neuen Funktion ins Intent-Metadatum schreiben.
  3. Order-Router liest diese dynamischen Werte statt globaler Konstanten; Config-Werte dienen nur als Caps (`MAX_*_BPS_ENTRY`).
- **Akzeptanzkriterien:** In Logs ist ersichtlich, dass Symbole mit Spread >50 bps automatisch Slippage 30 bps erhalten; Regression-Tests (`tests/integration/test_order_flow.py`) prüfen beide Pfade (liquid vs. illiquid).

### 9.7 Schritt 7 – Regressionstest & Monitoring (Medium)
- **Ziel:** Sicherstellen, dass alle Fixes zusammenspielen und echte Fills passieren.
- **Umsetzung:**
  1. Automatisiertes Paper-Trading-Skript (`scripts/run_paper_session.sh`) erstellen, das eine 60‑min Session mit Mock-Exchange fährt.
  2. Assertions: mindestens ein Event `ORDER_FILLED`, keine verwaisten `pending_buy_intents`, Budget-Delta > 0, keine `ORDER_FAILED` ohne Fehlercode.
  3. Nach Deployment reale Session beobachten; Prometheus/StatsD-Metriken für `order_fail_rate`, `intent_cleanup_latency`, `budget_refresh_timeout_total` erfassen.
- **Akzeptanzkriterien:** Test-Skript schlägt fehl, wenn irgendein Intent hängen bleibt; Monitoring-Dashboard zeigt <10 % Order-Fail-Rate und keine Budget-Refresh-Timeouts.

> **Hinweis:** Reihenfolge 9.1 → 9.4 sind Blocker für produktives Trading. Schritte 9.5–9.7 können parallelisiert werden, sollten aber vor erneutem Live-Einsatz abgeschlossen sein.
