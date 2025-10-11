"""
FSM State Management

CoinState: Complete state for one symbol
set_phase: Transition helper with logging and metrics
"""

from dataclasses import dataclass, field
from time import time
from typing import Optional, Dict, Any, List
from .phases import Phase


@dataclass
class CoinState:
    """
    Complete FSM state for a single symbol.

    This replaces scattered state across:
    - held_assets (portfolio)
    - open_buy_orders (portfolio)
    - pending_exits (signal manager)
    - trailing stops (trailing manager)

    All state is now centralized in this dataclass.
    """

    # ========== Identity ==========
    symbol: str
    phase: Phase = Phase.WARMUP

    # ========== Context ==========
    decision_id: Optional[str] = None
    """Current decision ID for audit trail"""

    order_id: Optional[str] = None
    """Current order ID (buy or sell)"""

    client_order_id: Optional[str] = None
    """Client order ID for tracking"""

    # ========== Timestamps ==========
    ts_ms: int = 0
    """Timestamp of last phase change (milliseconds since epoch)"""

    entry_ts: float = 0.0
    """Position entry timestamp (seconds since epoch)"""

    cooldown_until: float = 0.0
    """Cooldown expires at this timestamp (seconds since epoch)"""

    order_placed_ts: float = 0.0
    """When current order was placed (for timeout detection)"""

    # ========== Position Data ==========
    amount: float = 0.0
    """Holding amount (base currency)"""

    entry_price: float = 0.0
    """Average entry price (quote currency)"""

    current_price: float = 0.0
    """Last observed price"""

    entry_fee_per_unit: float = 0.0
    """Buy fee per unit (for PnL calculation)"""

    # ========== Trailing Stop ==========
    peak_price: float = 0.0
    """Highest price since entry (for trailing stop)"""

    trailing_trigger: float = 0.0
    """Current trailing stop trigger price"""

    # ========== Drop Trigger ==========
    anchor_price: float = 0.0
    """Drop anchor price (for drop trigger calculation)"""

    anchor_ts: Optional[str] = None
    """Drop anchor timestamp (ISO format)"""

    # ========== Exit Protection ==========
    sl_order_id: Optional[str] = None
    """Stop loss order ID"""

    tp_order_id: Optional[str] = None
    """Take profit order ID"""

    sl_px: float = 0.0
    """Stop loss trigger price"""

    tp_px: float = 0.0
    """Take profit trigger price"""

    # ========== Metadata ==========
    note: str = ""
    """Human-readable note about current state"""

    signal: str = ""
    """Buy signal that triggered entry (DROP_BUY, etc.)"""

    exit_reason: str = ""
    """Reason for exit (TAKE_PROFIT, STOP_LOSS, etc.)"""

    # ========== Error Handling ==========
    error_count: int = 0
    """Total error count for this symbol"""

    retry_count: int = 0
    """Retry count for current operation"""

    last_error: str = ""
    """Last error message"""

    # ========== History (for Replay) ==========
    phase_history: List[Dict[str, Any]] = field(default_factory=list)
    """
    Complete phase transition history:
    [{"ts": 123, "from": "idle", "to": "entry_eval", "note": "..."}, ...]
    """

    def age_seconds(self) -> float:
        """Returns seconds since last phase change."""
        if self.ts_ms == 0:
            return 0.0
        return time() - (self.ts_ms / 1000.0)

    def in_cooldown(self) -> bool:
        """Returns True if symbol is in cooldown."""
        if self.cooldown_until == 0.0:
            return False
        return time() < self.cooldown_until

    def has_position(self) -> bool:
        """Returns True if holding a position (amount > 0)."""
        return self.amount > 1e-8

    def unrealized_pnl(self) -> float:
        """Calculate unrealized PnL (quick estimate, not including fees)."""
        if not self.has_position():
            return 0.0
        return (self.current_price - self.entry_price) * self.amount

    def get_phase_summary(self) -> str:
        """Returns human-readable phase summary for logging."""
        parts = [f"{self.symbol}:{self.phase.value}"]

        if self.decision_id:
            parts.append(f"dec={self.decision_id[-8:]}")

        if self.has_position():
            parts.append(f"amt={self.amount:.4f}@{self.entry_price:.4f}")

        if self.note:
            parts.append(f"note={self.note[:30]}")

        return " | ".join(parts)


