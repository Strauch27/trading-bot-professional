# Trading Bot - Offene TODOs

**Stand**: 2025-10-14
**Session**: feat/order-flow-hardening

---

## 📋 Übersicht

| Bereich | Status | Priorität |
|---------|--------|-----------|
| **MEMORY MANAGEMENT** | ✅ 100% Complete | - |
| **ORDER OPTIMIERUNG** | ✅ 95% Complete | NIEDRIG |
| **TERMINAL OPTIMIERUNG** | ✅ 100% Complete | - |

---

## 🎯 TERMINAL OPTIMIERUNG (Priorität: MITTEL)

### 1. Live Monitors Implementation

**Datei**: `ui/live_monitors.py` (neu)
**Aufwand**: ~3-4 Stunden
**Abhängigkeiten**: Rich library, MarketDataProvider, PortfolioManager

#### 1.1 LiveHeartbeat Panel
```python
# Zeigt in Echtzeit:
- Exchange Status (Connected/Disconnected)
- API Latenz (P50/P95/P99)
- Fill Rates (Limit/Market)
- Request Rate (Current/Max)
- Cache Hit Rate
- Throttle Rate
```

**Features**:
- Auto-refresh alle 1s
- Farbcodierung (grün = gut, gelb = warning, rot = critical)
- Rich.Live Context Manager

**Implementierung**:
```python
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

class LiveHeartbeat:
    def __init__(self, market_data_provider, fill_tracker):
        self.md_provider = market_data_provider
        self.fill_tracker = fill_tracker

    def create_panel(self) -> Panel:
        # Fetch stats
        md_stats = self.md_provider.get_statistics()
        fill_stats = self.fill_tracker.get_statistics()

        # Build table...
        return Panel(table, title="System Heartbeat")

    def run(self, refresh_rate=1.0):
        with Live(self.create_panel(), refresh_per_second=refresh_rate) as live:
            while not should_stop:
                live.update(self.create_panel())
```

---

#### 1.2 DropMonitorView (Live Top-10 Drops)

**Datei**: `ui/live_monitors.py`
**Zweck**: Zeigt Top-10 Drops in Echtzeit

```
┌─────────────────────────────────────────┐
│ 📉 Top 10 Drops (Last 5min)            │
├─────────┬──────────┬──────────┬─────────┤
│ Symbol  │ Drop %   │ Volume   │ Age     │
├─────────┼──────────┼──────────┼─────────┤
│ BTC/USDT│ -2.34%   │ 1.2M     │ 2m 15s  │
│ ETH/USDT│ -1.87%   │ 850K     │ 1m 42s  │
│ ...     │ ...      │ ...      │ ...     │
└─────────┴──────────┴──────────┴─────────┘
```

**Features**:
- Auto-refresh alle 2s
- Farbcodierung nach Drop-Stärke
- Sortierung nach Drop %
- Age Counter (live updates)

**Implementation**:
```python
class DropMonitorView:
    def __init__(self, drop_tracker):
        self.drop_tracker = drop_tracker

    def create_table(self) -> Table:
        table = Table(title="📉 Top 10 Drops")
        table.add_column("Symbol", style="yellow")
        table.add_column("Drop %", style="red bold")
        table.add_column("Volume", justify="right")
        table.add_column("Age", justify="right", style="dim")

        drops = self.drop_tracker.get_top_drops(limit=10)
        for drop in drops:
            # Calculate age
            age_s = time.time() - drop.timestamp
            age_str = format_duration(age_s)

            # Color based on magnitude
            drop_style = "red bold" if drop.pct < -2.0 else "red"

            table.add_row(
                drop.symbol,
                f"{drop.pct:.2f}%",
                format_volume(drop.volume),
                age_str
            )

        return table
```

---

#### 1.3 PortfolioMonitorView (Live Positions)

**Datei**: `ui/live_monitors.py`
**Zweck**: Zeigt offene Positionen + Unrealized P&L

```
┌───────────────────────────────────────────────────────┐
│ 💼 Portfolio (5 Positions)                            │
├──────────┬─────────┬──────────┬──────────┬────────────┤
│ Symbol   │ Qty     │ Entry    │ Current  │ P&L        │
├──────────┼─────────┼──────────┼──────────┼────────────┤
│ BTC/USDT │ 0.1000  │ 50000.00 │ 50150.00 │ +$15.00    │
│ ETH/USDT │ 2.5000  │ 3200.00  │ 3185.00  │ -$37.50    │
│ ...      │ ...     │ ...      │ ...      │ ...        │
├──────────┴─────────┴──────────┴──────────┼────────────┤
│                               Total P&L: │ +$125.50   │
└──────────────────────────────────────────┴────────────┘
```

