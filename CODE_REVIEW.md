# Code Review – Trading Bot Professional

Scope: end-to-end walkthrough of the trading pipeline (config → bootstrap → engine → order router → portfolio/state → terminal dashboard) with focus on configuration fidelity and dashboard wiring.

## Findings

### 1. `RESET_PORTFOLIO_ON_START` defaults to destroying user holdings (Critical)
- **Evidence:** `config.py:50-55` sets `RESET_PORTFOLIO_ON_START = True` even though the comment explicitly labels it “nur für Testing”. `main.py:597-600` unconditionally calls `portfolio.perform_startup_reset(preise)` whenever the flag is true.
- **Impact:** Every bot launch liquidates *all* non‑USDT assets and wipes state, even when the operator expects normal trading. This is catastrophic for live accounts and explains the empty holdings seen in recent sessions.
- **Recommendation:** Default the flag to `False`, gate it behind an explicit CLI/env confirmation (e.g., `CONFIRM_RESET=yes`), and surface a startup warning before executions. Add telemetry that refuses to start in LIVE mode if the reset flag is still enabled unintentionally.

### 2. Several documented config knobs are never read anywhere (High)
- **Evidence:** `config.py` defines multiple switches that the runtime ignores entirely:
  - `ORDER_FLOW_ENABLED` (`config.py:113`) – no `rg` hits outside the config file, so the advertised kill-switch cannot disable order placement.
  - Drop/market-data tuning parameters such as `DROP_SNAPSHOT_INTERVAL_MS` (line 101), `MD_USE_WEBSOCKET` (line 116), `ADAPTIVE_HOT_PERCENT`/`HOT_INTERVAL_MS`/`COLD_INTERVAL_MS` (lines 140‑142), and `MD_WS_FALLBACK_INTERVAL_MS` (line 145) also have zero usages in the codebase.
- **Impact:** Operators rely on CONFIG_README for supported controls, but toggling these values has *no* effect. This violates the requirement that everything in `config.py` must be “konsistent und vollständig implementiert”.
- **Recommendation:** Either (a) wire these settings into `services/market_data.py` (e.g., drive polling cadence, enable WS mode, honor HTTP fallback timings, guard order flow dispatch) or (b) remove them from config and documentation until the features exist. Additionally, extend `scripts/config_lint` to fail when a config attribute has no runtime reference to catch future drifts.

### 3. `ENABLE_RICH_TABLE` configuration is silently ignored (Medium)
- **Evidence:** Even though `config.py:526` exposes `ENABLE_RICH_TABLE`, the boot sequence forcibly overrides it (`main.py:816` assigns `ENABLE_RICH_TABLE = False` right before the feature check). As a result, the Rich FSM status table cannot be re-enabled from config despite the documentation in `docs/FSM_DEBUGGING.md`.
- **Impact:** Users cannot access the legacy terminal diagnostics when the new dashboard fails or when they explicitly want the FSM table; the setting is effectively dead.
- **Recommendation:** Respect the config value (e.g., `enable_rich_table = getattr(config_module, "ENABLE_RICH_TABLE", False)`), log a deprecation warning if necessary, and let operators opt back in. If the table is deprecated, remove the flag entirely to avoid confusion.

### 4. OrderRouter early exits never notify the intent subsystem (Critical)
- **Evidence:** `engine/buy_decision.py:505-538` stores every buy intent in `engine.pending_buy_intents` *before* dispatching it to the router. If the router bails out before reaching the retry loop—e.g., missing price (`services/order_router.py:382-390`) or failed budget reservation (`services/order_router.py:393-400`)—it simply logs and returns. There is no `_release_budget`, no `order.failed` publish, and therefore no call back into `engine._on_order_failed`.
- **Impact:** Those early failures leave intents stuck in `pending_buy_intents`, the dashboard shows phantom “active” intents, and the state file never clears them, which matches the stale intent accumulation observed in the latest session. The terminal dashboard and monitoring pipeline then misreport available slots and reserved budgets.
- **Recommendation:** Before every `return` path in `handle_intent`, emit an `order.failed` event (with reason `no_price` / `reserve_failed`), invoke `_release_budget` if any reservation happened, and ensure the engine’s intent state is cleared. Consider moving the `pending_buy_intents` entry creation *after* a successful reservation so that early validation failures never leak state.

### 5. Dashboard debug panel never finds logs (Medium)
- **Evidence:** `ui/dashboard.py:100-133` hard-codes glob patterns for `*.log`, but the project writes JSONL logs under `sessions/<session>/logs/*.jsonl` (`e.g., sessions/session_20251026_103545/logs/bot_log_…jsonl`, `events_…jsonl`, etc.). The helper therefore always returns “No log files found…”, so the debug footer is useless.
- **Impact:** Operators monitoring from the terminal cannot inspect any recent log excerpts, defeating the purpose of the debug panel and making incident triage harder.
- **Recommendation:** Update `get_log_tail` to include the actual JSONL files (e.g., `*.jsonl`, `system_*.jsonl.gz`), preferably prioritizing the current session folder already known via `config.LOG_DIR`. To keep the panel readable, decode JSONL entries into concise text instead of dumping raw JSON.

### 6. Dashboard/telemetry event bus lacks thread-safety (Medium)
- **Evidence:** `DashboardEventBus` (`ui/dashboard.py:41-60`) stores `events` and `last_event` without any lock. Producers from multiple threads (`services/market_data.py:545/573`, `engine/buy_decision.py:273/417/1041`, `engine/exit_handler.py:231`, etc.) call `emit_dashboard_event`, so concurrent writes to `last_event` and the deque can interleave.
- **Impact:** In stress scenarios (multiple triggers per second), the seen “Last Event” frequently lags or drops updates because one thread overwrites the `last_event` assignment performed by another mid-update. In CPython the `deque.append` is atomic, but updating `last_event` is not, leading to occasional truncated strings or stale timestamps on the dashboard.
- **Recommendation:** Guard `emit` with a `threading.Lock`, or move the bus onto `Queue` + dedicated consumer in the dashboard thread. That keeps the terminal display consistent and avoids subtle race conditions that are otherwise painful to reproduce.

## Suggested Next Steps
1. Prioritize the critical fixes (`RESET_PORTFOLIO_ON_START` default + router intent cleanup) before the next live session to prevent unintended liquidations and stale intents.
2. Decide which config toggles are genuinely supported; remove or implement the rest, then extend `config_lint` to detect orphaned settings.
3. Patch the dashboard (log discovery + event bus) so the terminal remains a reliable monitoring surface when Rich is enabled.

Addressing these items will make the process chain—from configuration through execution to UI—behave predictably and keep the terminal dashboard aligned with actual runtime state.
