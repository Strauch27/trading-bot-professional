# FSM Architecture

## Overview

The FSM (Finite State Machine) Trading Engine replaces the previous implicit state management with an explicit, traceable state machine for each symbol. This dramatically improves debuggability, testability, and observability.

## Why FSM?

### Problems with Legacy Architecture
- **Scattered State**: Position state spread across multiple dicts (`held_assets`, `open_buy_orders`, `pending_exits`)
- **Implicit Transitions**: State changes happened implicitly through flag updates
- **Hard to Debug**: No audit trail of state transitions
- **Race Conditions**: Concurrent access to shared state without proper synchronization
- **Limited Observability**: No way to see which phase each symbol is in

### FSM Benefits
- **Explicit States**: Each symbol has a clear phase (IDLE, ENTRY_EVAL, PLACE_BUY, etc.)
- **Traceable Transitions**: Every phase change logged to JSONL with timestamp
- **Centralized State**: All symbol state in single `CoinState` dataclass
- **Thread-Safe**: RLock-based synchronization throughout
- **Observable**: Live status table + Prometheus metrics
- **Testable**: Each phase handler is an isolated function

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────┐
│                      main.py                                 │
│  - Config loading                                            │
│  - HybridEngine initialization (mode: legacy/fsm/both)       │
│  - Prometheus server + Rich status table (optional)          │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  HybridEngine                                │
│  - Mode selection: "legacy", "fsm", or "both"                │
│  - Parallel validation (when mode="both")                    │
│  - State bridging (legacy positions → FSM states)            │
└─────────────────────────────────────────────────────────────┘
                            │
          ┌─────────────────┴─────────────────┐
          ▼                                   ▼
┌──────────────────┐              ┌──────────────────────┐
│  Legacy Engine   │              │  FSMTradingEngine    │
│  (engine.py)     │              │  (fsm_engine.py)     │
└──────────────────┘              └──────────────────────┘
                                            │
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
              ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
              │ StateMachine │   │ PhaseLogger  │   │ Metrics      │
              │ (machine.py) │   │ (events.py)  │   │ (metrics.py) │
              └──────────────┘   └──────────────┘   └──────────────┘
                      │
                      ▼
              ┌──────────────┐
              │  CoinState   │
              │  (state.py)  │
              └──────────────┘
```

## Core Components

### 1. CoinState (core/fsm/state.py)
Central dataclass holding all state for a symbol:

```python
@dataclass
class CoinState:
    symbol: str
    phase: Phase                      # Current phase

    # Context
    decision_id: Optional[str]        # Unique decision ID
    order_id: Optional[str]           # Current order ID
    client_order_id: Optional[str]    # Client order ID

    # Timestamps
    ts_ms: int                        # Last update timestamp
    entry_ts: float                   # Position entry time
    cooldown_until: float             # Cooldown expiry
    order_placed_ts: float            # Order placement time

    # Position
    amount: float                     # Position size
    entry_price: float                # Entry price
    current_price: float              # Latest price
    entry_fee_per_unit: float         # Fees per unit

    # Trailing Stop
    peak_price: float                 # Highest price seen
    trailing_trigger: float           # Trailing stop trigger

    # Drop Trigger
    anchor_price: float               # Drop anchor price
    anchor_ts: Optional[str]          # Anchor timestamp

    # Exit Protection
    sl_order_id: Optional[str]        # Stop-loss order ID
    tp_order_id: Optional[str]        # Take-profit order ID
    sl_px: float                      # Stop-loss price
    tp_px: float                      # Take-profit price

    # Metadata
    note: str                         # Human-readable note
    signal: str                       # Entry signal name
    exit_reason: str                  # Exit reason
    error_count: int                  # Error counter
    retry_count: int                  # Retry counter
    last_error: str                   # Last error message

    # History
    phase_history: List[Dict]         # Phase transition history
