from __future__ import annotations

import time  # Phase 9: For telemetry timestamps
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config as cfg  # Phase 8: Import config module for evaluate_all_entry_guards
from config import (
    BUY_ESCALATION_STEPS,
    DEPTH_MIN_NOTIONAL_USD,  # Phase 7
    MAX_SLIPPAGE_BPS_ENTRY,  # Phase 4
    MAX_SPREAD_BPS_ENTRY,  # Phase 7
    PREDICTIVE_BUY_ZONE_BPS,
    PREDICTIVE_BUY_ZONE_CAP_BPS,  # Phase 7
)
from core.logging.loggingx import (
    decision_end,
    decision_start,
    log_event,
    new_decision_id,
    report_buy_sizing,
)
from core.risk_limits import evaluate_all_entry_guards  # Phase 8
from core.telemetry import get_fill_tracker  # Phase 9
from core.utils import (
    compute_avg_fill_and_fees,
    fetch_ticker_cached,
    next_client_order_id,
)

from .order_service import OrderService
from .sizing_service import SizingService


@dataclass
class BuyPlan:
    symbol: str
    quote_usdt: float
    reason: str
    escalation_steps: List[Dict[str, Any]] = field(default_factory=lambda: BUY_ESCALATION_STEPS.copy())
    premium_bps: int = PREDICTIVE_BUY_ZONE_BPS  # Aufschlag über Ask in bp


