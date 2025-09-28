from __future__ import annotations
from typing import Optional, List, Dict
from datetime import datetime, timezone
from config import (
    TRADE_TTL_MIN, NEVER_MARKET_SELLS, ALLOW_MARKET_FALLBACK_TTL,
    EXIT_ESCALATION_BPS, EXIT_LADDER_BPS, EXIT_IOC_TTL_MS, MAX_SLIPPAGE_BPS_EXIT
)
from utils import (
    next_client_order_id, fetch_ticker_cached, quantize_price,
    compute_avg_fill_and_fees, compute_realized_pnl_net_sell, save_trade_history
)
from loggingx import log_event, exit_placed, exit_filled
from .sizing_service import SizingService

class SellService:
    """
    TTL-Enforcement & Exit-Leiter (Limit-IOC, optional Market-Fallback per Policy).
    Nutzt bestehende Utils/Logging – keine neue Logik „erfunden".
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

    def _place_limit_ioc(self, symbol: str, qty: float, price: float) -> Optional[Dict]:
        coid = next_client_order_id(symbol, "SELL")
        # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
        px = float(self.exchange.price_to_precision(symbol, price))
        qty = float(self.exchange.amount_to_precision(symbol, qty))

        # Sicherheitsnetz nach der Quantisierung
        if qty <= 0 or px <= 0:
            log_event("SELL_QUANTIZATION_ERROR", level="ERROR", symbol=symbol,
                     message=f"quantized qty/price invalid: qty={qty}, price={px}")
            return None

        try:
            order = self.exchange.create_limit_order(
                symbol, "sell", qty, px, "IOC", coid, False
            )
            return order
        except Exception as e:
            log_event("EXIT_ERROR", level="ERROR", symbol=symbol, message=str(e), ctx={"price": px, "qty": qty})
            return None

    def _market_ioc(self, symbol: str, qty: float) -> Optional[Dict]:
        if self.never_market_sells:
            return None
        coid = next_client_order_id(symbol, "SELL")
        # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
        qty = float(self.exchange.amount_to_precision(symbol, qty))

        # Sicherheitsnetz nach der Quantisierung
        if qty <= 0:
            log_event("SELL_MARKET_QUANTIZATION_ERROR", level="ERROR", symbol=symbol,
                     message=f"quantized qty invalid: qty={qty}")
            return None

        try:
            order = self.exchange.create_market_order(
                symbol, "sell", qty, "IOC", coid
            )
            return order
        except Exception as e:
            log_event("EXIT_ERROR", level="ERROR", symbol=symbol, message=str(e), ctx={"qty": qty})
            return None

    # ersetzt durch compute_realized_pnl_net_sell (siehe Exit-Filled-Block)

    def enforce_ttl(self, now_utc: datetime) -> None:
        """
        Iteriert über gehaltene Assets; wenn TTL überschritten → Exit-Leiter (Limit-IOC),
        optional Market-Fallback gem. Policy.
        """
        for symbol, data in list(self.portfolio.held_assets.items()):
            ts = data.get("timestamp")
            if not ts:
                continue
            try:
                open_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except Exception:
                continue

            age_min = (now_utc - open_time).total_seconds() / 60.0
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
                            self.portfolio.remove_held_asset(symbol)
                            if self.on_realized_pnl:
                                try:
                                    self.on_realized_pnl(pnl_ctx.get("pnl_net_quote", 0.0), (pnl_ctx.get("pnl_net_quote", 0.0) or 0.0) > 0)
                                except Exception:
                                    pass
                        else:
                            log_event("EXIT_MARKET_PLACED", symbol=symbol, ctx={"age_min": age_min})