```

### 2. Phase Enum (core/fsm/phases.py)
12 explicit phases covering full trading lifecycle:

```python
class Phase(str, Enum):
    WARMUP = "warmup"                 # Backfill + initialization
    IDLE = "idle"                     # Wait for signal
    ENTRY_EVAL = "entry_eval"         # Check guards + signals
    PLACE_BUY = "place_buy"           # Place buy order
    WAIT_FILL = "wait_fill"           # Wait for buy fill
    POSITION = "position"             # Hold position
    EXIT_EVAL = "exit_eval"           # Check TP/SL/trailing
    PLACE_SELL = "place_sell"         # Place sell order
    WAIT_SELL_FILL = "wait_sell_fill" # Wait for sell fill
    POST_TRADE = "post_trade"         # Record PnL + cleanup
    COOLDOWN = "cooldown"             # 15-min cooldown
    ERROR = "error"                   # Error recovery
```

### 3. StateMachine (core/fsm/machine.py)
Central registry managing all symbol states:

```python
class StateMachine:
    def __init__(self, phase_logger=None, metrics=None):
        self.states: Dict[str, CoinState] = {}
        self.transitions: Dict[Phase, Callable] = {}
        self._lock = threading.RLock()

    # Key Methods:
    register_symbol(symbol, initial_phase=Phase.WARMUP) -> CoinState
    register_transition(phase: Phase, handler: Callable)
    process_symbol(symbol: str, context: Dict[str, Any])
    get_states_by_phase(phase: Phase) -> List[CoinState]
    get_stuck_states(timeout_seconds=60.0) -> List[CoinState]
```

### 4. FSMTradingEngine (engine/fsm_engine.py)
Orchestrator connecting FSM to trading services:

```python
class FSMTradingEngine:
    def __init__(self, exchange, portfolio, orderbookprovider, telegram=None, watchlist=None):
        self.fsm = StateMachine(log=self.phase_logger, metrics=self.metrics)

        # Service Layer
        self.buy_signal_service = BuySignalService(...)
        self.market_guards = MarketGuards(...)
        self.order_service = OrderService(...)
        self.exit_manager = ExitManager(...)
        self.pnl_service = PnLService(...)

        # Register all 12 phase handlers
        self._register_transitions()

    def start(self):
        # Initialize symbols in WARMUP
        # Start main loop (process each symbol every cycle)

    def stop(self):
        # Close all positions
        # Flush logs and metrics
```

### 5. HybridEngine (engine/hybrid_engine.py)
Migration safety layer supporting parallel operation:

```python
class HybridEngine:
    def __init__(self, ..., mode: str = "legacy"):
        self.mode = mode  # "legacy", "fsm", or "both"

        if mode in ["legacy", "both"]:
            self.legacy_engine = TradingEngine(...)

        if mode in ["fsm", "both"]:
            self.fsm_engine = FSMTradingEngine(...)

        if mode == "both":
            # Start validation monitor
            self._start_validation_monitor()

    def get_states(self):
        # Return FSM states (or bridged legacy states)
```

## Phase Flow

### Complete Trading Lifecycle

```
START
  │
  ▼
WARMUP ────────────────────────────────────────┐
  │ Backfill history                           │
  │ Initialize drop anchors                    │
  ▼                                            │
IDLE ──────────────────────────────────────┐   │
  │ Wait for signal                         │   │
  │ Check slot availability                 │   │
  │ Check cooldown                          │   │
  ▼                                         │   │
ENTRY_EVAL ────────────────────────┐        │   │
  │ Update market guards            │        │   │
  │ Check all guards                │        │   │
  │ Evaluate buy signal             │        │   │
  ▼                                 │        │   │
  Guards passed?                    │        │   │
  ├─ NO → IDLE                      │        │   │
  └─ YES                            │        │   │
      ▼                             │        │   │
PLACE_BUY ──────────────────────┐   │        │   │
  │ Calculate position size      │   │        │   │
  │ Place buy order (IOC/GTC)    │   │        │   │
  ▼                              │   │        │   │
  Order placed?                  │   │        │   │
  ├─ NO → IDLE                   │   │        │   │
  └─ YES                         │   │        │   │
      ▼                          │   │        │   │