**Features**:
- Auto-refresh alle 5s
- Live price updates
- P&L color coding (green = profit, red = loss)
- Total P&L summary
- Position age indicator

**Implementation**:
```python
class PortfolioMonitorView:
    def __init__(self, portfolio, market_data_provider):
        self.portfolio = portfolio
        self.md_provider = market_data_provider

    def create_table(self) -> Table:
        table = Table(title=f"💼 Portfolio ({len(self.portfolio.held_assets)} Positions)")

        total_pnl = 0.0

        for symbol, asset in self.portfolio.held_assets.items():
            # Get current price
            ticker = self.md_provider.get_ticker(symbol)
            current_price = ticker.last if ticker else 0.0

            # Calculate unrealized P&L
            entry_price = asset.get("entry_price", 0.0)
            qty = asset.get("amount", 0.0)
            unrealized_pnl = (current_price - entry_price) * qty
            total_pnl += unrealized_pnl

            # Add row with color coding
            pnl_style = "green bold" if unrealized_pnl > 0 else "red bold"
            table.add_row(
                symbol,
                f"{qty:.4f}",
                f"{entry_price:.2f}",
                f"{current_price:.2f}",
                f"${unrealized_pnl:+.2f}",
                style=pnl_style if unrealized_pnl != 0 else "white"
            )

        # Total row
        table.add_row(
            "", "", "", "Total P&L:",
            f"${total_pnl:+.2f}",
            style="green bold" if total_pnl > 0 else "red bold"
        )

        return table
```

---

### 2. Rich Logging Integration

**Datei**: `core/logging/loggingx.py` (modify)
**Aufwand**: ~1-2 Stunden
**Abhängigkeiten**: ui/console_ui.py

**Zweck**: Bridge `log_event()` zu Rich Console Output

**Aktuell**:
```python
log_event("BUY_SKIP", symbol="BTC/USDT", message="Wide spread")
# Output: Plain text to stdout
```

**Soll**:
```python
log_event("BUY_SKIP", symbol="BTC/USDT", message="Wide spread")
# Output: Rich formatted with colors, icons, structured details
```

**Implementation Plan**:

1. **Add Rich Handler** zu `loggingx.py`:
```python
from ui.console_ui import console, log_info, log_warning, log_error, log_success

# Map log levels to Rich functions
LOG_LEVEL_HANDLERS = {
    "INFO": log_info,
    "WARNING": log_warning,
    "ERROR": log_error,
    "SUCCESS": log_success
}

def log_event(event_type: str, level: str = "INFO", symbol: str = None,
              message: str = "", ctx: Dict[str, Any] = None):
    """Enhanced log_event with Rich output."""

    # Build message
    msg_parts = [event_type]
    if symbol:
        msg_parts.append(f"[{symbol}]")
    if message:
        msg_parts.append(message)

    full_message = " ".join(msg_parts)

    # Get Rich handler
    handler = LOG_LEVEL_HANDLERS.get(level, log_info)

    # Output with Rich
    handler(full_message, details=ctx)

    # Still write to JSONL for audit
    _write_jsonl(event_type, level, symbol, message, ctx)
```

2. **Feature Flag** in `config.py`:
```python
# Terminal Output
ENABLE_RICH_LOGGING = True  # Use Rich for console output
RICH_LOG_LEVEL = "INFO"     # Minimum level for Rich output
```

3. **Test Cases** in `tests/test_rich_logging.py`:
```python
def test_log_event_with_rich():
    """Test that log_event uses Rich when enabled."""
    log_event("TEST", level="INFO", symbol="BTC/USDT",
              message="Test message", ctx={"key": "value"})
    # Should see colored output in terminal

def test_log_event_fallback_no_rich():
    """Test fallback when Rich not available."""
    # Simulate Rich not installed
    # Should fall back to plain text
```

---

## 🔧 ORDER OPTIMIERUNG (Priorität: NIEDRIG)

### Phase 3: Partial-Fill FSM (Optional Refactoring)

**Datei**: `core/fsm/partial_fills.py` (neu)
**Aufwand**: ~2 Stunden
**Priorität**: NIEDRIG (bereits implizit durch COID-Manager gelöst)

**Zweck**: Explizite State Machine für Partial-Fill Lifecycle

**States**:
```
PENDING → PARTIAL → FILLED
        ↓         ↓
        CANCELED  EXPIRED
```