def set_phase(
    st: CoinState,
    to: Phase,
    *,
    note: str = "",
    decision_id: Optional[str] = None,
    order_id: Optional[str] = None,
    log = None,
    metrics = None
) -> Dict[str, Any]:
    """
    Transition to new phase with logging and metrics.

    This is the ONLY function that should change st.phase.
    All phase changes must go through this function for proper tracking.

    Args:
        st: CoinState to update
        to: Target phase
        note: Human-readable reason for transition
        decision_id: Optional decision ID to set
        order_id: Optional order ID to set
        log: Logger instance (expects .info() method)
        metrics: Metrics instance (expects .phase_changes, .phase_code)

    Returns:
        Event dict that was logged (for testing/debugging)
    """
    prev_phase = st.phase

    # Update state
    st.phase = to
    st.ts_ms = int(time() * 1000)
    st.note = note

    if decision_id is not None:
        st.decision_id = decision_id

    if order_id is not None:
        st.order_id = order_id

    # Build event
    evt = {
        "ts_ms": st.ts_ms,
        "event": "PHASE_CHANGE",
        "symbol": st.symbol,
        "prev": prev_phase.value if prev_phase else None,
        "next": to.value,
        "decision_id": st.decision_id,
        "order_id": st.order_id,
        "note": note,
        "error_count": st.error_count,
        "retry_count": st.retry_count,
    }

    # Add position context if holding
    if st.has_position():
        evt["amount"] = st.amount
        evt["entry_price"] = st.entry_price
        evt["current_price"] = st.current_price
        evt["unrealized_pnl"] = st.unrealized_pnl()

    # Update history
    st.phase_history.append({
        "ts": st.ts_ms,
        "from": prev_phase.value if prev_phase else None,
        "to": to.value,
        "note": note,
        "decision_id": st.decision_id,
    })

    # Keep history limited to last 100 transitions (memory management)
    if len(st.phase_history) > 100:
        st.phase_history = st.phase_history[-100:]

    # Log event (JSONL)
    if log:
        try:
            log.info(evt)
        except Exception as e:
            # Don't let logging errors break state transitions
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Phase event logging failed: {e}",
                          extra={'event_type': 'PHASE_LOG_ERROR', 'error': str(e)})

    # Update metrics (Prometheus)
    if metrics:
        try:
            # Phase change counter
            metrics.phase_changes.labels(
                symbol=st.symbol,
                phase=to.value
            ).inc()

            # Current phase gauge
            if hasattr(metrics, 'PHASE_MAP') and hasattr(metrics, 'phase_code'):
                metrics.phase_code.labels(
                    symbol=st.symbol
                ).set(metrics.PHASE_MAP.get(to, -1))
        except Exception as e:
            # Don't let metrics errors break state transitions
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Phase metrics update failed: {e}",
                          extra={'event_type': 'PHASE_METRICS_ERROR', 'error': str(e)})

    return evt


def reset_state(st: CoinState, keep_history: bool = False):
    """
    Reset state to IDLE (useful after errors or manual interventions).

    Args:
        st: CoinState to reset
        keep_history: If True, keep phase_history; if False, clear it
    """
    st.phase = Phase.IDLE
    st.decision_id = None
    st.order_id = None
    st.client_order_id = None
    st.amount = 0.0
    st.entry_price = 0.0
    st.entry_ts = 0.0
    st.entry_fee_per_unit = 0.0
    st.peak_price = 0.0
    st.trailing_trigger = 0.0
    st.sl_order_id = None
    st.tp_order_id = None
    st.sl_px = 0.0
    st.tp_px = 0.0
    st.signal = ""
    st.exit_reason = ""
    st.retry_count = 0
    st.order_placed_ts = 0.0
    st.note = "reset to idle"

    if not keep_history:
        st.phase_history.clear()