WAIT_FILL ───────────────────┐   │   │        │   │
  │ Poll order status         │   │   │        │   │
  │ Check timeout (30s)       │   │   │        │   │
  ▼                           │   │   │        │   │
  Filled?                     │   │   │        │   │
  ├─ NO + Timeout → IDLE      │   │   │        │   │
  └─ YES                      │   │   │        │   │
      ▼                       │   │   │        │   │
      Record fill             │   │   │        │   │
      Set TP/SL               │   │   │        │   │
      Add to portfolio        │   │   │        │   │
      ▼                       │   │   │        │   │
POSITION ─────────────────┐   │   │   │        │   │
  │ Update current price   │   │   │   │        │   │
  │ Update trailing stops  │   │   │   │        │   │
  │ Update unrealized PnL  │   │   │   │        │   │
  ▼                        │   │   │   │        │   │
  Check exit? (every 2s)   │   │   │   │        │   │
  └─ YES                   │   │   │   │        │   │
      ▼                    │   │   │   │        │   │
EXIT_EVAL ──────────────┐  │   │   │   │        │   │
  │ Check TP              │  │   │   │   │        │   │
  │ Check SL              │  │   │   │   │        │   │
  │ Check trailing stop   │  │   │   │   │        │   │
  │ Check timeout         │  │   │   │   │        │   │
  ▼                       │  │   │   │   │        │   │
  Exit triggered?         │  │   │   │   │        │   │
  ├─ NO → POSITION        │  │   │   │   │        │   │
  └─ YES                  │  │   │   │   │        │   │
      ▼                   │  │   │   │   │        │   │
PLACE_SELL ──────────┐    │  │   │   │   │        │   │
  │ Place sell order  │    │  │   │   │   │        │   │
  │ (IOC with ladder) │    │  │   │   │   │        │   │
  ▼                   │    │  │   │   │   │        │   │
  Order placed?       │    │  │   │   │   │        │   │
  ├─ NO → EXIT_EVAL   │    │  │   │   │   │        │   │
  └─ YES              │    │  │   │   │   │        │   │
      ▼               │    │  │   │   │   │        │   │
WAIT_SELL_FILL ───┐   │    │  │   │   │   │        │   │
  │ Poll order      │   │    │  │   │   │   │        │   │
  │ Check timeout   │   │    │  │   │   │   │        │   │
  ▼                 │   │    │  │   │   │   │        │   │
  Filled?           │   │    │  │   │   │   │        │   │
  ├─ NO + Timeout   │   │    │  │   │   │   │        │   │
  │  → PLACE_SELL   │   │    │  │   │   │   │        │   │
  ├─ Partial Fill   │   │    │  │   │   │   │        │   │
  │  → PLACE_SELL   │   │    │  │   │   │   │        │   │
  └─ YES (>95%)     │   │    │  │   │   │   │        │   │
      ▼             │   │    │  │   │   │   │        │   │
POST_TRADE ──────┐  │   │    │  │   │   │   │        │   │
  │ Record PnL    │  │   │    │  │   │   │   │        │   │
  │ Remove from   │  │   │    │  │   │   │   │        │   │
  │  portfolio    │  │   │    │  │   │   │   │        │   │
  │ Telegram      │  │   │    │  │   │   │   │        │   │
  │  notify       │  │   │    │  │   │   │   │        │   │
  ▼               │  │   │    │  │   │   │   │        │   │
COOLDOWN ───────┐ │  │   │    │  │   │   │   │        │   │
  │ Wait 15 min  │ │  │   │    │  │   │   │   │        │   │
  ▼              │ │  │   │    │  │   │   │   │        │   │
  Expired?       │ │  │   │    │  │   │   │   │        │   │
  └─ YES → IDLE ─┴─┴──┴───┴────┴──┴───┴───┴───┴────────┴───┘

ERROR ────────────┐
  │ Exponential   │
  │ backoff       │
  │ (max 5 min)   │
  └─ Retry → IDLE
