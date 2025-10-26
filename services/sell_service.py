from __future__ import annotations

import time  # Phase 9: For telemetry timestamps
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import (
    ALLOW_MARKET_FALLBACK_TTL,
    EXIT_ESCALATION_BPS,
    EXIT_IOC_TTL_MS,
    EXIT_LADDER_BPS,
    MAX_SLIPPAGE_BPS_EXIT,
    NEVER_MARKET_SELLS,
    TRADE_TTL_MIN,
)
from core.logging.loggingx import exit_filled, exit_placed, log_event
from core.telemetry import get_fill_tracker  # Phase 9
from core.utils import (
    compute_realized_pnl_net_sell,
    fetch_ticker_cached,
    next_client_order_id,
    save_trade_history,
)

from .order_service import OrderService
from .sizing_service import SizingService

# Phase 6: Symbol-scoped locking helper
try:
    from core.portfolio.locks import get_symbol_lock as _get_symbol_lock
    LOCKS_AVAILABLE = True
except ImportError:
    LOCKS_AVAILABLE = False
    @contextmanager
    def _get_symbol_lock(symbol: str):
        """Fallback: no-op context manager if locks not available"""
        yield

class SellService:
    """
    TTL-Enforcement & Exit-Leiter (Limit-IOC, optional Market-Fallback per Policy).
    Nutzt bestehende Utils/Logging – keine neue Logik „erfunden".

    Now uses OrderService for centralized order submission.
    """
    def __init__(
        self,
        exchange,
        portfolio,
        sizing: SizingService,
        *,
        never_market_sells: bool = NEVER_MARKET_SELLS,
        allow_market_fallback_ttl: bool = ALLOW_MARKET_FALLBACK_TTL,
        exit_ladder_bps: List[int] = EXIT_LADDER_BPS,
        exit_escalation_bps: List[int] = EXIT_ESCALATION_BPS,
        exit_ioc_ttl_ms: int = EXIT_IOC_TTL_MS,
        max_slippage_bps_exit: int = MAX_SLIPPAGE_BPS_EXIT,
        on_realized_pnl = None,
        order_service: Optional[OrderService] = None,
    ):
        self.exchange = exchange
        self.portfolio = portfolio
        self.sizing = sizing
        self.never_market_sells = never_market_sells
        self.allow_market_fallback_ttl = allow_market_fallback_ttl
        self.exit_ladder_bps = list(exit_escalation_bps or exit_ladder_bps or [0, 5, 10])
        self.exit_ioc_ttl_ms = exit_ioc_ttl_ms
        self.max_slippage_bps_exit = max_slippage_bps_exit
        self.on_realized_pnl = on_realized_pnl
        self.order_service = order_service or OrderService(exchange)

    def _place_limit_ioc(self, symbol: str, qty: float, price: float) -> Optional[Dict]:
        """
        Place limit IOC sell order using OrderService.

        Args:
            symbol: Trading pair
            qty: Order quantity
            price: Limit price

        Returns:
            Order dict if successful, None otherwise
        """
        coid = next_client_order_id(symbol, "SELL")

        # Phase 9: Start telemetry tracking
        tracker = get_fill_tracker()
        telemetry_id = f"{symbol}_{coid}_{int(time.time()*1000)}"
        tracker.start_order(
            order_id=telemetry_id,
            symbol=symbol,
            side="SELL",
            quantity=qty,
            limit_price=price,
            time_in_force="IOC",
            coid=coid
        )

        # Submit via OrderService (handles quantization, retry, error classification)
        result = self.order_service.submit_limit(
            symbol=symbol,
            side="sell",
            qty=qty,
            price=price,
            coid=coid,
            time_in_force="IOC"
        )

        # Log result
        if not result.success:
            # Phase 9: Record failed order in telemetry
            tracker.record_fill(
                order_id=telemetry_id,
                filled_qty=0.0,
                status="ERROR",
                attempts=result.attempts,
                error_msg=result.error
            )

            log_event(
                "EXIT_ERROR",
                level="ERROR",
                symbol=symbol,
                message=result.error,
                ctx={
                    "price": price,
                    "qty": qty,
                    "error_type": result.error_type.value if result.error_type else "unknown",
                    "attempts": result.attempts
                }
            )
            return None

        # Log retry if multiple attempts
        if result.attempts > 1:
            log_event(
                "ORDER_RETRY",
                level="INFO",
                symbol=symbol,
                message=f"Order succeeded after {result.attempts} attempts",
                ctx={"coid": coid, "attempts": result.attempts}
            )

        # Phase 9: Record fill in telemetry
        order = result.raw_order
        if order:
            status = (order.get("status") or "").upper()
            filled_qty = float(order.get("filled") or 0.0)
            avg_price = float(order.get("average") or price)

            tracker.record_fill(
                order_id=telemetry_id,
                filled_qty=filled_qty,
                avg_fill_price=avg_price if filled_qty > 0 else None,
                status=status,
                exchange_order_id=order.get("id"),
                attempts=result.attempts
            )

        # Return raw order for backwards compatibility
        return result.raw_order

    def _market_ioc(self, symbol: str, qty: float) -> Optional[Dict]:
        """
        Place market IOC sell order using OrderService.

        Args:
            symbol: Trading pair
            qty: Order quantity

        Returns:
            Order dict if successful, None otherwise
        """
        if self.never_market_sells:
            return None

        coid = next_client_order_id(symbol, "SELL")

        # Phase 9: Start telemetry tracking (market order)
        tracker = get_fill_tracker()
        telemetry_id = f"{symbol}_{coid}_{int(time.time()*1000)}"
        tracker.start_order(
            order_id=telemetry_id,
            symbol=symbol,
            side="SELL",
            quantity=qty,
            limit_price=None,  # Market order has no limit price
            time_in_force="IOC",
            coid=coid
        )

        # Submit via OrderService (handles quantization, retry, error classification)
        result = self.order_service.submit_market(
            symbol=symbol,
            side="sell",
            qty=qty,
            coid=coid,
            time_in_force="IOC"
        )

        # Log result
        if not result.success:
            # Phase 9: Record failed order in telemetry
            tracker.record_fill(
                order_id=telemetry_id,
                filled_qty=0.0,
                status="ERROR",
                attempts=result.attempts,
                error_msg=result.error
            )

            log_event(
                "EXIT_ERROR",
                level="ERROR",
                symbol=symbol,
                message=result.error,
                ctx={
                    "qty": qty,
                    "error_type": result.error_type.value if result.error_type else "unknown",
                    "attempts": result.attempts
                }
            )
            return None

        # Log retry if multiple attempts
        if result.attempts > 1:
            log_event(
                "ORDER_RETRY",
                level="INFO",
                symbol=symbol,
                message=f"Order succeeded after {result.attempts} attempts",
                ctx={"coid": coid, "attempts": result.attempts}
            )

        # Phase 9: Record fill in telemetry
        order = result.raw_order
        if order:
            status = (order.get("status") or "").upper()
            filled_qty = float(order.get("filled") or 0.0)
            avg_price = float(order.get("average") or 0.0)

            tracker.record_fill(
                order_id=telemetry_id,
                filled_qty=filled_qty,
                avg_fill_price=avg_price if filled_qty > 0 and avg_price > 0 else None,
                status=status,
                exchange_order_id=order.get("id"),
                attempts=result.attempts
            )

        # Return raw order for backwards compatibility
        return result.raw_order

    # ersetzt durch compute_realized_pnl_net_sell (siehe Exit-Filled-Block)

    def enforce_ttl(self, now_utc: datetime) -> None:
        """
        Iteriert über gehaltene Assets; wenn TTL überschritten → Exit-Leiter (Limit-IOC),
        optional Market-Fallback gem. Policy.

        Phase 5: Uses first_fill_ts for accurate TTL calculation.
        """
        import time

        for symbol, data in list(self.portfolio.held_assets.items()):
            # Phase 5: Prefer first_fill_ts over timestamp for TTL
            first_fill_ts = data.get("first_fill_ts")
            if first_fill_ts:
                # Use first_fill_ts (Unix timestamp)
                try:
                    age_min = (time.time() - float(first_fill_ts)) / 60.0
                except (ValueError, TypeError):
                    # Fallback to timestamp if first_fill_ts is invalid
                    ts = data.get("timestamp")
                    if not ts:
                        continue
                    try:
                        open_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        age_min = (now_utc - open_time).total_seconds() / 60.0
                    except Exception:
                        continue
            else:
                # Backward compatibility: use timestamp
                ts = data.get("timestamp")
                if not ts:
                    continue
                try:
                    open_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    age_min = (now_utc - open_time).total_seconds() / 60.0
                except Exception:
                    continue

            if age_min < TRADE_TTL_MIN:
                continue

            qty = float(data.get("amount") or 0.0)
            if qty <= 0:
                continue

            ticker = fetch_ticker_cached(self.exchange, symbol, ttl_s=2.0)
            bid = float(ticker.get("bid") or 0.0)
            if bid <= 0:
                log_event("EXIT_SKIP", symbol=symbol, message="No valid bid for TTL exit", ctx={"age_min": age_min})
                continue

            entry_price = float(data.get("entry_price") or data.get("buy_price") or 0.0)
            filled_any = False

            # Leiter: mehrere BPS-Schritte unter Bid
            for bps in self.exit_ladder_bps:
                target_price = bid * (1 - bps / 10_000.0)
                q_adj, ok = self.sizing.validate_sell_qty(symbol, qty, target_price)
                if not ok:
                    log_event("EXIT_BELOW_MIN_NOTIONAL", symbol=symbol, ctx={"qty": q_adj, "price": target_price})
                    break

                order = self._place_limit_ioc(symbol, q_adj, target_price)
                if not order:
                    continue

                status = (order.get("status") or "").upper()
                if status in ("FILLED", "CLOSED"):
                    filled_any = True
                    # Trades laden
                    trades = order.get("trades")
                    if trades is None and hasattr(self.exchange, "fetch_my_trades"):
                        try:
                            trades = self.exchange.fetch_my_trades(symbol, params={"orderId": order.get("id")})
                        except Exception:
                            trades = []
                    # Portfolio-Daten: Buy-Fee/Unit & Entry WAC
                    pa = self.portfolio.held_assets.get(symbol, {})
                    buy_fee_per_unit = float(pa.get("buy_fee_quote_per_unit") or 0.0)
                    pnl_ctx = compute_realized_pnl_net_sell(symbol, order, trades, entry_price, buy_fee_per_unit)
                    exit_filled(symbol, "timeout", pnl=pnl_ctx.get("pnl_net_quote", 0.0), ctx={**pnl_ctx, "age_min": age_min, "bps": bps})
                    # Trade-History ergänzen (optional: erweitere mit decision_id/coid, falls hier verfügbar)
                    try:
                        save_trade_history({
                            "timestamp_utc": datetime.now(timezone.utc).isoformat() + "Z",
                            "symbol": symbol,
                            "side": "SELL",
                            "quantity": pnl_ctx.get("qty", 0.0),
                            "avg_exit_price": pnl_ctx.get("avg_exit_price", 0.0),
                            "entry_avg_price": pnl_ctx.get("entry_avg_price", 0.0),
                            "proceeds_quote": pnl_ctx.get("proceeds_quote", 0.0),
                            "sell_fees_quote": pnl_ctx.get("sell_fees_quote", 0.0),
                            "buy_fees_alloc_quote": pnl_ctx.get("buy_fees_alloc_quote", 0.0),
                            "cost_basis_quote": pnl_ctx.get("cost_basis_quote", 0.0),
                            "pnl_net_quote": pnl_ctx.get("pnl_net_quote", 0.0),
                            "order_id": order.get("id"),
                            "client_order_id": (order.get("clientOrderId") or order.get("client_order_id")),
                            "reason": "timeout",
                        })
                    except Exception:
                        pass
                    # Phase 6: Symbol-scoped lock for thread-safe position removal
                    with _get_symbol_lock(symbol):
                        self.portfolio.remove_held_asset(symbol)
                    if self.on_realized_pnl:
                        try:
                            self.on_realized_pnl(pnl_ctx.get("pnl_net_quote", 0.0), (pnl_ctx.get("pnl_net_quote", 0.0) or 0.0) > 0)
                        except Exception:
                            pass
                    break
                else:
                    exit_placed(symbol, "timeout", ctx={"age_min": age_min, "bps": bps})

            # Optionaler Market-Fallback, wenn Policy es erlaubt
            if (not filled_any) and self.allow_market_fallback_ttl and (not self.never_market_sells):
                q_adj, ok = self.sizing.validate_sell_qty(symbol, qty, bid * 0.99)
                if ok:
                    order = self._market_ioc(symbol, q_adj)
                    if order:
                        status = (order.get("status") or "").upper()
                        if status in ("FILLED", "CLOSED"):
                            trades = order.get("trades")
                            if trades is None and hasattr(self.exchange, "fetch_my_trades"):
                                try:
                                    trades = self.exchange.fetch_my_trades(symbol, params={"orderId": order.get("id")})
                                except Exception:
                                    trades = []
                            pa = self.portfolio.held_assets.get(symbol, {})
                            buy_fee_per_unit = float(pa.get("buy_fee_quote_per_unit") or 0.0)
                            pnl_ctx = compute_realized_pnl_net_sell(symbol, order, trades, entry_price, buy_fee_per_unit)
                            exit_filled(symbol, "timeout_market", pnl=pnl_ctx.get("pnl_net_quote", 0.0), ctx={**pnl_ctx, "age_min": age_min})
                            try:
                                save_trade_history({
                                    "timestamp_utc": datetime.now(timezone.utc).isoformat() + "Z",
                                    "symbol": symbol,
                                    "side": "SELL",
                                    "quantity": pnl_ctx.get("qty", 0.0),
                                    "avg_exit_price": pnl_ctx.get("avg_exit_price", 0.0),
                                    "entry_avg_price": pnl_ctx.get("entry_avg_price", 0.0),
                                    "proceeds_quote": pnl_ctx.get("proceeds_quote", 0.0),
                                    "sell_fees_quote": pnl_ctx.get("sell_fees_quote", 0.0),
                                    "buy_fees_alloc_quote": pnl_ctx.get("buy_fees_alloc_quote", 0.0),
                                    "cost_basis_quote": pnl_ctx.get("cost_basis_quote", 0.0),
                                    "pnl_net_quote": pnl_ctx.get("pnl_net_quote", 0.0),
                                    "order_id": order.get("id"),
                                    "client_order_id": (order.get("clientOrderId") or order.get("client_order_id")),
                                    "reason": "timeout_market",
                                })
                            except Exception:
                                pass
                            # Phase 6: Symbol-scoped lock for thread-safe position removal
                            with _get_symbol_lock(symbol):
                                self.portfolio.remove_held_asset(symbol)
                            if self.on_realized_pnl:
                                try:
                                    self.on_realized_pnl(pnl_ctx.get("pnl_net_quote", 0.0), (pnl_ctx.get("pnl_net_quote", 0.0) or 0.0) > 0)
                                except Exception:
                                    pass
                        else:
                            log_event("EXIT_MARKET_PLACED", symbol=symbol, ctx={"age_min": age_min})