**Implementation** (falls gewünscht):
```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class OrderState(Enum):
    PENDING = "pending"      # Order submitted, not filled
    PARTIAL = "partial"      # Partially filled
    FILLED = "filled"        # Fully filled (terminal)
    CANCELED = "canceled"    # Canceled by user/system (terminal)
    EXPIRED = "expired"      # Expired (IOC timeout) (terminal)

@dataclass
class OrderFSM:
    order_id: str
    state: OrderState = OrderState.PENDING
    filled_qty: float = 0.0
    total_qty: float = 0.0

    def transition(self, new_state: OrderState, filled_qty: float = None):
        """Transition to new state with validation."""
        # Validate transitions
        if self.state in (OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED):
            raise ValueError(f"Cannot transition from terminal state {self.state}")

        # Update state
        self.state = new_state
        if filled_qty is not None:
            self.filled_qty = filled_qty

    def is_terminal(self) -> bool:
        return self.state in (OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED)

    @property
    def fill_rate(self) -> float:
        return self.filled_qty / self.total_qty if self.total_qty > 0 else 0.0
```

**Integration**: Ergänze `COIDManager` um FSM-Tracking (optional).

**Note**: Aktuell wird Partial-Fill-Handling bereits durch COID-Status (PENDING/FILLED/CANCELED) + Telemetry abgedeckt. FSM ist nice-to-have für explizite State-Validierung.

---

## 🚫 NICHT GEPLANT

### CCXT Async Adapter (services/market_data/adapters/ccxt_async.py)

**Grund**: Würde massive Refactoring des gesamten Codebases erfordern:
- Async/Await überall (exchange calls, market data, buy/sell services)
- Event Loop Management
- Threading → Asyncio Migration
- ~80% des Codes betroffen

**Alternative**: Aktuelles Setup mit Threading + Coalescing + Rate-Limiting ist performant genug für die Anforderungen.

**Falls später gewünscht**: Kann als separate Phase 2.0 geplant werden mit dediziertem Async-Branch.

---

## 📊 Completion Status

### Memory Management: ✅ 100%
- ✅ Soft-TTL Cache
- ✅ Candle Alignment
- ✅ JSONL Audit Logging
- ✅ Request Coalescing
- ✅ Rate-Limit-Budget
- ✅ Ringbuffer für Orderbooks
- ⚠️ CCXT Async (übersprungen)

### Order Optimierung: ✅ 95%
- ✅ Phase 1: Entry/Exit Centralization (OrderService)
- ✅ Phase 2: COID Idempotency + Persistence
- ⚠️ Phase 3: Partial-Fill FSM (optional)
- ✅ Phase 4: Entry Slippage Guard
- ✅ Phase 5: TTL Timing (first_fill_ts)
- ✅ Phase 6: Symbol-Scoped Locks
- ✅ Phase 7: Spread/Depth Guards
- ✅ Phase 8: Consolidated Entry Guards
- ✅ Phase 9: Fill Telemetry
- ✅ Phase 10: Consolidated Exit Evaluation
- ✅ Phase 11: Unit Tests (42 tests)
- ✅ Phase 12: Feature Flags

### Terminal Optimierung: ✅ 100%
- ✅ Rich Console Basis (ui/console_ui.py)
- ✅ Live Monitors (LiveHeartbeat, DropMonitor, PortfolioMonitor)
- ✅ Rich Logging Integration (log_event → Rich)
- ✅ Feature Flags (ENABLE_RICH_LOGGING, ENABLE_LIVE_MONITORS, etc.)
- ✅ Validation Tests (tests/test_terminal_ui.py, tests/validate_terminal_ui.py)

---

## 🎯 Nächste Schritte (Empfohlen)

### ✅ Option A: Terminal UI Vervollständigen - ABGESCHLOSSEN
- ✅ Live Monitors implementiert (LiveHeartbeat, DropMonitorView, PortfolioMonitorView, LiveDashboard)
- ✅ Rich Logging Integration abgeschlossen
- ✅ Feature Flags hinzugefügt
- ✅ Validationstests erstellt

### Option B: Testen & Deployment (2-3h) - EMPFOHLEN
1. **Integration Tests schreiben**
   - End-to-End Buy/Sell mit allen Guards
   - Coalescing unter Last
   - Rate-Limiting Behavior
2. **Performance Testing**
   - Load Test mit 100 parallelen Requests
   - Memory Profiling (Ringbuffer)
3. **Deployment Preparation**
   - Feature Flags Review
   - Documentation Update
   - Rollout Plan

### Option C: Phase 3 FSM (Optional, 2h)
- Explizite State Machine für Order Lifecycle
- Nur falls explizite State-Validierung gewünscht

---

## 📝 Notizen

- Alle Features sind **backward-compatible**
- Feature Flags erlauben schrittweises Rollout
- Tests vorhanden für kritische Pfade
- Rich Library ist optional (Fallback auf Plain Text)

**Empfehlung**: Start mit Option A (Terminal UI) für bessere Developer Experience, dann Option B (Testing) vor Production Rollout.
