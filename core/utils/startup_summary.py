#!/usr/bin/env python3
"""
Startup Summary - Clean Terminal Output for Bot Initialization
"""

import config


def print_startup_summary(
    exchange_name: str = "MEXC",
    coin_count: int = 0,
    budget: float = 0.0,
    equity: float = 0.0,
    mode: str = "OBSERVE",
    portfolio_reset: bool = False,
    reset_from: float = 0.0,
    reset_to: float = 0.0
):
    """
    Print clean startup summary to terminal.

    Args:
        exchange_name: Exchange name (e.g., "MEXC")
        coin_count: Number of tradeable coins
        budget: Current USDT budget
        equity: Current equity (budget + positions)
        mode: Trading mode ("LIVE" or "OBSERVE")
        portfolio_reset: Whether portfolio was reset
        reset_from: Budget before reset
        reset_to: Budget after reset
    """

    # Get config values
    tp = config.TAKE_PROFIT_THRESHOLD
    sl = config.STOP_LOSS_THRESHOLD
    dt = config.DROP_TRIGGER_VALUE
    mode_num = config.DROP_TRIGGER_MODE
    lookback = config.DROP_TRIGGER_LOOKBACK_MIN
    order_size = config.POSITION_SIZE_USDT
    max_positions = config.MAX_TRADES
    cooldown = config.COOLDOWN_MIN
    use_trailing = config.USE_TRAILING_STOP
    trailing_activation = getattr(config, 'TRAILING_ACTIVATION_PCT', 0.5)
    trailing_callback = getattr(config, 'TRAILING_CALLBACK_PCT', 0.3)
    ttl = config.TRADE_TTL_MIN

    # Calculate percentages
    tp_pct = (tp - 1.0) * 100.0
    sl_pct = (sl - 1.0) * 100.0
    dt_pct = (dt - 1.0) * 100.0

    # Guards status
    guards = []
    if config.USE_SMA_GUARD:
        guards.append(f"SMA({config.SMA_GUARD_WINDOW})")
    if config.USE_SPREAD_GUARD:
        guards.append(f"Spread({config.GUARD_MAX_SPREAD_BPS}bp)")
    if config.USE_VOLUME_GUARD:
        guards.append(f"Volume({config.VOLUME_GUARD_FACTOR}x)")
    if config.USE_VOL_SIGMA_GUARD:
        guards.append(f"VolSigma({config.REQUIRE_VOL_SIGMA_BPS_MIN}bp)")
    if config.USE_BTC_FILTER:
        guards.append(f"BTC({config.BTC_CHANGE_THRESHOLD:.2%})")
    if config.USE_FALLING_COINS_FILTER:
        guards.append(f"Falling({config.FALLING_COINS_THRESHOLD:.0%})")

    guards_str = ", ".join(guards) if guards else "None (All OFF)"

    # Build output
    line = "━" * 80

    print(f"\n{line}")
    print(f"🚀 Trading Bot v1.0 startet...")
    print(line)

    # Exchange & Config
    print(f"   Exchange:      {exchange_name} ({coin_count} Coins)")
    print(f"   Budget:        {budget:.2f} USDT")
    if equity != budget:
        print(f"   Equity:        {equity:.2f} USDT")
    print(f"   Mode:          {mode}")
    print()

    # Trading Parameters
    print(f"   Strategy:      TP={tp_pct:+.1f}% | SL={sl_pct:+.1f}% | DT={dt_pct:+.1f}%")
    print(f"   Drop Mode:     Mode {mode_num} (Lookback: {lookback}min)")
    print(f"   Order Size:    {order_size:.2f} USDT/Trade")
    print(f"   Max Positions: {max_positions} | Cooldown: {cooldown}min | TTL: {ttl}min")
    print()

    # Trailing Stop
    trailing_status = f"ON (Activate: {trailing_activation:.1f}%, Callback: {trailing_callback:.1f}%)" if use_trailing else "OFF"
    print(f"   Trailing Stop: {trailing_status}")
    print()

    # Guards
    print(f"   Guards:        {guards_str}")
    print()

    # Portfolio Reset
    if portfolio_reset:
        print(f"   Portfolio:     ✓ Reset ({reset_from:.2f} → {reset_to:.2f} USDT)")
    else:
        print(f"   Portfolio:     No Reset")

    print(line)
    print("✅ Trading Engine ready")
    print(f"{line}\n")