```

## Phase Handlers

### Entry Phase Handlers

#### WARMUP → IDLE
- **Trigger**: Symbol initialization
- **Actions**:
  - Backfill market history (if `BACKFILL_MINUTES` > 0)
  - Initialize drop anchor (if `USE_DROP_ANCHOR`)
- **Next**: IDLE

#### IDLE → ENTRY_EVAL
- **Trigger**: Slot available + cooldown expired
- **Actions**:
  - Check `MAX_TRADES` limit
  - Generate decision ID
- **Next**: ENTRY_EVAL

#### ENTRY_EVAL → PLACE_BUY / IDLE
- **Trigger**: Price update
- **Actions**:
  - Update market guards (spread, volume, SMA, etc.)
  - Check all guards
  - Evaluate buy signal (drop trigger)
- **Next**:
  - PLACE_BUY if guards passed + signal triggered
  - IDLE otherwise

#### PLACE_BUY → WAIT_FILL / IDLE
- **Trigger**: Entry from ENTRY_EVAL
- **Actions**:
  - Calculate position size (budget / price)
  - Generate client order ID
  - Place buy order (IOC or GTC based on config)
- **Next**:
  - WAIT_FILL if order placed successfully
  - IDLE on error

#### WAIT_FILL → POSITION / IDLE
- **Trigger**: Periodic polling (every cycle)
- **Actions**:
  - Poll order status
  - Check timeout (30s default)
  - Cancel order on timeout
- **Next**:
  - POSITION if filled
  - IDLE on timeout or cancel

### Position Phase Handlers

#### POSITION → EXIT_EVAL
- **Trigger**: Every 4 cycles (~2 seconds)
- **Actions**:
  - Update current price
  - Update trailing stop triggers
  - Update unrealized PnL
- **Next**: EXIT_EVAL

#### EXIT_EVAL → PLACE_SELL / POSITION
- **Trigger**: Entry from POSITION
- **Actions**:
  - Check take-profit threshold
  - Check stop-loss threshold
  - Check trailing stop trigger
  - Check max hold timeout
- **Next**:
  - PLACE_SELL if exit triggered
  - POSITION otherwise

### Exit Phase Handlers

#### PLACE_SELL → WAIT_SELL_FILL / EXIT_EVAL
- **Trigger**: Exit signal from EXIT_EVAL
- **Actions**:
  - Generate client order ID
  - Place IOC sell order
  - Try ladder pricing on failure
- **Next**:
  - WAIT_SELL_FILL if order placed
  - EXIT_EVAL on error (retry)

#### WAIT_SELL_FILL → POST_TRADE / PLACE_SELL
- **Trigger**: Periodic polling
- **Actions**:
  - Poll order status
  - Check timeout (20s default)
  - Handle partial fills
- **Next**:
  - POST_TRADE if >95% filled
  - PLACE_SELL if partial fill (retry with remaining)
  - PLACE_SELL on timeout (retry)

#### POST_TRADE → COOLDOWN
- **Trigger**: Sell order filled
- **Actions**:
  - Calculate realized PnL
  - Record fill in PnL service
  - Remove from portfolio
  - Send Telegram notification
  - Set cooldown expiry
- **Next**: COOLDOWN

#### COOLDOWN → IDLE
- **Trigger**: Cooldown expired
- **Actions**:
  - Check cooldown timer
- **Next**: IDLE when cooldown expired

### Error Recovery

#### ERROR → IDLE
- **Trigger**: Exception in any handler
- **Actions**:
  - Increment error counter
  - Exponential backoff (10s → 20s → 40s → ... → max 300s)
  - Log error details
- **Next**: IDLE after backoff

## Configuration

### config.py Section 11: FSM Configuration

```python
# Master Switch & Mode Selection
FSM_ENABLED = False  # False = Legacy engine (default), True = Use FSM_MODE
FSM_MODE = "legacy"  # Options: "legacy", "fsm", or "both"

# Phase Event Logging
PHASE_LOG_FILE = os.path.join(LOG_DIR, f"phase_events_{run_timestamp}.jsonl")
PHASE_LOG_BUFFER_SIZE = 8192

# Rich Terminal Status Table
ENABLE_RICH_TABLE = False  # Enable live-updating FSM status table
RICH_TABLE_REFRESH_HZ = 2.0  # Refresh rate
RICH_TABLE_SHOW_IDLE = False  # Show IDLE/WARMUP symbols

# Prometheus Metrics
ENABLE_PROMETHEUS = False  # Enable Prometheus HTTP server
PROMETHEUS_PORT = 8000  # Metrics endpoint

