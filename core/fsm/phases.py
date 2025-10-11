"""
Phase Definitions for FSM Trading Bot

Each phase represents a distinct state in the trading lifecycle.
Transitions between phases are explicit and logged.
"""

from enum import Enum


class Phase(str, Enum):
    """
    Trading Bot FSM Phases

    Lifecycle Flow:
    WARMUP → IDLE → ENTRY_EVAL → PLACE_BUY → WAIT_FILL → POSITION
         → EXIT_EVAL → PLACE_SELL → WAIT_SELL_FILL → POST_TRADE → COOLDOWN → IDLE

    Error Flow:
    Any Phase → ERROR → (retry or IDLE)
    """

    # ========== Initialization ==========
    WARMUP = "warmup"
    """
    Initial state after symbol registration.
    Actions: Market data backfill, anchor initialization.
    Next: IDLE
    """

    IDLE = "idle"
    """
    Waiting for buy signal.
    Actions: Monitor price, wait for free slots.
    Next: ENTRY_EVAL (if slots available)
    """

    # ========== Buy Flow ==========
    ENTRY_EVAL = "entry_eval"
    """
    Evaluating entry conditions.
    Actions: Check guards (SMA, spread, volume, sigma, BTC), check drop trigger.
    Next: PLACE_BUY (if signal) or IDLE (if no signal/guards fail)
    """

    PLACE_BUY = "place_buy"
    """
    Placing buy order.
    Actions: Calculate position size, place IOC ladder/GTC order.
    Next: WAIT_FILL (if order placed) or IDLE (if placement fails)
    """

    WAIT_FILL = "wait_fill"
    """
    Waiting for buy order fill.
    Actions: Poll order status, handle timeout.
    Next: POSITION (if filled) or IDLE (if timeout/canceled)
    """

    # ========== Position Management ==========
    POSITION = "position"
    """
    Holding position.
    Actions: Update trailing stops, monitor exit conditions, update unrealized PnL.
    Next: EXIT_EVAL (if exit condition triggered)
    """

    EXIT_EVAL = "exit_eval"
    """
    Evaluating exit conditions.
    Actions: Check TP/SL/trailing/ATR stop/timeout.
    Next: PLACE_SELL (if exit triggered) or POSITION (if no exit)
    """

    # ========== Exit Flow ==========
    PLACE_SELL = "place_sell"
    """
    Placing sell order.
    Actions: Place IOC ladder/depth sweep sell order.
    Next: WAIT_SELL_FILL (if order placed) or EXIT_EVAL (if placement fails - retry)
    """

    WAIT_SELL_FILL = "wait_sell_fill"
    """
    Waiting for sell order fill.
    Actions: Poll order status, handle partial fills, retry with market order.
    Next: POST_TRADE (if filled) or PLACE_SELL (if retry needed)
    """

    # ========== Cleanup ==========
    POST_TRADE = "post_trade"
    """
    Post-trade cleanup.
    Actions: Record PnL, update session digest, remove position, notify Telegram.
    Next: COOLDOWN
    """

    COOLDOWN = "cooldown"
    """
    Symbol cooldown period.
    Actions: Wait for configured cooldown duration (default: 15min).
    Next: IDLE (after cooldown expires)
    """

    # ========== Error Handling ==========
    ERROR = "error"
    """
    Error recovery state.
    Actions: Log error, increment error counter, apply backoff.
    Next: IDLE (after recovery) or stays in ERROR (if too many retries)
    """


# Phase Groups (for filtering/monitoring)
BUY_PHASES = {Phase.ENTRY_EVAL, Phase.PLACE_BUY, Phase.WAIT_FILL}
POSITION_PHASES = {Phase.POSITION, Phase.EXIT_EVAL}
SELL_PHASES = {Phase.PLACE_SELL, Phase.WAIT_SELL_FILL, Phase.POST_TRADE}
ACTIVE_PHASES = BUY_PHASES | POSITION_PHASES | SELL_PHASES
IDLE_PHASES = {Phase.WARMUP, Phase.IDLE, Phase.COOLDOWN}
ERROR_PHASES = {Phase.ERROR}

ALL_PHASES = set(Phase)


def is_active_trading(phase: Phase) -> bool:
    """Returns True if phase is actively trading (not idle/error)."""
    return phase in ACTIVE_PHASES


def is_holding_position(phase: Phase) -> bool:
    """Returns True if phase represents holding a position."""
    return phase in POSITION_PHASES


def is_waiting_for_fill(phase: Phase) -> bool:
    """Returns True if phase is waiting for order fill."""
    return phase in {Phase.WAIT_FILL, Phase.WAIT_SELL_FILL}