class BuyService:
    """
    Führt die 2-stufige (konfigurierbar) Limit-IOC Buy-Leiter aus.
    - Schritt 1: nahe Ask, kleiner Premium (bps)
    - Schritt 2..N: progressiv mehr Premium
    Sizing läuft über SizingService, Logging via loggingx.

    Now uses OrderService for centralized order submission.
    """
    def __init__(self, exchange, sizing: SizingService, portfolio=None, order_service: Optional[OrderService] = None):
        self.exchange = exchange
        self.sizing = sizing
        self.portfolio = portfolio
        self.order_service = order_service or OrderService(exchange)

    def _place_limit_ioc(self, symbol: str, qty: float, price: float, decision_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Place limit IOC order using OrderService.

        Args:
            symbol: Trading pair
            qty: Order quantity
            price: Limit price
            decision_id: Optional decision ID for telemetry correlation

        Returns:
            Order dict if successful, None otherwise
        """
        coid = next_client_order_id(symbol, "BUY")

        # Phase 9: Start telemetry tracking
        tracker = get_fill_tracker()
        telemetry_id = f"{symbol}_{coid}_{int(time.time()*1000)}"
        tracker.start_order(
            order_id=telemetry_id,
            symbol=symbol,
            side="BUY",
            quantity=qty,
            limit_price=price,
            time_in_force="IOC",
            decision_id=decision_id,
            coid=coid
        )

        # Submit via OrderService (handles quantization, retry, error classification)
        result = self.order_service.submit_limit(
            symbol=symbol,
            side="buy",
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
                "BUY_ERROR",
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

        # Phase 9: Record fill in telemetry (basic recording here, detailed in place() after fee calc)
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

    def _tiers(self, ask: float, escalation_steps: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
        # Convert escalation steps to price tiers with metadata
        tiers = []
        for step in escalation_steps:
            premium_bps = step.get("premium_bps", 0)
            price = ask * (1 + premium_bps / 10_000.0)
            tiers.append((price, step))
        return tiers

    def _check_entry_slippage(
        self,
        symbol: str,
        avg_fill_price: float,
        ref_price: float,
        premium_bps: int
    ) -> Tuple[bool, float]:
        """
        Check if entry slippage exceeds threshold (Phase 4).

        Args:
            symbol: Trading pair
            avg_fill_price: Average fill price
            ref_price: Reference price (ask at order time)
            premium_bps: Expected premium in bps

        Returns:
            (breach_occurred, actual_slippage_bps)
        """
        # Calculate expected price with premium
        expected_price = ref_price * (1 + premium_bps / 10_000.0)

        # Calculate actual slippage in bps
        # Positive = paid more than expected, negative = paid less
        slippage_bps = ((avg_fill_price - expected_price) / expected_price) * 10_000.0

        # Check breach
        breach = slippage_bps > MAX_SLIPPAGE_BPS_ENTRY

        if breach:
            log_event(
                "ENTRY_SLIPPAGE_BREACH",
                level="WARNING",
                symbol=symbol,
                message=f"Entry slippage exceeded threshold: {slippage_bps:.1f} bps > {MAX_SLIPPAGE_BPS_ENTRY} bps",
                ctx={
                    "avg_fill_price": float(avg_fill_price),
                    "ref_price": float(ref_price),
                    "expected_price": float(expected_price),
                    "premium_bps": premium_bps,
                    "slippage_bps": float(slippage_bps),
                    "threshold_bps": MAX_SLIPPAGE_BPS_ENTRY
                }
            )
        else:
            log_event(
                "ENTRY_SLIPPAGE_OK",
                level="DEBUG",
                symbol=symbol,
                message=f"Entry slippage within threshold: {slippage_bps:.1f} bps",
                ctx={
                    "slippage_bps": float(slippage_bps),
                    "threshold_bps": MAX_SLIPPAGE_BPS_ENTRY
                }
            )

        return breach, slippage_bps

    def _check_market_quality_guards(
        self,
        symbol: str,
        ask: float,
        premium_bps: int
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Check spread and depth guards before buy (Phase 7).

        Args:
            symbol: Trading pair
            ask: Current ask price
            premium_bps: Intended premium in bps

        Returns:
            (should_skip, skip_reason, guard_ctx)
        """
        guard_ctx: Dict[str, Any] = {}

        # Check if guards are enabled
        enable_spread_guard = getattr(cfg, 'ENABLE_SPREAD_GUARD_ENTRY', False)
        enable_depth_guard = getattr(cfg, 'ENABLE_DEPTH_GUARD_ENTRY', False)

        # Check if exchange has get_spread/get_top5_depth (MarketDataProvider)
        has_spread = hasattr(self.exchange, 'get_spread')
        has_depth = hasattr(self.exchange, 'get_top5_depth')

        # Phase 7.1: Spread guard (if enabled)
        if enable_spread_guard and has_spread:
            try:
                spread_bps = self.exchange.get_spread(symbol)
                if spread_bps is not None:
                    guard_ctx["spread_bps"] = float(spread_bps)
                    if spread_bps > MAX_SPREAD_BPS_ENTRY:
                        log_event(
                            "BUY_SKIP_WIDE_SPREAD",
                            level="WARNING",
                            symbol=symbol,
                            message=f"Spread too wide: {spread_bps:.1f} bps > {MAX_SPREAD_BPS_ENTRY} bps",
                            ctx=guard_ctx
                        )
                        return True, "WIDE_SPREAD", guard_ctx
            except Exception as e:
                log_event(
                    "SPREAD_CHECK_ERROR",
                    level="ERROR",
                    symbol=symbol,
                    message=f"Error checking spread: {e}",
                    ctx={}
                )

        # Phase 7.2: Depth guard (check ask side liquidity) (if enabled)
        if enable_depth_guard and has_depth:
            try:
                bid_depth, ask_depth = self.exchange.get_top5_depth(symbol, levels=5)
                guard_ctx["bid_depth_usd"] = float(bid_depth)
                guard_ctx["ask_depth_usd"] = float(ask_depth)

                # Check ask-side depth (we're buying, so we care about ask liquidity)
                if ask_depth < DEPTH_MIN_NOTIONAL_USD:
                    log_event(
                        "BUY_SKIP_THIN_DEPTH",
                        level="WARNING",
                        symbol=symbol,
                        message=f"Insufficient ask depth: ${ask_depth:.2f} < ${DEPTH_MIN_NOTIONAL_USD}",
                        ctx=guard_ctx
                    )
                    return True, "THIN_DEPTH", guard_ctx
            except Exception as e:
                log_event(
                    "DEPTH_CHECK_ERROR",
                    level="ERROR",
                    symbol=symbol,
                    message=f"Error checking depth: {e}",
                    ctx={}
                )

        # Phase 7.3: Cap premium at PREDICTIVE_BUY_ZONE_CAP_BPS
        if premium_bps > PREDICTIVE_BUY_ZONE_CAP_BPS:
            guard_ctx["premium_capped"] = True
            guard_ctx["original_premium_bps"] = premium_bps
            guard_ctx["capped_premium_bps"] = PREDICTIVE_BUY_ZONE_CAP_BPS
            log_event(
                "BUY_PREMIUM_CAPPED",
                level="INFO",
                symbol=symbol,
                message=f"Premium capped: {premium_bps} bps → {PREDICTIVE_BUY_ZONE_CAP_BPS} bps",
                ctx=guard_ctx
            )

        return False, "", guard_ctx

    def place(self, plan: BuyPlan) -> Tuple[bool, Dict[str, Any]]:
        """
        Führt die Buy-Leiter aus.
        Rückgabe: (order_placed, context)
        """
        symbol = plan.symbol
        reason = plan.reason
        dec_id = new_decision_id()
        ctx: Dict[str, Any] = {
            "reason": reason,
            "quote_usdt": plan.quote_usdt,
            "steps": len(plan.escalation_steps),
            "premium_bps": plan.premium_bps
        }

        # Markt-Snapshot
        ticker = fetch_ticker_cached(self.exchange, symbol)
        ask = float(ticker.get("ask") or 0.0)
        bid = float(ticker.get("bid") or 0.0)
        if ask <= 0:
            log_event("BUY_SKIP", symbol=symbol, message="No valid ask", ctx=ctx)
            return False, {"reason": "NO_ASK"}

        # Phase 8: Consolidated entry guard evaluation (risk limits + market quality)
        # Note: requires self.portfolio to be set for risk limit checks
        if self.portfolio:
            guards_passed, block_reason, guard_ctx = evaluate_all_entry_guards(
                symbol=symbol,
                order_value_usdt=plan.quote_usdt,
                ask=ask,
                portfolio=self.portfolio,
                config=cfg,
                market_data_provider=self.exchange  # Pass exchange (may be MarketDataProvider)
            )
            ctx.update(guard_ctx)

            if not guards_passed:
                log_event("BUY_SKIP", symbol=symbol, message=f"Entry guard blocked: {block_reason}", ctx=ctx)
                return False, {"reason": block_reason, **guard_ctx}
        else:
            # Fallback: if no portfolio, just check market quality guards
            should_skip, skip_reason, guard_ctx = self._check_market_quality_guards(
                symbol=symbol,
                ask=ask,
                premium_bps=plan.premium_bps
            )
            ctx.update(guard_ctx)

            if should_skip:
                log_event("BUY_SKIP", symbol=symbol, message=f"Market quality guard: {skip_reason}", ctx=ctx)
                return False, {"reason": skip_reason, **guard_ctx}

        # Start der Decision – Schema von loggingx: (id, symbol, side, strategy, price, quote, ctx)
        decision_start(
            dec_id,
            symbol=symbol,
            side="BUY",
            strategy="BUY_ESC",
            price=float(ask),
            quote=float(plan.quote_usdt),
            ctx=ctx,
        )

        # Erste Zielpreise berechnen (Limit-Stufen über Ask)
        price_tiers = self._tiers(ask, plan.escalation_steps)
        placed_any = False

        for idx, (px, step_config) in enumerate(price_tiers, start=1):
            # Get premium_bps and apply cap (Phase 7)
            premium_bps = step_config.get("premium_bps", 0)
            if premium_bps > PREDICTIVE_BUY_ZONE_CAP_BPS:
                premium_bps = PREDICTIVE_BUY_ZONE_CAP_BPS

            # Frischen Ticker für jede Ladder-Stufe holen (verhindert stale prices)
            if idx > 1:  # Erste Stufe nutzt schon den aktuellen Ticker
                fresh_ticker = fetch_ticker_cached(self.exchange, symbol)
                fresh_ask = float(fresh_ticker.get("ask") or ask)  # Fallback auf originalen ask
                if fresh_ask > 0:
                    # Preis-Tier basierend auf frischem Ask neu berechnen
                    px = fresh_ask * (1 + premium_bps / 10_000.0)
            else:
                # Phase 7: Recalculate first tier price with capped premium
                px = ask * (1 + premium_bps / 10_000.0)

            # Sizing: wie viel können wir uns bei diesem Limit leisten?
            qty, est_cost, min_required, sizing_reason = self.sizing.affordable_buy_qty(symbol, px, plan.quote_usdt)
            report_buy_sizing(symbol, {
                "step": idx,
                "price": float(px),
                "qty": float(qty or 0),
                "est_cost": float(est_cost or 0),
                "min_required": float(min_required or 0),
                "reason": sizing_reason or "OK"
            })

            if sizing_reason:
                # zu wenig Budget / unter MinNotional
                ctx[f"sizing_step_{idx}"] = sizing_reason
                continue

            order = self._place_limit_ioc(symbol, qty, px, decision_id=dec_id)
            if not order:
                continue

            status = (order.get("status") or "").upper()
            filled = status in ("FILLED", "CLOSED")
            ctx[f"step_{idx}_status"] = status
            ctx[f"step_{idx}_premium_bps"] = step_config.get("premium_bps", 0)
            if filled:
                # Trades einlesen (falls nicht im Orderobjekt)
                trades = order.get("trades")
                if trades is None and hasattr(self.exchange, "fetch_my_trades"):
                    try:
                        trades = self.exchange.fetch_my_trades(symbol, params={"orderId": order.get("id")})
                    except Exception:
                        trades = []
                # Buy-Fees in Quote bestimmen & pro Einheit verteilen
                avg_px, filled_qty, _proceeds_q, buy_fees_q = compute_avg_fill_and_fees(order, trades)
                buy_fee_per_unit = (buy_fees_q / filled_qty) if filled_qty > 0 else 0.0

                # Phase 4: Check entry slippage (if enabled)
                slippage_breach = False
                slippage_bps = 0.0
                if getattr(cfg, 'ENABLE_ENTRY_SLIPPAGE_GUARD', False):
                    ref_ask = float(fresh_ask if idx > 1 and 'fresh_ask' in locals() else ask)
                    premium_bps = step_config.get("premium_bps", 0)
                    slippage_breach, slippage_bps = self._check_entry_slippage(
                        symbol=symbol,
                        avg_fill_price=avg_px,
                        ref_price=ref_ask,
                        premium_bps=premium_bps
                    )
                    ctx[f"step_{idx}_slippage_bps"] = float(slippage_bps)
                    ctx[f"step_{idx}_slippage_breach"] = slippage_breach

                # Portfolio aktualisieren (WAC + Fee/Unit)
                # Phase 6: Symbol-scoped lock for thread-safe position updates
                if self.portfolio and filled_qty > 0:
                    try:
                        from core.portfolio.locks import get_symbol_lock
                        with get_symbol_lock(symbol):
                            self.portfolio.add_held_asset(symbol, {
                                "amount": filled_qty,
                                "entry_price": float(avg_px or px),
                                "buy_fee_quote_per_unit": buy_fee_per_unit,
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                    except ImportError:
                        # Fallback without locks if locks module not available
                        self.portfolio.add_held_asset(symbol, {
                            "amount": filled_qty,
                            "entry_price": float(avg_px or px),
                            "buy_fee_quote_per_unit": buy_fee_per_unit,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
                placed_any = True

                # Phase 4: Stop ladder if slippage breach
                if slippage_breach:
                    log_event(
                        "BUY_LADDER_STOPPED",
                        level="WARNING",
                        symbol=symbol,
                        message=f"Stopping buy ladder due to slippage breach at step {idx}",
                        ctx={"step": idx, "slippage_bps": float(slippage_bps)}
                    )
                    ctx["ladder_stopped_reason"] = "SLIPPAGE_BREACH"
                    break

                break

        decision_end(
            dec_id,
            symbol=symbol,
            side="BUY",
            outcome="ORDER_PLACED" if placed_any else "ORDER_FAILED",
            reason=reason,
            ctx={**ctx, "bid": float(bid or 0.0), "ask": float(ask or 0.0)},
        )
        return placed_any, ctx


# ============================================================================
# FSM Parity: Simple Submit+Retry with Quantization
# ============================================================================

def submit_buy(exchange, symbol: str, raw_price: float, raw_amount: float):
    """
    FSM Parity: Submit buy order with quantization and single retry.

    This is a simplified, deterministic buy submission for FSM compatibility.
    Uses new quantization and validation modules.

    Args:
        exchange: CCXT exchange instance
        symbol: Trading symbol
        raw_price: Raw price (before quantization)
        raw_amount: Raw amount (before quantization)

    Returns:
        Order dict if successful, None if failed/aborted

    Flow:
        1. Load filters
        2. Pre-flight validation (quantize + check min_notional)
        3. Submit order
        4. If fails: Hard re-quantization retry (once)
        5. If still fails: Abort

    Example:
        >>> order = submit_buy(exchange, "BTC/USDT", 50000.123, 0.05678)
        >>> order["id"]  # "1234567890"
    """
    from services.exchange_filters import get_filters
    from services.order_validation import preflight
    from services.quantize import q_price, q_amount
    from core.logging.events import emit
    import logging

    logger = logging.getLogger(__name__)

    # 1. Load filters
    f = get_filters(exchange, symbol)

    # 2. Pre-flight validation
    ok, data = preflight(symbol, raw_price, raw_amount, f)
    if not ok:
        emit("buy_aborted", symbol=symbol, detail=data)
        logger.warning(f"[SUBMIT_BUY] {symbol} ABORTED: {data}")
        return None

    pr, am = data["price"], data["amount"]

    # 3. Submit order (first attempt)
    try:
        o = exchange.create_limit_buy_order(symbol, am, pr)
        emit("buy_submit", symbol=symbol, price=pr, amount=am, status="submitted")
        logger.info(f"[SUBMIT_BUY] {symbol} SUCCESS: order_id={o.get('id')}")
        return o
    except Exception as e1:
        logger.warning(f"[SUBMIT_BUY] {symbol} FAILED (attempt 1): {e1}")

        # 4. Hard re-quantization retry (single attempt)
        pr2 = q_price(pr, f["tick_size"])
        am2 = q_amount(am, f["step_size"])

        if pr2 != pr or am2 != am:
            try:
                o2 = exchange.create_limit_buy_order(symbol, am2, pr2)
                emit("buy_submit", symbol=symbol, price=pr2, amount=am2, status="submitted_after_requant")
                logger.info(f"[SUBMIT_BUY] {symbol} SUCCESS (requant): order_id={o2.get('id')}")
                return o2
            except Exception as e2:
                emit("buy_aborted", symbol=symbol, error=str(e2))
                logger.error(f"[SUBMIT_BUY] {symbol} ABORTED (attempt 2): {e2}")
                return None

        # If no re-quantization needed, abort
        emit("buy_aborted", symbol=symbol, error=str(e1))
        logger.error(f"[SUBMIT_BUY] {symbol} ABORTED: {e1}")
        return None
