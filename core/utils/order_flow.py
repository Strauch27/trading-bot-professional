# order_flow.py

def on_trade_fill(evt: dict, pnl_tracker, rolling_stats, log_event):
    """
    evt: {event_type:"TRADE_FILL", symbol, side, price, qty, fee_quote?, slippage_bp?, ts?}
    """
    pnl_tracker.on_fill(evt["symbol"], evt["side"], evt["price"], evt["qty"], evt.get("fee_quote",0.0))
    if "slippage_bp" in evt and rolling_stats is not None and hasattr(rolling_stats, "add_fill"):
        rolling_stats.add_fill(evt.get("ts", 0.0), evt["slippage_bp"])
    log_event("TRADE_FILL", **evt)

def on_order_update(evt, pnl_tracker):
    """
    Order-Update-Hook für PnL-Tracking (Legacy).

    Args:
        evt: Order-Update-Event
        pnl_tracker: PnLTracker-Instanz
    """
    if evt.get("event_type") == "TRADE_FILL":
        symbol = evt.get("symbol")
        side = evt.get("side")
        price = evt.get("price")
        qty = evt.get("qty")

        if all([symbol, side, price is not None, qty is not None]):
            pnl_tracker.on_fill(symbol, side, price, qty, evt.get("fee_quote", 0.0))


def process_fill_event(fill_data, pnl_tracker, logger):
    """
    Verarbeite Fill-Event und update PnL.

    Args:
        fill_data: Fill-Daten vom Exchange
        pnl_tracker: PnLTracker-Instanz
        logger: Logger-Instanz
    """
    try:
        symbol = fill_data.get("symbol")
        side = fill_data.get("side", "").upper()
        price = float(fill_data.get("price", 0))
        qty = float(fill_data.get("amount", 0))

        if not all([symbol, side in ["BUY", "SELL"], price > 0, qty > 0]):
            logger.warning("Invalid fill data", extra={
                "event_type": "FILL_PROCESSING_ERROR",
                "reason": "invalid_data",
                "fill_data": fill_data
            })
            return

        # Process fill in PnL tracker
        pnl_tracker.on_fill(symbol, side, price, qty)

        logger.info("Fill processed", extra={
            "event_type": "TRADE_FILL",
            "symbol": symbol,
            "side": side,
            "price": price,
            "qty": qty,
            "pnl_summary": pnl_tracker.get_total_pnl()
        })

    except Exception as e:
        logger.error(f"Fill processing failed: {e}", extra={
            "event_type": "FILL_PROCESSING_ERROR",
            "error": str(e),
            "fill_data": fill_data
        })