# FSM Phase Timeouts (seconds)
BUY_ORDER_TIMEOUT_SECONDS = 30  # Max time in WAIT_FILL
SELL_ORDER_TIMEOUT_SECONDS = 20  # Max time in WAIT_SELL_FILL
MAX_POSITION_HOLD_MINUTES = 60  # Auto-exit timeout

# FSM Error Recovery
FSM_MAX_RETRIES = 5  # Max retries before ERROR phase
FSM_BACKOFF_BASE_SECONDS = 10  # Exponential backoff base

# Hybrid Mode Validation (when FSM_MODE="both")
HYBRID_VALIDATION_INTERVAL_S = 60  # Comparison check interval
HYBRID_LOG_DIVERGENCES = True  # Log when legacy/FSM diverge
```

## Migration Path

### Phase 1: Validation (FSM_MODE="both")
1. Set `FSM_ENABLED=True`, `FSM_MODE="both"`
2. Both engines run in parallel
3. Validation monitor compares states every 60s
4. Check logs for divergences
5. Run for 24-48 hours

### Phase 2: Shadow Mode (FSM_MODE="fsm" + observe)
1. Set `FSM_MODE="fsm"`, `GLOBAL_TRADING=False`
2. FSM engine runs without executing trades
3. Monitor phase transitions in logs
4. Check Prometheus metrics
5. Run for 24 hours

### Phase 3: Live Cutover (FSM_MODE="fsm" + trading)
1. Set `FSM_MODE="fsm"`, `GLOBAL_TRADING=True`
2. Start with small position sizes
3. Monitor closely for first few trades
4. Gradually increase to normal sizes

### Phase 4: Legacy Removal
1. Remove legacy engine code
2. Update HybridEngine to directly use FSMTradingEngine
3. Clean up backward compatibility code

## Design Decisions

### Why 12 Phases?
- **Granular Observability**: Each phase represents a distinct operational state
- **Clear Transitions**: No ambiguity about what happens when
- **Debuggability**: Easy to see exactly where a symbol is stuck
- **Separation of Concerns**: Each handler has single responsibility

### Why JSONL for Events?
- **Append-Only**: No file locking issues
- **Line-Oriented**: Easy to parse with streaming tools
- **Human-Readable**: Can `tail -f` for debugging
- **Queryable**: Use `jq` for complex queries

### Why Prometheus for Metrics?
- **Industry Standard**: Widely used, well-documented
- **Time-Series**: Built for metrics over time
- **Grafana Integration**: Easy to build dashboards
- **Alerting**: Built-in alertmanager support

### Why Rich for Terminal UI?
- **Live Updates**: Real-time view of FSM state
- **Color-Coded**: Visual feedback on phase health
- **Sorted**: Most important symbols (errors, active trades) first
- **Zero Config**: Works out of the box

## Performance Considerations

### CPU Usage
- **Minimal Overhead**: Phase transitions are fast (<1ms)
- **Batch Processing**: States processed in single pass
- **Lazy Evaluation**: Only active symbols checked for exits

### Memory Usage
- **Bounded**: Each CoinState is ~2KB
- **Fixed Size**: No unbounded growth (phase_history limited)
- **Efficient**: Dataclass uses `__slots__` internally

### I/O Impact
- **Buffered Logging**: 8KB buffer reduces write frequency
- **Batched Metrics**: Prometheus client batches updates
- **Async Rich**: Status table updates don't block main loop

## Testing Strategy

### Unit Tests
- Test each phase handler in isolation
- Mock external dependencies (exchange, portfolio)
- Verify state transitions

### Integration Tests
- Test full trading lifecycle (WARMUP → IDLE → ... → COOLDOWN)
- Use exchange simulator
- Verify event logging and metrics

### Validation Tests
- Run both engines in parallel (`FSM_MODE="both"`)
- Compare position counts, PnL, trade history
- Verify no divergence over 24h

## See Also
- [FSM Debugging Guide](FSM_DEBUGGING.md) - Troubleshooting and common issues
- [FSM Metrics Reference](FSM_METRICS.md) - Prometheus metrics and queries
- [CONFIG_README.md](../CONFIG_README.md) - Full configuration reference
