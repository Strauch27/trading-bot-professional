# 📊 Trading Bot - Logging & Event Tracking Roadmap

**Status:** Phase 3 zu 85% abgeschlossen
**Letzte Aktualisierung:** 2025-10-12
**Version:** 1.0

---

## 📋 Inhaltsverzeichnis

1. [Übersicht](#übersicht)
2. [Aktueller Implementierungsstand](#aktueller-implementierungsstand)
3. [Phase 0: Basis Stabilisieren](#phase-0-basis-stabilisieren)
4. [Phase 1: Datenintegrität & Trigger-States](#phase-1-datenintegrität--trigger-states)
5. [Phase 2: Idempotenz, Retries, Rate-Limit-Sicherheit](#phase-2-idempotenz-retries-rate-limit-sicherheit)
6. [Phase 3: Risiko-Kontrolle & Portfolio](#phase-3-risiko-kontrolle--portfolio)
7. [Phase 4: Reconciliation & Replay](#phase-4-reconciliation--replay)
8. [Phase 5: Betriebsfähigkeit & Überwachung](#phase-5-betriebsfähigkeit--überwachung)
9. [Phase 6: Compliance & Hygiene](#phase-6-compliance--hygiene)
10. [Implementierungsplan](#implementierungsplan)

---

## Übersicht

Dieses Dokument beschreibt die vollständige Event-Tracking-Infrastruktur des Trading Bots mit strukturiertem JSONL-Logging für forensische Analyse, Debugging und Compliance.

### Event-Taxonomien

| Taxonomie | Zweck | Log-Datei |
|-----------|-------|-----------|
| **DECISION_LOG** | Buy/Sell Entscheidungen, Guards, Trigger, Sizing | `logs/decisions/decision.jsonl` |
| **ORDER_LOG** | Order Lifecycle (attempt, ack, fill, cancel, done) | `logs/orders/order.jsonl` |
| **TRACER_LOG** | Exchange API Requests/Responses | `logs/tracer/exchange.jsonl` |
| **AUDIT_LOG** | State Changes, Config, Exceptions, Ledger | `logs/audit/audit.jsonl` |
| **HEALTH_LOG** | Heartbeats, Rate Limits, System Metrics | `logs/health/health.jsonl` |

### Correlation IDs

Alle Events enthalten automatisch Correlation IDs via ContextVars:

```python
{
    "session_id": "sess_abc123",      # Bot-Session
    "decision_id": "dec_xyz789",      # Kauf-/Verkaufsentscheidung
    "order_req_id": "oreq_456def",    # Order Request
    "client_order_id": "coid_...",    # Client Order ID
    "exchange_order_id": "12345678"   # Exchange Order ID
}
```

---

## Aktueller Implementierungsstand

### ✅ Vollständig implementiert

#### Logging-Infrastruktur
- ✅ **JSONL Logging mit Rotation** - `core/logger_factory.py:99-121`
  - Daily Rotation um Mitternacht UTC
  - Automatische gzip-Kompression
  - 14 Tage History (konfigurierbar)
  - JsonlFormatter mit Nanosekunden-Timestamps

- ✅ **5 Event-Taxonomien** - `core/logger_factory.py:191-214`
  - `DECISION_LOG()` - Buy/Sell Decisions
  - `ORDER_LOG()` - Order Lifecycle
  - `TRACER_LOG()` - Exchange API Calls
  - `AUDIT_LOG()` - State/Config Changes
  - `HEALTH_LOG()` - System Health

- ✅ **ContextVars für Correlation IDs** - `core/trace_context.py:24-28`
  - Thread-safe ID propagation
  - Automatische Injection in alle Log-Events

- ✅ **Trace Context Manager** - `core/trace_context.py:70-171`
  - `with Trace(decision_id=...):` für automatisches ID-Tracking
  - Nested contexts unterstützt
  - Automatic cleanup

- ✅ **Pydantic Event Schemas** - `core/event_schemas.py:1-287`
  - Type-safe event validation
  - 20+ strukturierte Schemas
  - Consistent field naming

#### TracedExchange
- ✅ **CCXT Wrapper mit Request/Response Logging** - `core/exchange_tracer.py:42-467`
  - Alle API-Calls geloggt
  - Latency tracking (ms)
  - HTTP Status Codes
  - Rate Limit Remaining
  - Automatic retry detection
  - Sensitive field masking

#### Phase 3 Events (Sell & Portfolio)
- ✅ `guards_eval` - Market Quality Checks - `engine/buy_decision.py:103-120`
- ✅ `drop_trigger_eval` - Entry Signal Evaluation - `engine/buy_decision.py:259-276`
- ✅ `decision_outcome` - Buy/Skip/Blocked Decisions - `engine/buy_decision.py:148-156, 193-201, 243-251, 298-308, 332-340, 363-373`
- ✅ `sizing_calc` - Position Sizing mit Market Limits - `engine/buy_decision.py:508-529`
- ✅ `price_snapshot` - Bid/Ask vor Orders - `engine/buy_decision.py:657-669`
- ✅ `order_attempt` - Order Submission - `engine/buy_decision.py:671-682`
- ✅ `order_ack` - Exchange Confirmation - `engine/buy_decision.py:698-710`
- ✅ `order_done` - Order Completion (nur Buy) - `engine/buy_decision.py:884-900`
- ✅ `position_opened` - New Position Created - `engine/buy_decision.py:843-861`
- ✅ `position_closed` - Position Exited - `engine/exit_handler.py:120-153`
- ✅ `sell_trigger_eval` - Exit Signals:
  - TTL (Time-To-Live) - `services/exits.py:72-101`
  - ATR Stop-Loss - `engine/position_manager.py:122-147`
  - Trailing Stop - `engine/position_manager.py:180-206`
- ✅ `sell_sizing_calc` - Exit Sizing Validation - `services/exits.py:150-187`
- ✅ `trailing_update` - Trailing Stop Adjustments - `services/trailing.py:65-126`
- ✅ `portfolio_snapshot` - Equity Tracking - `core/utils/heartbeat_telemetry.py:485-512`
- ✅ `reconciliation_result` - Exchange Drift Detection - `core/portfolio/portfolio.py:765-783`

#### Config Tracking
- ✅ `config_snapshot` - Startup Config Capture - `core/logger_factory.py:343-450`
- ✅ `config_change` - Runtime Parameter Changes - `core/logger_factory.py:452-501`
- ✅ `config_drift` - Unexpected Config Modifications - `core/logger_factory.py:503-577`

#### Health Monitoring
- ✅ `rate_limit_hit` - API Rate Limit Events - `core/exchange_tracer.py:141-157`
- ✅ `exchange_call` - All API Requests/Responses - `core/exchange_tracer.py:174-188`
- ✅ Uncaught exception tracking - `core/logger_factory.py:306-336`

---

## Phase 0: Basis Stabilisieren

**Ziel:** Lückenlose End-to-End-Verfolgung aller Order-Flows
**Status:** 70% Complete
**Priorität:** 🟠 HIGH

### ✅ Bereits implementiert

| Event | Status | Location |
|-------|--------|----------|
| `order_attempt` | ✅ | `engine/buy_decision.py:671-682` |
| `order_ack` | ✅ | `engine/buy_decision.py:698-710` |
| `order_done` (buy only) | ✅ | `engine/buy_decision.py:884-900` |
| `price_snapshot` | ✅ | `engine/buy_decision.py:657-669` |

### ❌ TODO: Fehlende Events

#### TODO 1: `order_fill` Event

**Status:** ❌ Nicht implementiert
**Priorität:** HIGH
**Geschätzte Zeit:** 2 Stunden

**Schema:**
```python
class OrderFill(BaseModel):
    """Order fill event (partial or full)"""
    symbol: str
    exchange_order_id: str
    fill_qty: float
    fill_price: float
    fill_cost: float
    fee_quote: float
    is_full_fill: bool
    cumulative_filled: float
    remaining_qty: float
```

**Implementierung:**
```python
# File: services/exits.py oder trading/orders.py

def check_order_fills(order):
    """Check for new fills and log them"""
    filled_qty = order.get('filled', 0)
    total_qty = order.get('amount', 0)

    if filled_qty > 0:
        from core.event_schemas import OrderFill
        from core.logger_factory import ORDER_LOG, log_event
        from core.trace_context import Trace

        order_fill = OrderFill(
            symbol=order['symbol'],
            exchange_order_id=order['id'],
            fill_qty=filled_qty,
            fill_price=order.get('average', 0),
            fill_cost=filled_qty * order.get('average', 0),
            fee_quote=order.get('fee', {}).get('cost', 0),
            is_full_fill=(filled_qty >= total_qty),
            cumulative_filled=filled_qty,
            remaining_qty=total_qty - filled_qty
        )

        with Trace(exchange_order_id=order['id']):
            log_event(ORDER_LOG(), "order_fill", **order_fill.model_dump())
```

**Zu modifizierende Dateien:**
- `services/exits.py` - Nach order placement check
- `trading/orders.py` - In order status polling
- `core/event_schemas.py` - Add `OrderFill` schema

---

#### TODO 2: `order_cancel` Event

**Status:** ❌ Nicht implementiert
**Priorität:** HIGH
**Geschätzte Zeit:** 2 Stunden

**Schema:**
```python
class OrderCancel(BaseModel):
    """Order cancellation event"""
    symbol: str
    exchange_order_id: str
    reason: str  # "manual_cancel", "timeout", "replaced", "insufficient_margin"
    filled_before_cancel: float
    remaining_qty: float
    age_seconds: Optional[float] = None
```

**Implementierung:**
```python
# File: trading/orders.py

def cancel_order_with_tracking(exchange, order_id, symbol, reason="manual_cancel"):
    """Cancel order and log cancellation event"""
    from core.event_schemas import OrderCancel
    from core.logger_factory import ORDER_LOG, log_event
    from core.trace_context import Trace

    # Fetch order before cancel to get filled amount
    order = exchange.fetch_order(order_id, symbol)

    # Cancel order
    cancel_result = exchange.cancel_order(order_id, symbol)

    # Log cancellation
    order_cancel = OrderCancel(
        symbol=symbol,
        exchange_order_id=order_id,
        reason=reason,
        filled_before_cancel=order.get('filled', 0),
        remaining_qty=order.get('remaining', 0),
        age_seconds=(time.time() - order.get('timestamp', 0) / 1000) if order.get('timestamp') else None
    )

    with Trace(exchange_order_id=order_id):
        log_event(ORDER_LOG(), "order_cancel", **order_cancel.model_dump())

    return cancel_result
```

**Zu modifizierende Dateien:**
- `trading/orders.py` - Create `cancel_order_with_tracking` wrapper
- `services/exits.py` - Use wrapper for all cancellations
- `engine/buy_decision.py` - Use wrapper for buy order timeouts
- `core/event_schemas.py` - Add `OrderCancel` schema

---

#### TODO 3: Systematische `order_done` für Sell Orders

**Status:** ❌ Teilweise implementiert (nur Buy)
**Priorität:** HIGH
**Geschätzte Zeit:** 2 Stunden

**Problem:**
`order_done` wird nur für Buy Orders geloggt (`engine/buy_decision.py:884-900`), aber nicht für Sell Orders.

**Implementierung:**
```python
# File: engine/exit_handler.py

def handle_exit_fill(self, symbol: str, result: ExitResult, reason: str):
    """Handle exit order fill - remove position"""
    # ... existing code ...

    # Phase 3: Log order_done event for sell order
    order_timestamp = result.order.get('timestamp', 0) / 1000 if result.order and result.order.get('timestamp') else None
    latency_ms_total = int((time.time() - order_timestamp) * 1000) if order_timestamp else None

    with Trace(decision_id=position_data.get('decision_id'),
               exchange_order_id=result.order.get('id') if result.order else None):
        log_event(
            ORDER_LOG(),
            "order_done",
            symbol=symbol,
            side="sell",
            final_status="filled",
            filled_qty=result.filled_amount,
            avg_price=result.avg_price,
            total_proceeds=result.filled_amount * result.avg_price,
            fee_quote=exit_fee,
            latency_ms_total=latency_ms_total,
            reason=reason
        )

    # ... rest of existing code ...
```

**Zu modifizierende Dateien:**
- `engine/exit_handler.py:82-172` - Add `order_done` event nach fill handling
- `services/exits.py` - Ensure all exit paths lead to `order_done`

---

### 📋 Phase 0 Checkliste

- [x] `order_attempt` Event implementiert
- [x] `order_ack` Event implementiert
- [x] `price_snapshot` Event implementiert
- [x] `order_done` für Buy Orders implementiert
- [ ] **TODO:** `order_fill` Event implementieren
- [ ] **TODO:** `order_cancel` Event implementieren
- [ ] **TODO:** `order_done` für Sell Orders implementieren
- [ ] **TODO:** Cancel tracking mit Gründen (timeout, replaced, manual)

---

## Phase 1: Datenintegrität & Trigger-States

**Ziel:** Systematische Trigger-Evaluation für alle Exit-Bedingungen
**Status:** 40% Complete
**Priorität:** 🟡 MEDIUM

### ✅ Bereits implementiert

| Event | Status | Location |
|-------|--------|----------|
| `sell_trigger_eval` (TTL) | ✅ | `services/exits.py:72-101` |
| `sell_trigger_eval` (ATR SL) | ✅ | `engine/position_manager.py:122-147` |
| `sell_trigger_eval` (Trailing) | ✅ | `engine/position_manager.py:180-206` |

### ❌ TODO: Fehlende Events

#### TODO 4: `sell_trigger_eval` für TP/SL (Percentage-based)

**Status:** ❌ Nur in FSM implementiert (inaktiv)
**Priorität:** HIGH
**Geschätzte Zeit:** 3 Stunden

**Problem:**
TP/SL Logic existiert nur in `core/fsm/trading_fsm_engine.py` (FSM_ENABLED=False in config).
Main TradingEngine in `main.py` nutzt diese nicht.

**Implementierung:**
```python
# File: engine/position_manager.py

def evaluate_position_exits(self, symbol: str, data: Dict, current_price: float):
    """Evaluate exit conditions with deterministic guards and queue signals"""
    try:
        # ... existing ATR and Trailing checks ...

        # NEW: TP/SL Percentage-based Evaluation
        entry_price = data.get('buying_price', 0)
        if entry_price <= 0:
            return

        # Get TP/SL thresholds from config
        tp_threshold = getattr(config, 'TAKE_PROFIT_THRESHOLD', 1.005)
        sl_threshold = getattr(config, 'STOP_LOSS_THRESHOLD', 0.990)

        price_ratio = current_price / entry_price
        unrealized_pct = (price_ratio - 1.0) * 100

        # Check Take Profit
        if price_ratio >= tp_threshold:
            try:
                from core.event_schemas import SellTriggerEval
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace

                tp_eval = SellTriggerEval(
                    symbol=symbol,
                    trigger="tp",
                    entry_price=entry_price,
                    current_price=current_price,
                    unrealized_pct=unrealized_pct,
                    threshold=tp_threshold,
                    hit=True,
                    reason="take_profit_hit"
                )

                decision_id = data.get('decision_id')
                with Trace(decision_id=decision_id) if decision_id else Trace():
                    log_event(DECISION_LOG(), "sell_trigger_eval", **tp_eval.model_dump())

                # Queue exit
                logger.info(f"[DECISION_END] {symbol} SELL | Take Profit Hit")
                success = self.engine.exit_manager.queue_exit_signal(
                    symbol=symbol,
                    reason="take_profit_hit",
                    position_data=data,
                    current_price=current_price
                )
                if success:
                    logger.info(f"Exit signal queued for {symbol}: take_profit_hit")
                return

            except Exception as e:
                logger.debug(f"Failed to log sell_trigger_eval (TP) for {symbol}: {e}")

        # Check Stop Loss
        if price_ratio <= sl_threshold:
            try:
                from core.event_schemas import SellTriggerEval
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace

                sl_eval = SellTriggerEval(
                    symbol=symbol,
                    trigger="sl",
                    entry_price=entry_price,
                    current_price=current_price,
                    unrealized_pct=unrealized_pct,
                    threshold=sl_threshold,
                    hit=True,
                    reason="stop_loss_hit"
                )

                decision_id = data.get('decision_id')
                with Trace(decision_id=decision_id) if decision_id else Trace():
                    log_event(DECISION_LOG(), "sell_trigger_eval", **sl_eval.model_dump())

                # Queue exit
                logger.info(f"[DECISION_END] {symbol} SELL | Stop Loss Hit")
                success = self.engine.exit_manager.queue_exit_signal(
                    symbol=symbol,
                    reason="stop_loss_hit",
                    position_data=data,
                    current_price=current_price
                )
                if success:
                    logger.info(f"Exit signal queued for {symbol}: stop_loss_hit")
                return

            except Exception as e:
                logger.debug(f"Failed to log sell_trigger_eval (SL) for {symbol}: {e}")

        # ... continue with existing checks (ATR, Trailing, Legacy) ...

    except Exception as e:
        logger.error(f"Exit evaluation error for {symbol}: {e}")
```

**Zu modifizierende Dateien:**
- `engine/position_manager.py:106-226` - Add TP/SL checks BEFORE ATR/Trailing checks

---

#### TODO 5: `position_updated` Event

**Status:** ❌ Nicht implementiert
**Priorität:** MEDIUM
**Geschätzte Zeit:** 2 Stunden

**Schema:**
```python
class PositionUpdated(BaseModel):
    """Position state update during lifetime"""
    symbol: str
    qty: float
    unrealized_pnl: float
    unrealized_pct: float
    peak_price: Optional[float] = None
    trailing_stop: Optional[float] = None
    age_minutes: Optional[float] = None
```

**Implementierung:**
```python
# File: engine/position_manager.py

def update_unrealized_pnl(self, symbol: str, data: Dict, current_price: float):
    """Update unrealized PnL via PnL Service"""
    try:
        if not data.get('amount') or not data.get('buying_price'):
            return

        unrealized_pnl = self.engine.pnl_service.set_unrealized_position(
            symbol=symbol,
            quantity=data['amount'],
            avg_entry_price=data['buying_price'],
            current_price=current_price,
            entry_fee_per_unit=data.get('entry_fee_per_unit', 0)
        )

        data['unrealized_pnl'] = unrealized_pnl

        # NEW: Log position_updated event
        try:
            from core.event_schemas import PositionUpdated
            from core.logger_factory import DECISION_LOG, log_event
            from core.trace_context import Trace

            entry_price = data['buying_price']
            unrealized_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            age_minutes = (time.time() - data.get('time', 0)) / 60 if data.get('time') else None

            position_updated = PositionUpdated(
                symbol=symbol,
                qty=data['amount'],
                unrealized_pnl=unrealized_pnl,
                unrealized_pct=unrealized_pct,
                peak_price=data.get('peak_price'),
                trailing_stop=data.get('trailing_stop'),
                age_minutes=age_minutes
            )

            decision_id = data.get('decision_id')
            with Trace(decision_id=decision_id) if decision_id else Trace():
                log_event(DECISION_LOG(), "position_updated", **position_updated.model_dump())

        except Exception as e:
            logger.debug(f"Failed to log position_updated for {symbol}: {e}")

    except Exception as e:
        logger.error(f"PnL update error for {symbol}: {e}")
```

**Zu modifizierende Dateien:**
- `engine/position_manager.py:247-265` - Add `position_updated` event
- `core/event_schemas.py` - Add `PositionUpdated` schema (already exists)

---

#### TODO 6: `risk_limits_eval` Event

**Status:** ❌ Nicht implementiert
**Priorität:** MEDIUM
**Geschätzte Zeit:** 3 Stunden

**Schema:**
```python
class RiskLimitsEval(BaseModel):
    """Risk limit checks before order placement"""
    symbol: str
    limit_checks: List[Dict[str, Any]]  # [{"limit": "max_exposure", "value": 0.45, "threshold": 0.50, "hit": False}, ...]
    all_passed: bool
    blocking_limit: Optional[str] = None
```

**Implementierung:**
```python
# File: core/risk_limits.py (NEW FILE)

class RiskLimitChecker:
    """Check portfolio risk limits before order placement"""

    def __init__(self, portfolio, config):
        self.portfolio = portfolio
        self.config = config

    def check_limits(self, symbol: str, order_value_usdt: float) -> tuple[bool, list]:
        """
        Check all risk limits.

        Returns:
            (all_passed, limit_checks)
        """
        limit_checks = []

        # 1. Max Exposure Check
        current_exposure = self.portfolio.get_total_exposure()
        total_budget = self.portfolio.get_balance("USDT")
        new_exposure = (current_exposure + order_value_usdt) / (total_budget + current_exposure)
        max_exposure = getattr(self.config, 'MAX_PORTFOLIO_EXPOSURE_PCT', 0.80)

        limit_checks.append({
            "limit": "max_exposure",
            "value": new_exposure,
            "threshold": max_exposure,
            "hit": new_exposure > max_exposure
        })

        # 2. Max Positions Check
        current_positions = len(self.portfolio.positions)
        max_positions = getattr(self.config, 'MAX_TRADES', 10)

        limit_checks.append({
            "limit": "max_positions",
            "value": current_positions,
            "threshold": max_positions,
            "hit": current_positions >= max_positions
        })

        # 3. Daily Drawdown Check
        daily_pnl_pct = self.portfolio.get_daily_pnl_pct()
        max_drawdown = getattr(self.config, 'MAX_DAILY_DRAWDOWN_PCT', 0.08)

        limit_checks.append({
            "limit": "daily_drawdown",
            "value": abs(daily_pnl_pct) if daily_pnl_pct < 0 else 0,
            "threshold": max_drawdown,
            "hit": daily_pnl_pct < -max_drawdown
        })

        # 4. Max Trades Per Day Check
        daily_trade_count = self.portfolio.get_daily_trade_count()
        max_daily_trades = getattr(self.config, 'MAX_TRADES_PER_DAY', 120)

        limit_checks.append({
            "limit": "max_trades_per_day",
            "value": daily_trade_count,
            "threshold": max_daily_trades,
            "hit": daily_trade_count >= max_daily_trades
        })

        all_passed = all(not check['hit'] for check in limit_checks)
        return all_passed, limit_checks

# File: engine/buy_decision.py - Add before order placement

def execute_buy_order(self, symbol: str, coin_data: Dict, current_price: float, signal: str):
    """Execute buy order via Order Service"""
    decision_id = self.engine.current_decision_id or new_decision_id()

    try:
        quote_budget = self._calculate_position_size(...)

        # NEW: Risk Limits Check
        from core.risk_limits import RiskLimitChecker
        from core.event_schemas import RiskLimitsEval
        from core.logger_factory import DECISION_LOG, log_event
        from core.trace_context import Trace

        risk_checker = RiskLimitChecker(self.engine.portfolio, config)
        all_passed, limit_checks = risk_checker.check_limits(symbol, quote_budget)

        risk_eval = RiskLimitsEval(
            symbol=symbol,
            limit_checks=limit_checks,
            all_passed=all_passed,
            blocking_limit=[c['limit'] for c in limit_checks if c['hit']][0] if not all_passed else None
        )

        with Trace(decision_id=decision_id):
            log_event(DECISION_LOG(), "risk_limits_eval", **risk_eval.model_dump())

        if not all_passed:
            logger.info(f"BUY BLOCKED {symbol} - Risk limit exceeded: {risk_eval.blocking_limit}")
            return

        # Continue with order placement...
```

**Zu erstellende Dateien:**
- `core/risk_limits.py` - NEW FILE mit `RiskLimitChecker` class
- `core/event_schemas.py` - Add `RiskLimitsEval` schema (already exists)

**Zu modifizierende Dateien:**
- `engine/buy_decision.py:379-446` - Add risk limits check before order placement
- `config.py` - Add risk limit thresholds if missing

---

### 📋 Phase 1 Checkliste

- [x] `sell_trigger_eval` (TTL) implementiert
- [x] `sell_trigger_eval` (ATR) implementiert
- [x] `sell_trigger_eval` (Trailing) implementiert
- [ ] **TODO:** `sell_trigger_eval` (TP/SL percentage-based) implementieren
- [ ] **TODO:** `position_updated` Event implementieren
- [ ] **TODO:** `risk_limits_eval` Event implementieren

---

## Phase 2: Idempotenz, Retries, Rate-Limit-Sicherheit

**Ziel:** Sichere Order-Platzierung mit Deduplication und Retry-Tracking
**Status:** 10% Complete
**Priorität:** 🔴 CRITICAL

### ✅ Bereits implementiert

| Feature | Status | Location |
|---------|--------|----------|
| Client Order ID Generation | ✅ | `core/trace_context.py:65-67` |
| Rate Limit Hit Logging | ✅ | `core/exchange_tracer.py:141-157` |

### ❌ TODO: Fehlende Features

#### TODO 7: Idempotency Store mit Persistence

**Status:** ❌ Nicht implementiert
**Priorität:** CRITICAL
**Geschätzte Zeit:** 4 Stunden

**Problem:**
Aktuell werden `order_req_id` generiert, aber nicht persistiert. Bei Netzwerkfehlern oder Restarts kann derselbe Order-Request mehrfach gesendet werden → Doppel-Orders!

**Implementierung:**

```python
# File: core/idempotency.py (NEW FILE)

import sqlite3
import time
import logging
from typing import Optional, Tuple
from pathlib import Path

from core.logger_factory import AUDIT_LOG, log_event
from core.trace_context import Trace

logger = logging.getLogger(__name__)


class IdempotencyStore:
    """
    Persistent idempotency key storage for order deduplication.

    Prevents duplicate orders by tracking order_req_id → exchange_order_id mappings.
    """

    def __init__(self, db_path: str = "state/idempotency.db"):
        """Initialize idempotency store with SQLite backend"""
        self.db_path = db_path

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._create_table()

    def _create_table(self):
        """Create idempotency_keys table if not exists"""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                order_req_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                price REAL,
                exchange_order_id TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                completed_at REAL
            )
        """)
        self.db.commit()

        # Create index for fast lookups
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exchange_order_id
            ON idempotency_keys(exchange_order_id)
        """)
        self.db.commit()

    def register_order(
        self,
        order_req_id: str,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None
    ) -> Optional[str]:
        """
        Register order attempt - returns existing exchange_order_id if duplicate.

        Returns:
            None if new order (proceed with placement)
            exchange_order_id (str) if duplicate (skip placement, return existing)
        """
        # Check for existing order
        cursor = self.db.execute(
            "SELECT exchange_order_id, status, created_at FROM idempotency_keys WHERE order_req_id = ?",
            (order_req_id,)
        )
        existing = cursor.fetchone()

        if existing:
            exchange_order_id, status, created_at = existing
            age_seconds = time.time() - created_at

            logger.warning(
                f"Duplicate order blocked: {order_req_id} → {exchange_order_id} "
                f"(status: {status}, age: {age_seconds:.1f}s)"
            )

            # Log duplicate detection event
            with Trace(order_req_id=order_req_id):
                log_event(
                    AUDIT_LOG(),
                    "duplicate_order_blocked",
                    order_req_id=order_req_id,
                    symbol=symbol,
                    side=side,
                    existing_exchange_order_id=exchange_order_id,
                    existing_status=status,
                    age_seconds=age_seconds,
                    level=logging.WARNING
                )

            return exchange_order_id  # Return existing order ID

        # Register new order (pending state)
        now = time.time()
        self.db.execute(
            """
            INSERT INTO idempotency_keys
            (order_req_id, symbol, side, amount, price, exchange_order_id, status, created_at, updated_at, completed_at)
            VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?, ?, NULL)
            """,
            (order_req_id, symbol, side, amount, price, now, now)
        )
        self.db.commit()

        return None  # No duplicate, proceed with order placement

    def update_order_status(
        self,
        order_req_id: str,
        exchange_order_id: str,
        status: str
    ):
        """Update order status after exchange response"""
        now = time.time()
        completed_at = now if status in ['filled', 'canceled', 'expired', 'rejected'] else None

        self.db.execute(
            """
            UPDATE idempotency_keys
            SET exchange_order_id = ?, status = ?, updated_at = ?, completed_at = ?
            WHERE order_req_id = ?
            """,
            (exchange_order_id, status, now, completed_at, order_req_id)
        )
        self.db.commit()

    def get_order_by_req_id(self, order_req_id: str) -> Optional[dict]:
        """Get order details by order_req_id"""
        cursor = self.db.execute(
            "SELECT * FROM idempotency_keys WHERE order_req_id = ?",
            (order_req_id,)
        )
        row = cursor.fetchone()

        if not row:
            return None

        return {
            'order_req_id': row[0],
            'symbol': row[1],
            'side': row[2],
            'amount': row[3],
            'price': row[4],
            'exchange_order_id': row[5],
            'status': row[6],
            'created_at': row[7],
            'updated_at': row[8],
            'completed_at': row[9]
        }

    def cleanup_old_orders(self, max_age_days: int = 7):
        """Remove completed orders older than max_age_days"""
        cutoff_time = time.time() - (max_age_days * 86400)

        deleted = self.db.execute(
            """
            DELETE FROM idempotency_keys
            WHERE completed_at IS NOT NULL AND completed_at < ?
            """,
            (cutoff_time,)
        ).rowcount

        self.db.commit()

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old idempotency records (older than {max_age_days}d)")

        return deleted
```

**Integration in Order Placement:**

```python
# File: engine/buy_decision.py

def _place_buy_order(self, symbol: str, amount: float, current_price: float, ...):
    """Place buy order with appropriate strategy"""
    order_start_time = time.time()

    # Phase 1: Log structured order events
    order_req_id = new_order_req_id()

    # NEW: Idempotency Check
    from core.idempotency import get_idempotency_store

    idempotency_store = get_idempotency_store()
    existing_order_id = idempotency_store.register_order(
        order_req_id=order_req_id,
        symbol=symbol,
        side="buy",
        amount=amount,
        price=aggressive_price
    )

    if existing_order_id:
        # Duplicate detected - fetch and return existing order
        logger.info(f"Returning existing order {existing_order_id} for {order_req_id}")
        try:
            existing_order = self.engine.exchange_adapter.fetch_order(existing_order_id, symbol)
            return existing_order
        except Exception as e:
            logger.error(f"Failed to fetch existing order {existing_order_id}: {e}")
            return None

    with Trace(decision_id=decision_id, order_req_id=order_req_id, client_order_id=client_order_id):
        # ... existing order_attempt logging ...

        try:
            order = self.engine.order_service.place_limit_ioc(...)

            # Update idempotency store with exchange order ID
            if order and order.get('id'):
                idempotency_store.update_order_status(
                    order_req_id=order_req_id,
                    exchange_order_id=order['id'],
                    status=order.get('status', 'open')
                )

            # ... existing order_ack logging ...

        except Exception as e:
            # Update idempotency store with failed status
            idempotency_store.update_order_status(
                order_req_id=order_req_id,
                exchange_order_id="",
                status="failed"
            )
            raise

    return order

# File: core/idempotency.py - Add singleton getter

_idempotency_store = None

def get_idempotency_store() -> IdempotencyStore:
    """Get global idempotency store singleton"""
    global _idempotency_store
    if _idempotency_store is None:
        _idempotency_store = IdempotencyStore()
    return _idempotency_store
```

**Zu erstellende Dateien:**
- `core/idempotency.py` - NEW FILE mit `IdempotencyStore` class
- `state/idempotency.db` - SQLite database (automatisch erstellt)

**Zu modifizierende Dateien:**
- `engine/buy_decision.py:621-746` - Wrap order placement with idempotency checks
- `services/exits.py` - Add idempotency to sell order placement
- `main.py` - Initialize idempotency store on startup
- `core/event_schemas.py` - Add `DuplicateOrderBlocked` schema

---

#### TODO 8: Retry Tracking mit Backoff

**Status:** ❌ Nicht implementiert
**Priorität:** CRITICAL
**Geschätzte Zeit:** 3 Stunden

**Schema:**
```python
class RetryAttempt(BaseModel):
    """Retry attempt tracking"""
    symbol: str
    operation: str  # "place_order", "cancel_order", "fetch_balance", etc.
    attempt: int
    max_retries: int
    error_class: str
    error_message: str
    backoff_ms: int
    will_retry: bool
```

**Implementierung:**

```python
# File: trading/orders.py (NEW FILE or add to existing)

import time
import logging
from typing import Callable, Any
from functools import wraps

from core.logger_factory import ORDER_LOG, log_event
from core.trace_context import Trace

logger = logging.getLogger(__name__)


def retry_with_backoff(
    max_retries: int = 3,
    base_backoff_ms: int = 1000,
    exponential: bool = True,
    retry_on: tuple = (Exception,)
):
    """
    Decorator for retrying operations with exponential backoff and logging.

    Args:
        max_retries: Maximum number of retry attempts
        base_backoff_ms: Base backoff time in milliseconds
        exponential: Use exponential backoff (2^attempt * base)
        retry_on: Tuple of exception types to retry on
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(1, max_retries + 1):
                try:
                    # Execute function
                    result = func(*args, **kwargs)

                    # Success - log if this was a retry
                    if attempt > 1:
                        logger.info(f"{func.__name__} succeeded on attempt {attempt}/{max_retries}")

                    return result

                except retry_on as e:
                    last_exception = e

                    # Calculate backoff
                    if exponential:
                        backoff_ms = base_backoff_ms * (2 ** (attempt - 1))
                    else:
                        backoff_ms = base_backoff_ms

                    will_retry = (attempt < max_retries)

                    # Extract symbol if available
                    symbol = kwargs.get('symbol') or (args[0] if args else None)

                    # Log retry attempt
                    try:
                        from core.event_schemas import RetryAttempt

                        retry_event = RetryAttempt(
                            symbol=str(symbol) if symbol else "unknown",
                            operation=func.__name__,
                            attempt=attempt,
                            max_retries=max_retries,
                            error_class=type(e).__name__,
                            error_message=str(e),
                            backoff_ms=backoff_ms,
                            will_retry=will_retry
                        )

                        with Trace():
                            log_event(
                                ORDER_LOG(),
                                "retry_attempt",
                                **retry_event.model_dump(),
                                level=logging.WARNING
                            )

                    except Exception as log_error:
                        logger.debug(f"Failed to log retry_attempt: {log_error}")

                    if will_retry:
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt}/{max_retries}): {type(e).__name__}: {e}. "
                            f"Retrying in {backoff_ms}ms..."
                        )
                        time.sleep(backoff_ms / 1000.0)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} attempts: {type(e).__name__}: {e}"
                        )
                        raise

            # Should never reach here, but just in case
            raise last_exception

        return wrapper
    return decorator


# Example usage:

@retry_with_backoff(max_retries=3, base_backoff_ms=500, retry_on=(ccxt.NetworkError, ccxt.ExchangeNotAvailable))
def place_order_safe(exchange, symbol, type, side, amount, price=None):
    """Place order with automatic retry on network errors"""
    return exchange.create_order(symbol, type, side, amount, price)


@retry_with_backoff(max_retries=5, base_backoff_ms=2000, retry_on=(ccxt.RateLimitExceeded,))
def fetch_balance_safe(exchange):
    """Fetch balance with retry on rate limits"""
    return exchange.fetch_balance()
```

**Integration:**

```python
# File: engine/buy_decision.py

def _place_buy_order(self, ...):
    """Place buy order with retry logic"""

    # Use retry wrapper for order placement
    try:
        from trading.orders import place_order_safe

        order = place_order_safe(
            self.engine.exchange_adapter._exchange,  # Underlying CCXT instance
            symbol=symbol,
            type="limit",
            side="buy",
            amount=amount,
            price=aggressive_price
        )

    except Exception as e:
        # All retries exhausted
        logger.error(f"Order placement failed after retries: {e}")
        raise

    return order
```

**Zu erstellende Dateien:**
- `trading/orders.py` - NEW FILE mit `retry_with_backoff` decorator
- `core/event_schemas.py` - Add `RetryAttempt` schema

**Zu modifizierende Dateien:**
- `engine/buy_decision.py:621-746` - Use retry wrapper for order placement
- `services/exits.py` - Use retry wrapper for sell orders
- `adapters/exchange.py` - Use retry wrapper for critical API calls

---

#### TODO 9: Order Deduplication via Client Order ID

**Status:** ⚠️ Teilweise (Client Order IDs werden generiert, aber nicht konsequent für Deduplication genutzt)
**Priorität:** HIGH
**Geschätzte Zeit:** 2 Stunden

**Problem:**
Client Order IDs werden generiert (`core/trace_context.py:65-67`) aber nicht systematisch für Deduplication genutzt.

**Implementierung:**

```python
# File: trading/orders.py

def verify_order_not_duplicate(exchange, client_order_id: str, symbol: str) -> Optional[dict]:
    """
    Check if order with client_order_id already exists on exchange.

    Returns:
        None if no duplicate
        Order dict if duplicate found
    """
    try:
        # Fetch recent orders
        orders = exchange.fetch_orders(symbol, limit=50)

        # Check for matching client order ID
        for order in orders:
            if order.get('clientOrderId') == client_order_id:
                logger.warning(f"Duplicate order detected via clientOrderId: {client_order_id}")

                # Log duplicate detection
                with Trace(client_order_id=client_order_id, exchange_order_id=order.get('id')):
                    log_event(
                        AUDIT_LOG(),
                        "duplicate_order_detected_exchange",
                        symbol=symbol,
                        client_order_id=client_order_id,
                        exchange_order_id=order.get('id'),
                        status=order.get('status'),
                        level=logging.WARNING
                    )

                return order

        return None

    except Exception as e:
        logger.debug(f"Failed to check for duplicate orders: {e}")
        return None


# File: engine/buy_decision.py

def _place_buy_order(self, ...):
    """Place buy order with deduplication check"""

    # Generate client order ID
    client_order_id = new_client_order_id(decision_id, "buy")

    # Check for existing order on exchange
    existing_order = verify_order_not_duplicate(
        self.engine.exchange_adapter._exchange,
        client_order_id,
        symbol
    )

    if existing_order:
        logger.info(f"Returning existing order from exchange: {existing_order.get('id')}")
        return existing_order

    # Proceed with order placement...
```

**Zu modifizierende Dateien:**
- `trading/orders.py` - Add `verify_order_not_duplicate` function
- `engine/buy_decision.py:621-746` - Add deduplication check before placement
- `services/exits.py` - Add deduplication check for sell orders

---

### 📋 Phase 2 Checkliste

- [x] Client Order ID Generation implementiert
- [x] Rate Limit Hit Logging implementiert
- [ ] **TODO:** Idempotency Store mit SQLite Persistence implementieren
- [ ] **TODO:** Retry Tracking mit exponential backoff implementieren
- [ ] **TODO:** Order Deduplication via Exchange API implementieren
- [ ] **TODO:** Retry-specific events (retry_attempt) implementieren

---

## Phase 3: Risiko-Kontrolle & Portfolio

**Ziel:** Vollständiges Portfolio-Tracking mit Risk Controls
**Status:** 85% Complete ✅
**Priorität:** 🟢 LOW (bereits größtenteils fertig)

### ✅ Bereits implementiert

| Event | Status | Location |
|-------|--------|----------|
| `portfolio_snapshot` | ✅ | `core/utils/heartbeat_telemetry.py:485-512` |
| `position_opened` | ✅ | `engine/buy_decision.py:843-861` |
| `position_closed` | ✅ | `engine/exit_handler.py:120-153` |
| `reconciliation_result` | ✅ | `core/portfolio/portfolio.py:765-783` |
| `trailing_update` | ✅ | `services/trailing.py:65-126` |
| `sell_trigger_eval` (partial) | ✅ | Multiple files |

### ❌ TODO: Fehlende Events

#### TODO 10: `position_updated` Event

**Status:** ❌ Nicht implementiert
**Siehe:** [Phase 1 - TODO 5](#todo-5-position_updated-event)

#### TODO 11: `risk_limits_eval` Event

**Status:** ❌ Nicht implementiert
**Siehe:** [Phase 1 - TODO 6](#todo-6-risk_limits_eval-event)

### 📋 Phase 3 Checkliste

- [x] `portfolio_snapshot` implementiert
- [x] `position_opened` implementiert
- [x] `position_closed` implementiert
- [x] `reconciliation_result` implementiert
- [x] `trailing_update` implementiert
- [x] `sell_trigger_eval` (TTL, ATR, Trailing) implementiert
- [ ] **TODO:** `position_updated` implementieren (siehe Phase 1)
- [ ] **TODO:** `risk_limits_eval` implementieren (siehe Phase 1)

---

## Phase 4: Reconciliation & Replay

**Ziel:** Vollständige State-Reconstruction aus Logs
**Status:** 5% Complete
**Priorität:** 🟡 MEDIUM

### ✅ Bereits implementiert

| Feature | Status | Location |
|---------|--------|----------|
| Basic Reconciliation | ✅ | `core/portfolio/portfolio.py:765-783` |

### ❌ TODO: Fehlende Features

#### TODO 12: Double-Entry Ledger System

**Status:** ❌ Nicht implementiert
**Priorität:** MEDIUM
**Geschätzte Zeit:** 6 Stunden

**Schema:**
```python
class LedgerEntry(BaseModel):
    """Double-entry ledger transaction"""
    timestamp: float
    transaction_id: str  # Unique per trade
    account: str  # "asset:BTC/USDT", "cash:USDT", "fees:trading"
    debit: float
    credit: float
    balance_after: float
    symbol: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
```

**Implementierung:**

```python
# File: core/ledger.py (NEW FILE)

import sqlite3
import time
import logging
from typing import List, Dict, Optional
from pathlib import Path
import uuid

from core.logger_factory import AUDIT_LOG, log_event
from core.trace_context import Trace

logger = logging.getLogger(__name__)


class DoubleEntryLedger:
    """
    Double-entry accounting ledger for portfolio tracking.

    Every trade creates balanced debit/credit entries across:
    - Asset accounts (inventory)
    - Cash accounts (USDT balance)
    - Fee accounts (expenses)
    """

    def __init__(self, db_path: str = "state/ledger.db"):
        """Initialize ledger with SQLite backend"""
        self.db_path = db_path

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        """Create ledger tables"""
        # Ledger entries
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                transaction_id TEXT NOT NULL,
                account TEXT NOT NULL,
                debit REAL NOT NULL,
                credit REAL NOT NULL,
                balance_after REAL NOT NULL,
                symbol TEXT,
                side TEXT,
                qty REAL,
                price REAL,
                metadata TEXT
            )
        """)

        # Account balances (current state)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS account_balances (
                account TEXT PRIMARY KEY,
                balance REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        self.db.commit()

        # Create indexes
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_transaction_id ON ledger_entries(transaction_id)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_entries(timestamp)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_account ON ledger_entries(account)")
        self.db.commit()

    def record_trade(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        timestamp: Optional[float] = None
    ):
        """
        Record trade as double-entry ledger transaction.

        Buy Trade:
            - Debit: Asset (increase inventory)
            - Credit: Cash (decrease USDT)
            - Credit: Fees (expense)

        Sell Trade:
            - Debit: Cash (increase USDT)
            - Credit: Asset (decrease inventory)
            - Credit: Fees (expense)
        """
        timestamp = timestamp or time.time()
        transaction_id = f"trade_{uuid.uuid4().hex[:12]}"

        notional = qty * price

        # Define entries
        if side.lower() == "buy":
            entries = [
                {
                    "account": f"asset:{symbol}",
                    "debit": notional,
                    "credit": 0,
                    "description": f"Buy {qty} {symbol} @ {price}"
                },
                {
                    "account": "cash:USDT",
                    "debit": 0,
                    "credit": notional,
                    "description": f"Pay {notional} USDT"
                },
                {
                    "account": "fees:trading",
                    "debit": 0,
                    "credit": fee,
                    "description": f"Trading fee {fee} USDT"
                }
            ]
        else:  # sell
            entries = [
                {
                    "account": "cash:USDT",
                    "debit": notional,
                    "credit": 0,
                    "description": f"Receive {notional} USDT"
                },
                {
                    "account": f"asset:{symbol}",
                    "debit": 0,
                    "credit": notional,
                    "description": f"Sell {qty} {symbol} @ {price}"
                },
                {
                    "account": "fees:trading",
                    "debit": 0,
                    "credit": fee,
                    "description": f"Trading fee {fee} USDT"
                }
            ]

        # Verify double-entry balance
        total_debit = sum(e['debit'] for e in entries)
        total_credit = sum(e['credit'] for e in entries)

        if abs(total_debit - total_credit) > 1e-6:
            raise ValueError(
                f"Ledger imbalance! Debit: {total_debit}, Credit: {total_credit}, "
                f"Difference: {abs(total_debit - total_credit)}"
            )

        # Write entries to database
        for entry in entries:
            # Get current balance
            balance = self._get_account_balance(entry['account'])

            # Calculate new balance
            new_balance = balance + entry['debit'] - entry['credit']

            # Insert ledger entry
            self.db.execute(
                """
                INSERT INTO ledger_entries
                (timestamp, transaction_id, account, debit, credit, balance_after, symbol, side, qty, price, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, transaction_id, entry['account'],
                    entry['debit'], entry['credit'], new_balance,
                    symbol, side, qty, price,
                    entry['description']
                )
            )

            # Update account balance
            self._set_account_balance(entry['account'], new_balance, timestamp)

            # Log ledger entry event
            try:
                from core.event_schemas import LedgerEntry

                ledger_event = LedgerEntry(
                    timestamp=timestamp,
                    transaction_id=transaction_id,
                    account=entry['account'],
                    debit=entry['debit'],
                    credit=entry['credit'],
                    balance_after=new_balance,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price
                )

                with Trace():
                    log_event(AUDIT_LOG(), "ledger_entry", **ledger_event.model_dump())

            except Exception as e:
                logger.debug(f"Failed to log ledger_entry event: {e}")

        self.db.commit()

        logger.debug(
            f"Ledger: {side.upper()} {qty} {symbol} @ {price} "
            f"(tx: {transaction_id}, fee: {fee})"
        )

    def _get_account_balance(self, account: str) -> float:
        """Get current balance for account"""
        cursor = self.db.execute(
            "SELECT balance FROM account_balances WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0.0

    def _set_account_balance(self, account: str, balance: float, timestamp: float):
        """Set account balance"""
        self.db.execute(
            """
            INSERT OR REPLACE INTO account_balances (account, balance, updated_at)
            VALUES (?, ?, ?)
            """,
            (account, balance, timestamp)
        )

    def get_all_balances(self) -> Dict[str, float]:
        """Get all account balances"""
        cursor = self.db.execute("SELECT account, balance FROM account_balances")
        return {row[0]: row[1] for row in cursor.fetchall()}

    def verify_balance(self, account: str, expected_balance: float, tolerance: float = 0.01) -> bool:
        """Verify account balance matches expected value"""
        actual_balance = self._get_account_balance(account)
        diff = abs(actual_balance - expected_balance)

        if diff > tolerance:
            logger.error(
                f"Balance mismatch for {account}: "
                f"expected {expected_balance}, got {actual_balance} (diff: {diff})"
            )
            return False

        return True


# File: services/pnl.py - Integration

class PnLService:
    def __init__(self, ...):
        # ... existing init ...

        # Initialize ledger
        from core.ledger import DoubleEntryLedger
        self.ledger = DoubleEntryLedger()

    def record_fill(self, symbol, side, quantity, avg_price, fee_quote, entry_price=None):
        """Record fill in PnL service and ledger"""
        # ... existing PnL logic ...

        # Record in double-entry ledger
        try:
            self.ledger.record_trade(
                symbol=symbol,
                side=side,
                qty=quantity,
                price=avg_price,
                fee=fee_quote
            )
        except Exception as e:
            logger.error(f"Failed to record trade in ledger: {e}")

        return realized_pnl
```

**Zu erstellende Dateien:**
- `core/ledger.py` - NEW FILE mit `DoubleEntryLedger` class
- `state/ledger.db` - SQLite database (automatisch erstellt)
- `core/event_schemas.py` - Add `LedgerEntry` schema

**Zu modifizierende Dateien:**
- `services/pnl.py` - Integrate ledger on all fills
- `core/portfolio/portfolio.py` - Add ledger balance verification to reconciliation

---

#### TODO 13: Replay Functionality

**Status:** ❌ Nicht implementiert
**Priorität:** MEDIUM
**Geschätzte Zeit:** 4 Stunden

**Implementierung:**

```python
# File: core/replay.py (NEW FILE)

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionReplay:
    """Reconstruct portfolio state from JSONL logs"""

    def __init__(self, decision_log_path: str, order_log_path: str, audit_log_path: Optional[str] = None):
        """
        Initialize replay from log files.

        Args:
            decision_log_path: Path to decision.jsonl
            order_log_path: Path to order.jsonl
            audit_log_path: Path to audit.jsonl (optional, for ledger entries)
        """
        self.decision_log_path = decision_log_path
        self.order_log_path = order_log_path
        self.audit_log_path = audit_log_path

    def replay(self) -> Dict:
        """
        Replay session and reconstruct final state.

        Returns:
            {
                'portfolio': {symbol: {'qty': float, 'avg_entry': float}},
                'cash_usdt': float,
                'realized_pnl': float,
                'trades': [...]
            }
        """
        # Load all events
        events = self._load_all_events()

        # Sort chronologically
        events.sort(key=lambda e: e.get('ts_ns', 0))

        # Initialize state
        state = {
            'portfolio': {},
            'cash_usdt': 0.0,
            'realized_pnl': 0.0,
            'trades': [],
            'config_hash': None
        }

        # Replay events
        for event in events:
            event_type = event.get('event')

            if event_type == 'position_opened':
                # Add position to portfolio
                symbol = event['symbol']
                state['portfolio'][symbol] = {
                    'qty': event['qty'],
                    'avg_entry': event['avg_entry'],
                    'opened_at': event['opened_at']
                }
                state['cash_usdt'] -= event['notional'] + event['fee_accum']

                logger.debug(f"Replay: Opened {symbol} @ {event['avg_entry']} x{event['qty']}")

            elif event_type == 'position_closed':
                # Remove position from portfolio
                symbol = event['symbol']
                if symbol in state['portfolio']:
                    proceeds = event['qty_closed'] * event['exit_price']
                    state['cash_usdt'] += proceeds - event['fee_total']
                    state['realized_pnl'] += event['realized_pnl_usdt']

                    state['trades'].append({
                        'symbol': symbol,
                        'entry': state['portfolio'][symbol]['avg_entry'],
                        'exit': event['exit_price'],
                        'qty': event['qty_closed'],
                        'pnl': event['realized_pnl_usdt'],
                        'duration_minutes': event.get('duration_minutes')
                    })

                    del state['portfolio'][symbol]

                    logger.debug(f"Replay: Closed {symbol} @ {event['exit_price']} (PnL: {event['realized_pnl_usdt']:.2f})")

            elif event_type == 'ledger_entry':
                # Process ledger entries for balance tracking
                account = event['account']
                if account == 'cash:USDT':
                    state['cash_usdt'] += event['debit'] - event['credit']

            elif event_type == 'config_snapshot':
                # Capture config hash
                state['config_hash'] = event.get('config_hash')

        logger.info(
            f"Replay complete: {len(state['trades'])} trades, "
            f"{len(state['portfolio'])} open positions, "
            f"realized PnL: {state['realized_pnl']:.2f} USDT"
        )

        return state

    def _load_all_events(self) -> List[Dict]:
        """Load all events from log files"""
        events = []

        # Load decision log
        if Path(self.decision_log_path).exists():
            with open(self.decision_log_path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Load order log
        if Path(self.order_log_path).exists():
            with open(self.order_log_path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Load audit log (optional)
        if self.audit_log_path and Path(self.audit_log_path).exists():
            with open(self.audit_log_path) as f:
                for line in f:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return events

    def compare_with_live_state(self, live_portfolio: Dict, live_cash: float) -> Dict:
        """
        Compare replayed state with live state to detect discrepancies.

        Returns:
            {
                'matches': bool,
                'portfolio_diff': {...},
                'cash_diff': float
            }
        """
        replayed_state = self.replay()

        # Compare portfolios
        portfolio_diff = {}
        all_symbols = set(replayed_state['portfolio'].keys()) | set(live_portfolio.keys())

        for symbol in all_symbols:
            replay_qty = replayed_state['portfolio'].get(symbol, {}).get('qty', 0)
            live_qty = live_portfolio.get(symbol, {}).get('qty', 0)

            if abs(replay_qty - live_qty) > 1e-8:
                portfolio_diff[symbol] = {
                    'replayed': replay_qty,
                    'live': live_qty,
                    'diff': live_qty - replay_qty
                }

        # Compare cash
        cash_diff = live_cash - replayed_state['cash_usdt']

        matches = (len(portfolio_diff) == 0 and abs(cash_diff) < 0.01)

        return {
            'matches': matches,
            'portfolio_diff': portfolio_diff,
            'cash_diff': cash_diff,
            'replayed_state': replayed_state
        }


# File: scripts/replay_session.py (NEW FILE) - CLI Tool

#!/usr/bin/env python3
"""
Session Replay CLI Tool

Usage:
    python scripts/replay_session.py sessions/session_20250112_123456/logs
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.replay import SessionReplay


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/replay_session.py <log_directory>")
        print("Example: python scripts/replay_session.py sessions/session_20250112_123456/logs")
        sys.exit(1)

    log_dir = Path(sys.argv[1])

    if not log_dir.exists():
        print(f"Error: Log directory not found: {log_dir}")
        sys.exit(1)

    # Find log files
    decision_log = log_dir / "decisions" / "decision.jsonl"
    order_log = log_dir / "orders" / "order.jsonl"
    audit_log = log_dir / "audit" / "audit.jsonl"

    if not decision_log.exists():
        print(f"Error: decision.jsonl not found in {log_dir}")
        sys.exit(1)

    if not order_log.exists():
        print(f"Error: order.jsonl not found in {log_dir}")
        sys.exit(1)

    # Replay session
    print(f"🔄 Replaying session from {log_dir}...")
    print()

    replay = SessionReplay(
        decision_log_path=str(decision_log),
        order_log_path=str(order_log),
        audit_log_path=str(audit_log) if audit_log.exists() else None
    )

    state = replay.replay()

    # Print results
    print("=" * 60)
    print("📊 REPLAY RESULTS")
    print("=" * 60)
    print()

    print(f"Config Hash: {state['config_hash']}")
    print(f"Total Trades: {len(state['trades'])}")
    print(f"Realized PnL: {state['realized_pnl']:.2f} USDT")
    print(f"Cash Balance: {state['cash_usdt']:.2f} USDT")
    print(f"Open Positions: {len(state['portfolio'])}")
    print()

    if state['portfolio']:
        print("Open Positions:")
        for symbol, pos in state['portfolio'].items():
            print(f"  - {symbol}: {pos['qty']:.6f} @ {pos['avg_entry']:.6f}")
        print()

    if state['trades']:
        print("Trade History:")
        for i, trade in enumerate(state['trades'][-10:], 1):  # Last 10 trades
            pnl_sign = "+" if trade['pnl'] >= 0 else ""
            print(
                f"  {i}. {trade['symbol']}: "
                f"{trade['entry']:.6f} → {trade['exit']:.6f} "
                f"({pnl_sign}{trade['pnl']:.2f} USDT)"
            )
        print()

    print("✅ Replay complete!")


if __name__ == '__main__':
    main()
```

**Zu erstellende Dateien:**
- `core/replay.py` - NEW FILE mit `SessionReplay` class
- `scripts/replay_session.py` - NEW FILE mit CLI tool

**Verwendung:**
```bash
# Replay a specific session
python scripts/replay_session.py sessions/session_20250112_123456/logs

# Compare replayed state with live state (in code)
from core.replay import SessionReplay
replay = SessionReplay("logs/decisions/decision.jsonl", "logs/orders/order.jsonl")
comparison = replay.compare_with_live_state(engine.positions, portfolio.get_balance("USDT"))
```

---

### 📋 Phase 4 Checkliste

- [x] Basic Reconciliation implementiert
- [ ] **TODO:** Double-Entry Ledger System implementieren
- [ ] **TODO:** Replay Functionality implementieren
- [ ] **TODO:** State Reconstruction aus Logs
- [ ] **TODO:** Automated Reconciliation Tests

---

## Phase 5: Betriebsfähigkeit & Überwachung

**Ziel:** Production-ready Monitoring & Alerting
**Status:** 60% Complete
**Priorität:** 🟢 LOW

### ✅ Bereits implementiert

| Feature | Status | Location |
|---------|--------|----------|
| Heartbeat Events | ✅ | `core/utils/heartbeat_telemetry.py` |
| Performance Metrics | ✅ | Various files |
| Error Tracking | ✅ | `core/logger_factory.py:306-336` |

### ❌ TODO: Fehlende Features

#### TODO 14: Alerting System

**Status:** ❌ Nicht implementiert
**Priorität:** LOW
**Geschätzte Zeit:** 5 Stunden

**Implementierung:**

```python
# File: core/alerting.py (NEW FILE)

import logging
from typing import Callable, Dict, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    """Alert rule definition"""
    name: str
    condition: Callable[[Dict], bool]  # Function that returns True if alert should fire
    severity: AlertSeverity
    message_template: str
    cooldown_seconds: int = 300  # Don't fire same alert within 5 minutes


class AlertManager:
    """Manage alert rules and notifications"""

    def __init__(self, telegram_bot=None):
        """
        Initialize alert manager.

        Args:
            telegram_bot: Optional Telegram bot for notifications
        """
        self.telegram_bot = telegram_bot
        self.rules: List[AlertRule] = []
        self.alert_history: Dict[str, float] = {}  # rule_name → last_fired_timestamp

    def add_rule(self, rule: AlertRule):
        """Register alert rule"""
        self.rules.append(rule)
        logger.info(f"Alert rule registered: {rule.name} ({rule.severity.value})")

    def check_alerts(self, context: Dict):
        """
        Check all alert rules against current context.

        Args:
            context: Dictionary with current state (portfolio, positions, metrics, etc.)
        """
        import time

        for rule in self.rules:
            try:
                # Check cooldown
                last_fired = self.alert_history.get(rule.name, 0)
                if time.time() - last_fired < rule.cooldown_seconds:
                    continue

                # Evaluate condition
                if rule.condition(context):
                    # Fire alert
                    message = rule.message_template.format(**context)
                    self._fire_alert(rule, message)
                    self.alert_history[rule.name] = time.time()

            except Exception as e:
                logger.error(f"Error evaluating alert rule {rule.name}: {e}")

    def _fire_alert(self, rule: AlertRule, message: str):
        """Send alert via configured channels"""
        # Log alert
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.ERROR: "❌",
            AlertSeverity.CRITICAL: "🚨"
        }

        emoji = severity_emoji.get(rule.severity, "")
        full_message = f"{emoji} {rule.severity.value.upper()}: {message}"

        logger.warning(f"ALERT FIRED: {rule.name} - {message}")

        # Send to Telegram if available
        if self.telegram_bot:
            try:
                self.telegram_bot.send_message(full_message)
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")

        # Log alert event
        from core.logger_factory import HEALTH_LOG, log_event
        from core.trace_context import Trace

        with Trace():
            log_event(
                HEALTH_LOG(),
                "alert_fired",
                alert_name=rule.name,
                severity=rule.severity.value,
                message=message,
                level=logging.WARNING if rule.severity == AlertSeverity.WARNING else logging.ERROR
            )


# File: main.py - Initialize alert system

def setup_alerts(engine, telegram_bot=None):
    """Setup alert rules for production monitoring"""
    from core.alerting import AlertManager, AlertRule, AlertSeverity

    alert_manager = AlertManager(telegram_bot)

    # Rule 1: Portfolio Drawdown Alert
    alert_manager.add_rule(AlertRule(
        name="portfolio_drawdown",
        condition=lambda ctx: ctx.get('daily_pnl_pct', 0) < -5.0,
        severity=AlertSeverity.ERROR,
        message_template="Portfolio drawdown: {daily_pnl_pct:.2f}%",
        cooldown_seconds=600
    ))

    # Rule 2: High Order Cancel Rate
    alert_manager.add_rule(AlertRule(
        name="high_cancel_rate",
        condition=lambda ctx: ctx.get('cancel_rate', 0) > 0.50,
        severity=AlertSeverity.WARNING,
        message_template="High order cancel rate: {cancel_rate:.1%} ({cancel_count}/{total_orders})",
        cooldown_seconds=300
    ))

    # Rule 3: Rate Limit Threshold
    alert_manager.add_rule(AlertRule(
        name="rate_limit_threshold",
        condition=lambda ctx: ctx.get('rate_limit_hits_1min', 0) >= 3,
        severity=AlertSeverity.CRITICAL,
        message_template="Rate limit hit {rate_limit_hits_1min}x in 1 minute",
        cooldown_seconds=180
    ))

    # Rule 4: Position Exposure Warning
    alert_manager.add_rule(AlertRule(
        name="high_exposure",
        condition=lambda ctx: ctx.get('portfolio_exposure_pct', 0) > 0.85,
        severity=AlertSeverity.WARNING,
        message_template="Portfolio exposure high: {portfolio_exposure_pct:.1%}",
        cooldown_seconds=600
    ))

    # Rule 5: Exchange Disconnection
    alert_manager.add_rule(AlertRule(
        name="exchange_connection_lost",
        condition=lambda ctx: ctx.get('exchange_connected', True) == False,
        severity=AlertSeverity.CRITICAL,
        message_template="Exchange connection lost!",
        cooldown_seconds=120
    ))

    return alert_manager
```

**Zu erstellende Dateien:**
- `core/alerting.py` - NEW FILE mit `AlertManager` class
- `core/event_schemas.py` - Add `AlertFired` schema

**Zu modifizierende Dateien:**
- `main.py` - Initialize and register alert rules
- `engine/engine.py` - Check alerts periodically in main loop

---

#### TODO 15: Dashboard Query Tools

**Status:** ❌ Nicht implementiert
**Priorität:** LOW
**Geschätzte Zeit:** 3 Stunden

**Implementierung:**

```python
# File: scripts/query_logs.py (NEW FILE)

#!/usr/bin/env python3
"""
Log Query Tool for Trading Bot

Usage:
    python scripts/query_logs.py --session session_20250112_123456 --query trades
    python scripts/query_logs.py --session session_20250112_123456 --query guards --symbol BTC/USDT
"""

import json
import sys
from pathlib import Path
from typing import List, Dict
import argparse


class LogQuery:
    """Query trading bot logs"""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.decision_log = session_dir / "logs" / "decisions" / "decision.jsonl"
        self.order_log = session_dir / "logs" / "orders" / "order.jsonl"
        self.audit_log = session_dir / "logs" / "audit" / "audit.jsonl"

    def query_trades(self) -> List[Dict]:
        """Get all completed trades"""
        trades = []

        # Read position_opened and position_closed events
        opens = {}

        with open(self.decision_log) as f:
            for line in f:
                event = json.loads(line)

                if event['event'] == 'position_opened':
                    opens[event['symbol']] = event

                elif event['event'] == 'position_closed':
                    if event['symbol'] in opens:
                        open_event = opens[event['symbol']]
                        trades.append({
                            'symbol': event['symbol'],
                            'entry_price': open_event['avg_entry'],
                            'exit_price': event['exit_price'],
                            'qty': event['qty_closed'],
                            'realized_pnl': event['realized_pnl_usdt'],
                            'realized_pct': event['realized_pnl_pct'],
                            'duration_minutes': event.get('duration_minutes'),
                            'reason': event['reason']
                        })
                        del opens[event['symbol']]

        return trades

    def query_guards(self, symbol: str = None) -> List[Dict]:
        """Get guard evaluation events"""
        guards = []

        with open(self.decision_log) as f:
            for line in f:
                event = json.loads(line)

                if event['event'] == 'guards_eval':
                    if symbol is None or event['symbol'] == symbol:
                        guards.append(event)

        return guards

    def query_orders(self, status: str = None) -> List[Dict]:
        """Get order events"""
        orders = {}  # order_req_id → event

        with open(self.order_log) as f:
            for line in f:
                event = json.loads(line)

                if event['event'] in ['order_attempt', 'order_ack', 'order_done']:
                    order_req_id = event.get('order_req_id')
                    if order_req_id:
                        if order_req_id not in orders:
                            orders[order_req_id] = {}
                        orders[order_req_id][event['event']] = event

        # Filter by status if specified
        if status:
            orders = {
                k: v for k, v in orders.items()
                if v.get('order_done', {}).get('final_status') == status
            }

        return list(orders.values())

    def query_performance(self) -> Dict:
        """Calculate performance metrics"""
        trades = self.query_trades()

        if not trades:
            return {'total_trades': 0}

        total_pnl = sum(t['realized_pnl'] for t in trades)
        winning_trades = [t for t in trades if t['realized_pnl'] > 0]
        losing_trades = [t for t in trades if t['realized_pnl'] < 0]

        return {
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(trades) if trades else 0,
            'total_pnl': total_pnl,
            'avg_pnl_per_trade': total_pnl / len(trades),
            'avg_win': sum(t['realized_pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0,
            'avg_loss': sum(t['realized_pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0,
            'avg_duration_minutes': sum(t.get('duration_minutes', 0) for t in trades if t.get('duration_minutes')) / len(trades)
        }


def main():
    parser = argparse.ArgumentParser(description="Query trading bot logs")
    parser.add_argument('--session', required=True, help="Session directory name (e.g., session_20250112_123456)")
    parser.add_argument('--query', required=True, choices=['trades', 'guards', 'orders', 'performance'], help="Query type")
    parser.add_argument('--symbol', help="Filter by symbol")
    parser.add_argument('--status', help="Filter orders by status")

    args = parser.parse_args()

    # Find session directory
    session_dir = Path("sessions") / args.session
    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}")
        sys.exit(1)

    # Execute query
    query = LogQuery(session_dir)

    if args.query == 'trades':
        trades = query.query_trades()
        print(f"Found {len(trades)} trades:")
        for i, trade in enumerate(trades, 1):
            pnl_sign = "+" if trade['realized_pnl'] >= 0 else ""
            print(
                f"{i}. {trade['symbol']}: {trade['entry_price']:.6f} → {trade['exit_price']:.6f} "
                f"({pnl_sign}{trade['realized_pnl']:.2f} USDT, {pnl_sign}{trade['realized_pct']:.2f}%) "
                f"[{trade['reason']}]"
            )

    elif args.query == 'guards':
        guards = query.query_guards(symbol=args.symbol)
        print(f"Found {len(guards)} guard evaluations:")
        for event in guards[-10:]:  # Last 10
            passed = "✅ PASS" if event['all_passed'] else "❌ BLOCK"
            print(f"{event['symbol']}: {passed}")
            for guard in event.get('guards', []):
                status = "✅" if guard['passed'] else "❌"
                print(f"  {status} {guard['name']}: {guard.get('value')} (threshold: {guard.get('threshold')})")

    elif args.query == 'orders':
        orders = query.query_orders(status=args.status)
        print(f"Found {len(orders)} orders:")
        for order_events in orders[-10:]:  # Last 10
            attempt = order_events.get('order_attempt', {})
            done = order_events.get('order_done', {})
            print(
                f"{attempt.get('symbol')}: {attempt.get('side')} {attempt.get('qty'):.6f} @ {attempt.get('price'):.6f} "
                f"→ {done.get('final_status', 'pending')}"
            )

    elif args.query == 'performance':
        perf = query.query_performance()
        print("Performance Summary:")
        print(f"  Total Trades: {perf['total_trades']}")
        print(f"  Win Rate: {perf.get('win_rate', 0):.1%}")
        print(f"  Total PnL: {perf.get('total_pnl', 0):.2f} USDT")
        print(f"  Avg PnL/Trade: {perf.get('avg_pnl_per_trade', 0):.2f} USDT")
        print(f"  Avg Win: {perf.get('avg_win', 0):.2f} USDT")
        print(f"  Avg Loss: {perf.get('avg_loss', 0):.2f} USDT")
        print(f"  Avg Duration: {perf.get('avg_duration_minutes', 0):.1f} minutes")


if __name__ == '__main__':
    main()
```

**Zu erstellende Dateien:**
- `scripts/query_logs.py` - NEW FILE mit CLI query tool

**Verwendung:**
```bash
# Get all trades
python scripts/query_logs.py --session session_20250112_123456 --query trades

# Get guard evaluations for BTC/USDT
python scripts/query_logs.py --session session_20250112_123456 --query guards --symbol BTC/USDT

# Get all filled orders
python scripts/query_logs.py --session session_20250112_123456 --query orders --status filled

# Get performance summary
python scripts/query_logs.py --session session_20250112_123456 --query performance
```

---

### 📋 Phase 5 Checkliste

- [x] Heartbeat Events implementiert
- [x] Performance Metrics implementiert
- [x] Error Tracking implementiert
- [ ] **TODO:** Alerting System implementieren
- [ ] **TODO:** Dashboard Query Tools implementieren
- [ ] **TODO:** Real-time Monitoring Dashboard (optional)

---

## Phase 6: Compliance & Hygiene

**Ziel:** Production-ready Compliance & Data Hygiene
**Status:** 40% Complete
**Priorität:** 🟢 LOW (bereits größtenteils fertig)

### ✅ Bereits implementiert

| Feature | Status | Location |
|---------|--------|----------|
| Config Versioning | ✅ | `core/logger_factory.py:343-450` |
| Config Drift Detection | ✅ | `core/logger_factory.py:503-577` |
| Sensitive Data Masking | ✅ | `core/logger_factory.py:255-302` |
| Audit Trail | ✅ | AUDIT_LOG everywhere |

### ❌ TODO: Fehlende Features

#### TODO 16: Automated Log Retention

**Status:** ⚠️ Basic (nur config, keine Enforcement)
**Priorität:** LOW
**Geschätzte Zeit:** 2 Stunden

**Implementierung:**

```python
# File: core/log_retention.py (NEW FILE)

import os
import time
import gzip
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class LogRetentionManager:
    """Manage log file retention policies"""

    def __init__(self, retention_days: Dict[str, int]):
        """
        Initialize retention manager.

        Args:
            retention_days: {'decisions': 365, 'orders': 365, 'tracer': 60, ...}
        """
        self.retention_days = retention_days

    def cleanup_old_logs(self, sessions_dir: Path = Path("sessions")):
        """Remove log files older than retention policy"""
        now = time.time()
        total_removed = 0
        total_size_mb = 0

        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue

            logs_dir = session_dir / "logs"
            if not logs_dir.exists():
                continue

            # Check each log type
            for log_type, retention_days in self.retention_days.items():
                log_subdir = logs_dir / log_type
                if not log_subdir.exists():
                    continue

                cutoff_time = now - (retention_days * 86400)

                # Remove old log files
                for log_file in log_subdir.glob("*.jsonl*"):
                    file_age = now - log_file.stat().st_mtime

                    if file_age > (retention_days * 86400):
                        file_size_mb = log_file.stat().st_size / (1024 * 1024)
                        log_file.unlink()
                        total_removed += 1
                        total_size_mb += file_size_mb

                        logger.info(
                            f"Removed old log: {log_file} "
                            f"(age: {file_age / 86400:.1f}d, size: {file_size_mb:.1f}MB)"
                        )

        if total_removed > 0:
            logger.info(
                f"Log cleanup complete: removed {total_removed} files, "
                f"freed {total_size_mb:.1f}MB"
            )

        return total_removed, total_size_mb


# File: main.py - Schedule cleanup

def schedule_log_cleanup(retention_manager):
    """Schedule periodic log cleanup"""
    import threading

    def cleanup_task():
        while True:
            time.sleep(86400)  # Run daily
            try:
                retention_manager.cleanup_old_logs()
            except Exception as e:
                logger.error(f"Log cleanup failed: {e}")

    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()


# Initialize in main()
from core.log_retention import LogRetentionManager

retention_manager = LogRetentionManager({
    'decisions': 365,
    'orders': 365,
    'tracer': 60,
    'audit': 730,
    'health': 30
})

schedule_log_cleanup(retention_manager)
```

**Zu erstellende Dateien:**
- `core/log_retention.py` - NEW FILE mit `LogRetentionManager` class

**Zu modifizierende Dateien:**
- `main.py` - Schedule daily cleanup task

---

### 📋 Phase 6 Checkliste

- [x] Config Versioning implementiert
- [x] Config Drift Detection implementiert
- [x] Sensitive Data Masking implementiert
- [x] Audit Trail implementiert
- [ ] **TODO:** Automated Log Retention Enforcement
- [ ] **TODO:** GDPR Compliance (PII removal, if applicable)

---

## Implementierungsplan

### 🎯 Empfohlene Reihenfolge

| Woche | Tasks | Priorität | Aufwand | Grund |
|-------|-------|-----------|---------|-------|
| **Woche 1** | TODO 7 (Idempotency Store)<br>TODO 8 (Retry Tracking) | 🔴 CRITICAL | 7h | **Verhindert Doppel-Orders in Production!** |
| **Woche 2** | TODO 1 (order_fill)<br>TODO 2 (order_cancel)<br>TODO 3 (order_done Sell) | 🟠 HIGH | 6h | Komplette Order-Lifecycle-Tracking |
| **Woche 3** | TODO 4 (TP/SL Eval)<br>TODO 9 (Order Dedup) | 🟠 HIGH | 5h | Exit-Trigger Completeness |
| **Woche 4** | TODO 5 (position_updated)<br>TODO 12 (Ledger) | 🟡 MEDIUM | 8h | Portfolio-Tracking + Double-Entry |
| **Woche 5** | TODO 13 (Replay)<br>TODO 6 (risk_limits_eval) | 🟡 MEDIUM | 7h | State Reconstruction + Risk Control |
| **Woche 6** | TODO 14 (Alerting)<br>TODO 15 (Dashboard) | 🟢 LOW | 8h | Monitoring & Operations |
| **Woche 7** | TODO 16 (Log Retention) | 🟢 LOW | 2h | Cleanup & Compliance |

**Gesamtaufwand:** ~43 Stunden für vollständige Roadmap-Implementation

---

### 🔴 **CRITICAL PRIORITY (Woche 1)**

#### Task 1: Idempotency Store
- **Datei:** `core/idempotency.py` (NEW)
- **Änderungen:** `engine/buy_decision.py`, `services/exits.py`, `main.py`
- **Aufwand:** 4h
- **Risiko:** HIGH - Verhindert Doppel-Orders in Production

#### Task 2: Retry Tracking
- **Datei:** `trading/orders.py` (NEW)
- **Änderungen:** `engine/buy_decision.py`, `services/exits.py`
- **Aufwand:** 3h
- **Risiko:** HIGH - Wichtig für Debugging & Reliability

---

### 🟠 **HIGH PRIORITY (Woche 2-3)**

#### Task 3: Order Lifecycle Events
- **Änderungen:** `services/exits.py`, `engine/exit_handler.py`, `trading/orders.py`
- **Aufwand:** 6h
- **Benefit:** Complete order forensics

#### Task 4: TP/SL Trigger Evaluation
- **Änderungen:** `engine/position_manager.py`
- **Aufwand:** 3h
- **Benefit:** Systematische Exit-Trigger-Tracking

#### Task 9: Order Deduplication
- **Änderungen:** `trading/orders.py`, `engine/buy_decision.py`
- **Aufwand:** 2h
- **Benefit:** Additional safety layer

---

### 🟡 **MEDIUM PRIORITY (Woche 4-5)**

#### Task 5: Position Updated Event
- **Änderungen:** `engine/position_manager.py`
- **Aufwand:** 2h
- **Benefit:** Better position lifecycle tracking

#### Task 6: Risk Limits Evaluation
- **Datei:** `core/risk_limits.py` (NEW)
- **Änderungen:** `engine/buy_decision.py`
- **Aufwand:** 3h
- **Benefit:** Pre-trade risk checks

#### Task 12: Double-Entry Ledger
- **Datei:** `core/ledger.py` (NEW)
- **Änderungen:** `services/pnl.py`, `core/portfolio/portfolio.py`
- **Aufwand:** 6h
- **Benefit:** Balance verification & accounting

#### Task 13: Replay Functionality
- **Datei:** `core/replay.py` (NEW), `scripts/replay_session.py` (NEW)
- **Aufwand:** 4h
- **Benefit:** State reconstruction for debugging

---

### 🟢 **LOW PRIORITY (Woche 6-7)**

#### Task 14: Alerting System
- **Datei:** `core/alerting.py` (NEW)
- **Änderungen:** `main.py`
- **Aufwand:** 5h
- **Benefit:** Proactive monitoring

#### Task 15: Dashboard Query Tools
- **Datei:** `scripts/query_logs.py` (NEW)
- **Aufwand:** 3h
- **Benefit:** Better log analysis

#### Task 16: Log Retention
- **Datei:** `core/log_retention.py` (NEW)
- **Änderungen:** `main.py`
- **Aufwand:** 2h
- **Benefit:** Disk space management

---

## Quick Reference: TODO Summary

### Phase 0 (HIGH)
- [ ] TODO 1: `order_fill` Event
- [ ] TODO 2: `order_cancel` Event
- [ ] TODO 3: `order_done` für Sell Orders

### Phase 1 (MEDIUM)
- [ ] TODO 4: `sell_trigger_eval` (TP/SL)
- [ ] TODO 5: `position_updated` Event
- [ ] TODO 6: `risk_limits_eval` Event

### Phase 2 (CRITICAL)
- [ ] TODO 7: Idempotency Store mit Persistence
- [ ] TODO 8: Retry Tracking mit Backoff
- [ ] TODO 9: Order Deduplication

### Phase 3 (LOW - mostly done)
- [ ] TODO 10: `position_updated` (siehe Phase 1)
- [ ] TODO 11: `risk_limits_eval` (siehe Phase 1)

### Phase 4 (MEDIUM)
- [ ] TODO 12: Double-Entry Ledger System
- [ ] TODO 13: Replay Functionality

### Phase 5 (LOW)
- [ ] TODO 14: Alerting System
- [ ] TODO 15: Dashboard Query Tools

### Phase 6 (LOW)
- [ ] TODO 16: Automated Log Retention

**Total: 16 TODOs**

---

## Maintenance & Updates

**Letzte Änderung:** 2025-10-12
**Änderungshistorie:**
- 2025-10-12: Initial roadmap creation
- *Weitere Updates hier eintragen*

**Wartung:**
- Diese Datei sollte bei jeder Phase-Implementation aktualisiert werden
- TODOs in "✅ Completed" verschieben wenn fertig
- Neue TODOs hinzufügen wenn weitere Lücken gefunden werden
- Zeitschätzungen anpassen basierend auf tatsächlichem Aufwand

---

## Kontakt & Fragen

Bei Fragen zur Roadmap oder Implementation:
- **Dokumentation:** Siehe `core/` und `services/` Module
- **Code-Referenzen:** Alle File-Locations sind verlinkt
- **Git Commits:** Commits mit "Phase 3 structured logging" enthalten relevante Änderungen

---

**🎯 Nächster Schritt:** Start mit Phase 2 - Idempotency Store (TODO 7) für Production Safety!
