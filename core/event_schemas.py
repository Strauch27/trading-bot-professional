#!/usr/bin/env python3
"""
Event Schemas - Pydantic Models for Structured Logging

Provides type-safe schemas for all trading events:
- Buy Decision Events (sizing, guards, triggers)
- Sell Decision Events (TP/SL, trailing, sizing)
- Order Events (attempt, ack, update, done)
- Portfolio Events (snapshots, lifecycle, reconciliation)
- Exchange Events (API calls)

Usage:
    from core.event_schemas import SellTriggerEval
    from core.logger_factory import DECISION_LOG, log_event

    ev = SellTriggerEval(
        symbol="BTC/USDT",
        trigger="tp",
        entry_price=50000.0,
        current_price=51000.0,
        unrealized_pct=2.0,
        threshold=50500.0,
        hit=True
    )
    log_event(DECISION_LOG(), "sell_trigger_eval", **ev.model_dump())
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


# =============================================================================
# BUY DECISION EVENTS
# =============================================================================

class SizingCalc(BaseModel):
    """Buy sizing calculation result."""
    symbol: str
    position_size_usdt_cfg: float
    quote_budget: float
    min_notional: float
    fees_bps: int
    slippage_bps: int
    qty_raw: float
    qty_rounded: float
    quote_after_round: float
    passed: bool
    fail_reason: Optional[str] = None
    market_limits: Optional[Dict[str, float]] = None
    market_limits_hash: Optional[str] = None


class GuardResult(BaseModel):
    """Single guard evaluation result."""
    name: str
    passed: bool
    value: Optional[float] = None
    threshold: Optional[float] = None
    reason: Optional[str] = None


# =============================================================================
# SELL DECISION EVENTS
# =============================================================================

class SellTriggerEval(BaseModel):
    """
    Sell trigger evaluation (TP/SL/Trailing/Signal).

    Fields:
        trigger: Type of trigger ("tp", "sl", "trailing", "signal", "ttl")
        entry_price: Position average entry price
        current_price: Current market price
        unrealized_pct: Unrealized P&L in percent
        rr_ratio: Risk/Reward ratio (optional)
        trailing_anchor: Current trailing anchor price (for trailing stops)
        threshold: Trigger threshold (TP/SL level)
        hit: Whether trigger condition is met
    """
    symbol: str
    trigger: str  # "tp" | "sl" | "trailing" | "signal" | "ttl"
    entry_price: float
    current_price: float
    unrealized_pct: float
    rr_ratio: Optional[float] = None
    trailing_anchor: Optional[float] = None
    threshold: Optional[float] = None
    hit: bool
    reason: Optional[str] = None


class SellSizingCalc(BaseModel):
    """
    Sell sizing calculation result.

    Fields:
        pos_qty_avail: Available position quantity
        qty_raw: Raw quantity before rounding
        qty_rounded: Rounded quantity after precision adjustment
        min_qty: Exchange minimum quantity
        min_notional: Exchange minimum notional
        notional: Total notional value (qty * price)
        passed: Whether sizing passed all checks
        fail_reason: Reason if failed
    """
    symbol: str
    pos_qty_avail: float
    qty_raw: float
    qty_rounded: float
    min_qty: float
    min_notional: float
    notional: float
    passed: bool
    fail_reason: Optional[str] = None


class TrailingUpdate(BaseModel):
    """
    Trailing stop update event.

    Fields:
        mode: Trailing mode ("percent_bps", "atr", etc.)
        anchor_old: Previous trailing anchor
        anchor_new: New trailing anchor (updated high/low)
        distance_bps: Distance in basis points from anchor
        armed: Whether trailing is active
        hit: Whether stop was hit
    """
    symbol: str
    mode: str  # "percent_bps", "atr", etc.
    anchor_old: float
    anchor_new: float
    distance_bps: int
    armed: bool
    hit: bool
    stop_price: Optional[float] = None


# =============================================================================
# ORDER EVENTS
# =============================================================================

class OrderAttempt(BaseModel):
    """Order creation attempt."""
    symbol: str
    side: str  # "buy" | "sell"
    type: str  # "limit" | "market"
    tif: Optional[str] = None  # "GTC" | "IOC" | "FOK"
    price: Optional[float] = None
    qty: float
    notional: float
    client_order_id: str


class OrderDone(BaseModel):
    """Final order status."""
    symbol: str
    side: str
    final_status: str  # "filled" | "canceled" | "expired" | "rejected"
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    total_cost: Optional[float] = None
    fee_quote: Optional[float] = None
    latency_ms_total: Optional[int] = None
    reason: Optional[str] = None


# =============================================================================
# PORTFOLIO EVENTS
# =============================================================================

class PositionOpened(BaseModel):
    """Position opened event."""
    symbol: str
    qty: float
    avg_entry: float
    notional: float
    fee_accum: float = 0.0
    opened_at: Optional[str] = None  # ISO timestamp


class PositionUpdated(BaseModel):
    """
    Position updated event - covers both quantity changes and state updates.

    For quantity changes (averaging, partial exits):
        - qty_before, qty_after, avg_entry_before, avg_entry_after, notional_change

    For state updates during lifetime (Phase 1, TODO 5):
        - qty, unrealized_pnl, unrealized_pct, peak_price, trailing_stop, age_minutes
    """
    symbol: str

    # Quantity change fields (for averaging/partial exits)
    qty_before: Optional[float] = None
    qty_after: Optional[float] = None
    avg_entry_before: Optional[float] = None
    avg_entry_after: Optional[float] = None
    notional_change: Optional[float] = None
    fee_accum: Optional[float] = None

    # State update fields (for PnL tracking during lifetime)
    qty: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pct: Optional[float] = None
    peak_price: Optional[float] = None
    trailing_stop: Optional[float] = None
    age_minutes: Optional[float] = None


class PositionClosed(BaseModel):
    """Position closed event."""
    symbol: str
    qty_closed: float
    avg_entry: float
    exit_price: float
    realized_pnl_usdt: float
    realized_pnl_pct: float
    fee_total: float
    duration_minutes: Optional[float] = None
    reason: str  # "tp" | "sl" | "trailing" | "signal" | "ttl"


class PortfolioSnapshot(BaseModel):
    """
    Portfolio snapshot for equity tracking.

    Fields:
        equity_total: Total equity (cash + position value)
        cash_free: Free USDT cash
        positions_count: Number of open positions
        exposure: Dict of symbol -> notional exposure
        realized_pnl: Cumulative realized P&L
        unrealized_pnl: Current unrealized P&L across all positions
    """
    equity_total: float
    cash_free: float
    positions_count: int
    exposure: Dict[str, float]
    realized_pnl: float
    unrealized_pnl: float = 0.0


class RiskLimitsEval(BaseModel):
    """
    Risk limits evaluation - comprehensive check of all risk limits before order placement.

    Fields:
        symbol: Trading symbol
        limit_checks: List of individual limit checks with results
        all_passed: Whether all limits passed
        blocking_limit: First limit that failed (if any)
    """
    symbol: str
    limit_checks: List[Dict[str, Any]]  # [{"limit": "max_exposure", "value": 0.45, "threshold": 0.50, "hit": False}, ...]
    all_passed: bool
    blocking_limit: Optional[str] = None


class ReconciliationResult(BaseModel):
    """
    Exchange vs Local state reconciliation result.

    Fields:
        cash_diff: Difference in USDT balance (exchange - local)
        positions_missing_local: Positions on exchange but not in local state
        positions_missing_exchange: Positions in local state but not on exchange
        position_qty_diffs: Dict of symbol -> qty difference
    """
    cash_diff: float
    positions_missing_local: List[str] = []
    positions_missing_exchange: List[str] = []
    position_qty_diffs: Dict[str, float] = {}
    drift_detected: bool = False


# =============================================================================
# EXCHANGE/TRACER EVENTS
# =============================================================================

class ExchangeCall(BaseModel):
    """Exchange API call trace."""
    api: str  # "public" | "private"
    method: str  # "fetch_ticker" | "create_order" | etc.
    params: Dict[str, Any]
    latency_ms: int
    http_status: Optional[int] = None
    ok: bool
    error_class: Optional[str] = None
    error_message: Optional[str] = None


class PriceSnapshot(BaseModel):
    """Price and orderbook snapshot before order."""
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    spread_bps: Optional[float] = None
    depth_top5_bids: Optional[List[List[float]]] = None  # [[price, qty], ...]
    depth_top5_asks: Optional[List[List[float]]] = None


# =============================================================================
# PHASE 2: IDEMPOTENCY & RETRY EVENTS
# =============================================================================

class DuplicateOrderBlocked(BaseModel):
    """
    Duplicate order detection via idempotency store.

    Fields:
        order_req_id: The order request ID that was duplicate
        symbol: Trading symbol
        side: Order side ("buy" | "sell")
        existing_exchange_order_id: Existing order ID on exchange
        existing_status: Status of existing order
        age_seconds: Age of the existing order request
    """
    order_req_id: str
    symbol: str
    side: str
    existing_exchange_order_id: str
    existing_status: str
    age_seconds: float


class RetryAttempt(BaseModel):
    """
    Retry attempt tracking with exponential backoff.

    Fields:
        symbol: Trading symbol (if applicable)
        operation: Function/operation being retried
        attempt: Current attempt number (1-indexed)
        max_retries: Maximum retry attempts configured
        error_class: Exception class name
        error_message: Exception message
        backoff_ms: Backoff time in milliseconds before next retry
        will_retry: Whether another retry will be attempted
    """
    symbol: str
    operation: str  # "place_order", "cancel_order", "fetch_balance", etc.
    attempt: int
    max_retries: int
    error_class: str
    error_message: str
    backoff_ms: int
    will_retry: bool


class OrderFill(BaseModel):
    """
    Order fill event (partial or full).

    Fields:
        symbol: Trading symbol
        exchange_order_id: Exchange-assigned order ID
        fill_qty: Quantity filled in this fill
        fill_price: Average fill price
        fill_cost: Total fill cost (qty * price)
        fee_quote: Fee in quote currency (USDT)
        is_full_fill: Whether this is the final fill
        cumulative_filled: Total quantity filled so far
        remaining_qty: Remaining quantity to fill
    """
    symbol: str
    exchange_order_id: str
    fill_qty: float
    fill_price: float
    fill_cost: float
    fee_quote: float = 0.0
    is_full_fill: bool
    cumulative_filled: float
    remaining_qty: float


class OrderCancel(BaseModel):
    """
    Order cancellation event.

    Fields:
        symbol: Trading symbol
        exchange_order_id: Exchange-assigned order ID
        reason: Cancellation reason
        filled_before_cancel: Quantity filled before cancel
        remaining_qty: Quantity remaining (not filled)
        age_seconds: Age of the order when cancelled
    """
    symbol: str
    exchange_order_id: str
    reason: str  # "manual_cancel" | "timeout" | "replaced" | "insufficient_margin"
    filled_before_cancel: float = 0.0
    remaining_qty: float = 0.0
    age_seconds: Optional[float] = None


class LedgerEntry(BaseModel):
    """
    Double-entry ledger transaction.

    Fields:
        timestamp: Transaction timestamp
        transaction_id: Unique transaction ID
        account: Account name (e.g., "asset:BTC/USDT", "cash:USDT", "fees:trading")
        debit: Debit amount (increases asset/expense, decreases liability)
        credit: Credit amount (increases liability/revenue, decreases asset)
        balance_after: Account balance after this entry
        symbol: Trading symbol (if trade-related)
        side: Trade side (if trade-related)
        qty: Trade quantity (if trade-related)
        price: Trade price (if trade-related)
    """
    timestamp: float
    transaction_id: str
    account: str
    debit: float
    credit: float
    balance_after: float
    symbol: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
