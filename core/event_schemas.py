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
    """Position updated (increased/decreased) event."""
    symbol: str
    qty_before: float
    qty_after: float
    avg_entry_before: float
    avg_entry_after: float
    notional_change: float
    fee_accum: float


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
    Risk limits evaluation.

    Fields:
        check_type: Type of check ("max_positions", "cap_per_symbol", "daily_dd", etc.)
        passed: Whether check passed
        current_value: Current value being checked
        limit_value: Limit/threshold value
        reason: Explanation if failed
    """
    symbol: str
    check_type: str  # "max_positions" | "cap_per_symbol" | "daily_dd"
    passed: bool
    current_value: Optional[float] = None
    limit_value: Optional[float] = None
    reason: Optional[str] = None


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
