#!/usr/bin/env python3
"""
Fill Telemetry - Phase 9

Tracks order fill metrics for performance analysis and optimization:
- Fill rates (full/partial/no-fill)
- Order placement attempts and success rates
- Latency metrics (order-to-fill time)
- Slippage analysis (expected vs actual fill prices)
- Fee analysis
- IOC vs GTC performance comparison

Usage:
    from core.telemetry import get_fill_tracker

    tracker = get_fill_tracker()

    # Start tracking order
    order_id = tracker.start_order(symbol, side, qty, limit_price)

    # Record fill result
    tracker.record_fill(order_id, filled_qty, avg_price, fees)

    # Get metrics
    stats = tracker.get_statistics()
"""

import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from collections import defaultdict, deque
from enum import Enum
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class FillStatus(Enum):
    """Order fill status"""
    PENDING = "pending"
    FULL_FILL = "full_fill"
    PARTIAL_FILL = "partial_fill"
    NO_FILL = "no_fill"
    CANCELED = "canceled"
    REJECTED = "rejected"
    ERROR = "error"


class OrderSide(Enum):
    """Order side"""
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderTelemetry:
    """Telemetry data for a single order"""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    limit_price: Optional[float]
    time_in_force: str

    # Timing
    submit_time: float  # Unix timestamp
    fill_time: Optional[float] = None
    latency_ms: Optional[float] = None

    # Fill results
    status: FillStatus = FillStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    fill_rate: float = 0.0  # filled_qty / quantity

    # Slippage (only for limit orders)
    expected_price: Optional[float] = None
    slippage_bps: Optional[float] = None

    # Fees
    fees_quote: float = 0.0
    fee_rate_bps: Optional[float] = None

    # Metadata
    decision_id: Optional[str] = None
    coid: Optional[str] = None
    exchange_order_id: Optional[str] = None
    attempts: int = 1
    error_msg: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class FillTracker:
    """
    Tracks order fill metrics for performance analysis.

    Features:
    - Real-time fill rate tracking
    - Latency distribution analysis
    - Slippage statistics
    - Fee analysis
    - Symbol-level and global statistics
    """

    def __init__(self, max_history: int = 10000, export_enabled: bool = False, export_dir: Optional[Path] = None):
        """
        Initialize fill tracker.

        Args:
            max_history: Maximum number of orders to keep in memory
            export_enabled: Enable JSONL export of telemetry
            export_dir: Directory for telemetry exports
        """
        self.max_history = max_history
        self.export_enabled = export_enabled
        self.export_dir = export_dir

        # Order tracking
        self._orders: Dict[str, OrderTelemetry] = {}
        self._history: deque = deque(maxlen=max_history)

        # Statistics cache
        self._stats_cache: Dict[str, Any] = {}
        self._stats_cache_time: float = 0
        self._stats_cache_ttl: float = 5.0  # Cache stats for 5 seconds

        # Export file
        self._export_file = None
        if export_enabled and export_dir:
            export_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self._export_file = export_dir / f"fill_telemetry_{timestamp}.jsonl"
            logger.info(f"Fill telemetry export enabled: {self._export_file}")

    def start_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: Optional[float] = None,
        time_in_force: str = "IOC",
        decision_id: Optional[str] = None,
        coid: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> OrderTelemetry:
        """
        Start tracking an order.

        Args:
            order_id: Unique order ID
            symbol: Trading pair
            side: BUY or SELL
            quantity: Order quantity
            limit_price: Limit price (if applicable)
            time_in_force: IOC, GTC, etc.
            decision_id: Decision ID for correlation
            coid: Client order ID
            metadata: Additional metadata

        Returns:
            OrderTelemetry instance
        """
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL

        telemetry = OrderTelemetry(
            order_id=order_id,
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            limit_price=limit_price,
            time_in_force=time_in_force,
            submit_time=time.time(),
            expected_price=limit_price,  # For slippage calculation
            decision_id=decision_id,
            coid=coid,
            metadata=metadata or {}
        )

        self._orders[order_id] = telemetry
        return telemetry

    def record_fill(
        self,
        order_id: str,
        filled_qty: float,
        avg_fill_price: Optional[float] = None,
        fees_quote: float = 0.0,
        status: str = "FILLED",
        exchange_order_id: Optional[str] = None,
        attempts: int = 1,
        error_msg: Optional[str] = None
    ) -> Optional[OrderTelemetry]:
        """
        Record fill result for an order.

        Args:
            order_id: Order ID
            filled_qty: Filled quantity
            avg_fill_price: Average fill price
            fees_quote: Total fees in quote currency
            status: Order status (FILLED, PARTIALLY_FILLED, CANCELED, etc.)
            exchange_order_id: Exchange's order ID
            attempts: Number of attempts to place order
            error_msg: Error message if failed

        Returns:
            OrderTelemetry instance or None if not found
        """
        telemetry = self._orders.get(order_id)
        if not telemetry:
            logger.warning(f"Order {order_id} not found in telemetry tracker")
            return None

        # Update timing
        telemetry.fill_time = time.time()
        telemetry.latency_ms = (telemetry.fill_time - telemetry.submit_time) * 1000

        # Update fill results
        telemetry.filled_qty = filled_qty
        telemetry.fill_rate = (filled_qty / telemetry.quantity) if telemetry.quantity > 0 else 0.0
        telemetry.avg_fill_price = avg_fill_price
        telemetry.fees_quote = fees_quote
        telemetry.exchange_order_id = exchange_order_id
        telemetry.attempts = attempts
        telemetry.error_msg = error_msg

        # Determine status
        if error_msg:
            telemetry.status = FillStatus.ERROR
        elif status.upper() in ("FILLED", "CLOSED"):
            if telemetry.fill_rate >= 0.999:  # Account for rounding
                telemetry.status = FillStatus.FULL_FILL
            else:
                telemetry.status = FillStatus.PARTIAL_FILL
        elif status.upper() == "CANCELED":
            telemetry.status = FillStatus.CANCELED
        elif status.upper() == "REJECTED":
            telemetry.status = FillStatus.REJECTED
        else:
            telemetry.status = FillStatus.NO_FILL

        # Calculate slippage (if limit order and filled)
        if telemetry.expected_price and avg_fill_price and filled_qty > 0:
            slippage = (avg_fill_price - telemetry.expected_price) / telemetry.expected_price
            telemetry.slippage_bps = slippage * 10000

            # Adjust sign for sell orders (negative slippage = worse for sells)
            if telemetry.side == OrderSide.SELL:
                telemetry.slippage_bps = -telemetry.slippage_bps

        # Calculate fee rate
        if avg_fill_price and filled_qty > 0:
            notional = avg_fill_price * filled_qty
            telemetry.fee_rate_bps = (fees_quote / notional * 10000) if notional > 0 else 0.0

        # Move to history
        self._history.append(telemetry)
        del self._orders[order_id]

        # Export to JSONL
        if self.export_enabled and self._export_file:
            try:
                with open(self._export_file, 'a') as f:
                    record = asdict(telemetry)
                    record['side'] = record['side']['value'] if isinstance(record['side'], dict) else str(telemetry.side.value)
                    record['status'] = record['status']['value'] if isinstance(record['status'], dict) else str(telemetry.status.value)
                    f.write(json.dumps(record) + '\n')
            except Exception as e:
                logger.error(f"Failed to export telemetry for {order_id}: {e}")

        # Invalidate stats cache
        self._stats_cache_time = 0

        return telemetry

    def get_statistics(self, symbol: Optional[str] = None, window_seconds: Optional[float] = None) -> Dict[str, Any]:
        """
        Get fill statistics.

        Args:
            symbol: Optional symbol filter
            window_seconds: Optional time window (recent N seconds)

        Returns:
            Statistics dictionary
        """
        # Check cache
        cache_key = f"{symbol}:{window_seconds}"
        if cache_key in self._stats_cache and (time.time() - self._stats_cache_time) < self._stats_cache_ttl:
            return self._stats_cache[cache_key]

        # Filter orders
        orders = list(self._history)

        if symbol:
            orders = [o for o in orders if o.symbol == symbol]

        if window_seconds:
            cutoff_time = time.time() - window_seconds
            orders = [o for o in orders if o.submit_time >= cutoff_time]

        if not orders:
            return {
                "total_orders": 0,
                "symbol": symbol,
                "window_seconds": window_seconds
            }

        # Calculate statistics
        total_orders = len(orders)
        full_fills = [o for o in orders if o.status == FillStatus.FULL_FILL]
        partial_fills = [o for o in orders if o.status == FillStatus.PARTIAL_FILL]
        no_fills = [o for o in orders if o.status == FillStatus.NO_FILL]
        errors = [o for o in orders if o.status == FillStatus.ERROR]

        # Fill rates
        full_fill_rate = len(full_fills) / total_orders if total_orders > 0 else 0.0
        partial_fill_rate = len(partial_fills) / total_orders if total_orders > 0 else 0.0
        no_fill_rate = len(no_fills) / total_orders if total_orders > 0 else 0.0
        error_rate = len(errors) / total_orders if total_orders > 0 else 0.0

        # Latency statistics
        latencies = [o.latency_ms for o in orders if o.latency_ms is not None]
        latency_stats = {}
        if latencies:
            latency_stats = {
                "mean_ms": sum(latencies) / len(latencies),
                "min_ms": min(latencies),
                "max_ms": max(latencies),
                "p50_ms": self._percentile(latencies, 0.5),
                "p95_ms": self._percentile(latencies, 0.95),
                "p99_ms": self._percentile(latencies, 0.99)
            }

        # Slippage statistics (only for filled orders)
        filled_orders = [o for o in orders if o.status in (FillStatus.FULL_FILL, FillStatus.PARTIAL_FILL) and o.slippage_bps is not None]
        slippage_stats = {}
        if filled_orders:
            slippages = [o.slippage_bps for o in filled_orders]
            slippage_stats = {
                "mean_bps": sum(slippages) / len(slippages),
                "min_bps": min(slippages),
                "max_bps": max(slippages),
                "p50_bps": self._percentile(slippages, 0.5),
                "p95_bps": self._percentile(slippages, 0.95)
            }

        # Fee statistics
        fee_rates = [o.fee_rate_bps for o in orders if o.fee_rate_bps is not None]
        fee_stats = {}
        if fee_rates:
            fee_stats = {
                "mean_bps": sum(fee_rates) / len(fee_rates),
                "min_bps": min(fee_rates),
                "max_bps": max(fee_rates)
            }

        # Side breakdown
        buy_orders = [o for o in orders if o.side == OrderSide.BUY]
        sell_orders = [o for o in orders if o.side == OrderSide.SELL]

        stats = {
            "total_orders": total_orders,
            "symbol": symbol,
            "window_seconds": window_seconds,
            "fill_rates": {
                "full_fill": full_fill_rate,
                "partial_fill": partial_fill_rate,
                "no_fill": no_fill_rate,
                "error": error_rate
            },
            "counts": {
                "full_fills": len(full_fills),
                "partial_fills": len(partial_fills),
                "no_fills": len(no_fills),
                "errors": len(errors)
            },
            "latency": latency_stats,
            "slippage": slippage_stats,
            "fees": fee_stats,
            "side_breakdown": {
                "buy_count": len(buy_orders),
                "sell_count": len(sell_orders),
                "buy_full_fill_rate": len([o for o in buy_orders if o.status == FillStatus.FULL_FILL]) / len(buy_orders) if buy_orders else 0.0,
                "sell_full_fill_rate": len([o for o in sell_orders if o.status == FillStatus.FULL_FILL]) / len(sell_orders) if sell_orders else 0.0
            }
        }

        # Cache result
        self._stats_cache[cache_key] = stats
        self._stats_cache_time = time.time()

        return stats

    def get_symbol_statistics(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics broken down by symbol."""
        symbols = set(o.symbol for o in self._history)
        return {symbol: self.get_statistics(symbol=symbol) for symbol in symbols}

    def get_recent_orders(self, limit: int = 100, symbol: Optional[str] = None) -> List[OrderTelemetry]:
        """
        Get recent orders.

        Args:
            limit: Maximum number of orders to return
            symbol: Optional symbol filter

        Returns:
            List of OrderTelemetry instances
        """
        orders = list(self._history)

        if symbol:
            orders = [o for o in orders if o.symbol == symbol]

        return orders[-limit:]

    def clear_history(self):
        """Clear order history (keeps pending orders)."""
        self._history.clear()
        self._stats_cache.clear()
        self._stats_cache_time = 0
        logger.info("Fill telemetry history cleared")

    @staticmethod
    def _percentile(data: List[float], p: float) -> float:
        """Calculate percentile."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * p)
        return sorted_data[min(index, len(sorted_data) - 1)]


# Global singleton
_fill_tracker: Optional[FillTracker] = None


def get_fill_tracker(
    max_history: int = 10000,
    export_enabled: bool = False,
    export_dir: Optional[Path] = None
) -> FillTracker:
    """
    Get global FillTracker singleton.

    Args:
        max_history: Maximum history size (only used on first call)
        export_enabled: Enable JSONL export (only used on first call)
        export_dir: Export directory (only used on first call)

    Returns:
        FillTracker instance
    """
    global _fill_tracker
    if _fill_tracker is None:
        _fill_tracker = FillTracker(
            max_history=max_history,
            export_enabled=export_enabled,
            export_dir=export_dir
        )
        logger.info("Fill tracker initialized (Phase 9)")
    return _fill_tracker


def reset_fill_tracker():
    """Reset global fill tracker (for testing)."""
    global _fill_tracker
    _fill_tracker = None
