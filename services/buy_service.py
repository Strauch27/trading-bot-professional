from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
from config import (
    FEE_RATE,
    MIN_ORDER_BUFFER,
    BUY_ESCALATION_STEPS,
    PREDICTIVE_BUY_ZONE_BPS,
)
from core.utils import (
    next_client_order_id,
    fetch_ticker_cached,
    quantize_price,
)
from core.logging.loggingx import (
    decision_start, decision_end, report_buy_sizing, log_event, new_decision_id,
)
from .sizing_service import SizingService
from core.utils import compute_avg_fill_and_fees


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
    """
    def __init__(self, exchange, sizing: SizingService, portfolio=None):
        self.exchange = exchange
        self.sizing = sizing
        self.portfolio = portfolio

    def _place_limit_ioc(self, symbol: str, qty: float, price: float) -> Optional[Dict[str, Any]]:
        coid = next_client_order_id(symbol, "BUY")
        # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
        px = float(self.exchange.price_to_precision(symbol, price))
        qty = float(self.exchange.amount_to_precision(symbol, qty))

        # Sicherheitsnetz nach der Quantisierung
        if qty <= 0 or px <= 0:
            log_event("BUY_QUANTIZATION_ERROR", level="ERROR", symbol=symbol,
                     message=f"quantized qty/price invalid: qty={qty}, price={px}")
            return None

        try:
            order = self.exchange.create_limit_order(
                symbol, "buy", qty, px, "IOC", coid, False
            )
            return order
        except Exception as e:
            log_event("BUY_ERROR", level="ERROR", symbol=symbol, message=str(e), ctx={"price": px, "qty": qty})
            return None

    def _tiers(self, ask: float, escalation_steps: List[Dict[str, Any]]) -> List[Tuple[float, Dict[str, Any]]]:
        # Convert escalation steps to price tiers with metadata
        tiers = []
        for step in escalation_steps:
            premium_bps = step.get("premium_bps", 0)
            price = ask * (1 + premium_bps / 10_000.0)
            tiers.append((price, step))
        return tiers

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
            # Frischen Ticker für jede Ladder-Stufe holen (verhindert stale prices)
            if idx > 1:  # Erste Stufe nutzt schon den aktuellen Ticker
                fresh_ticker = fetch_ticker_cached(self.exchange, symbol)
                fresh_ask = float(fresh_ticker.get("ask") or ask)  # Fallback auf originalen ask
                if fresh_ask > 0:
                    # Preis-Tier basierend auf frischem Ask neu berechnen
                    premium_bps = step_config.get("premium_bps", 0)
                    px = fresh_ask * (1 + premium_bps / 10_000.0)

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

            order = self._place_limit_ioc(symbol, qty, px)
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
                # Portfolio aktualisieren (WAC + Fee/Unit)
                if self.portfolio and filled_qty > 0:
                    self.portfolio.add_held_asset(symbol, {
                        "amount": filled_qty,
                        "entry_price": float(avg_px or px),
                        "buy_fee_quote_per_unit": buy_fee_per_unit,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                placed_any = True
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