#!/usr/bin/env python3
"""
Risk Limits Checker - Portfolio Risk Management

Provides comprehensive risk limit checking before order placement:
- Maximum portfolio exposure limits
- Maximum number of open positions
- Daily drawdown limits
- Maximum trades per day limits
- Per-symbol position size caps

Usage:
    from core.risk_limits import RiskLimitChecker

    checker = RiskLimitChecker(portfolio, config)
    all_passed, limit_checks = checker.check_limits(symbol, order_value_usdt)

    if not all_passed:
        logger.warning(f"Risk limit exceeded: {limit_checks}")
"""

import logging
import time
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class RiskLimitChecker:
    """
    Check portfolio risk limits before order placement.

    Evaluates all configured risk limits and returns detailed results
    for logging and decision making.
    """

    def __init__(self, portfolio, config):
        """
        Initialize risk limit checker.

        Args:
            portfolio: PortfolioManager instance
            config: Config module with risk limit settings
        """
        self.portfolio = portfolio
        self.config = config
        self._daily_trade_count = 0
        self._daily_trade_reset_ts = time.time()
        self._daily_pnl_start_budget = portfolio.my_budget

    def check_limits(self, symbol: str, order_value_usdt: float) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Check all risk limits for a proposed order.

        Args:
            symbol: Trading symbol
            order_value_usdt: Order value in USDT

        Returns:
            (all_passed: bool, limit_checks: List[Dict])

            limit_checks format:
            [
                {
                    "limit": "max_positions",
                    "value": 8,
                    "threshold": 10,
                    "hit": False
                },
                ...
            ]
        """
        limit_checks = []

        try:
            # 1. Max Positions Check
            current_positions = len(self.portfolio.held_assets)
            max_positions = getattr(self.config, 'MAX_TRADES', 10)

            limit_checks.append({
                "limit": "max_positions",
                "value": current_positions,
                "threshold": max_positions,
                "hit": current_positions >= max_positions
            })

            # 2. Max Portfolio Exposure Check
            try:
                # Calculate total exposure including new order
                total_exposure = order_value_usdt
                for held_symbol in self.portfolio.held_assets.keys():
                    total_exposure += self.portfolio.get_symbol_exposure_usdt(held_symbol)

                total_budget = self.portfolio.my_budget + total_exposure
                exposure_ratio = total_exposure / total_budget if total_budget > 0 else 0
                max_exposure_ratio = getattr(self.config, 'MAX_PORTFOLIO_EXPOSURE_PCT', 0.80)

                limit_checks.append({
                    "limit": "max_exposure",
                    "value": exposure_ratio,
                    "threshold": max_exposure_ratio,
                    "hit": exposure_ratio > max_exposure_ratio
                })
            except Exception as e:
                logger.debug(f"Failed to calculate portfolio exposure: {e}")

            # 3. Per-Symbol Position Cap Check
            try:
                symbol_exposure = self.portfolio.get_symbol_exposure_usdt(symbol)
                new_symbol_exposure = symbol_exposure + order_value_usdt

                total_budget = self.portfolio.my_budget
                for held_symbol in self.portfolio.held_assets.keys():
                    total_budget += self.portfolio.get_symbol_exposure_usdt(held_symbol)

                symbol_exposure_ratio = new_symbol_exposure / total_budget if total_budget > 0 else 0
                max_symbol_exposure_ratio = getattr(self.config, 'MAX_SYMBOL_EXPOSURE_PCT', 0.20)

                limit_checks.append({
                    "limit": "max_symbol_exposure",
                    "value": symbol_exposure_ratio,
                    "threshold": max_symbol_exposure_ratio,
                    "hit": symbol_exposure_ratio > max_symbol_exposure_ratio
                })
            except Exception as e:
                logger.debug(f"Failed to calculate per-symbol exposure for {symbol}: {e}")

            # 4. Daily Drawdown Check
            try:
                # Reset daily tracking if new day
                if time.time() - self._daily_trade_reset_ts > 86400:  # 24 hours
                    self._daily_pnl_start_budget = self.portfolio.my_budget
                    self._daily_trade_reset_ts = time.time()

                current_budget = self.portfolio.my_budget
                daily_pnl_pct = ((current_budget - self._daily_pnl_start_budget) / self._daily_pnl_start_budget) if self._daily_pnl_start_budget > 0 else 0
                max_drawdown = getattr(self.config, 'MAX_DAILY_DRAWDOWN_PCT', 0.08)

                # Only trigger if we're in drawdown (negative P&L)
                current_drawdown = abs(daily_pnl_pct) if daily_pnl_pct < 0 else 0

                limit_checks.append({
                    "limit": "daily_drawdown",
                    "value": current_drawdown,
                    "threshold": max_drawdown,
                    "hit": current_drawdown > max_drawdown
                })
            except Exception as e:
                logger.debug(f"Failed to calculate daily drawdown: {e}")

            # 5. Max Trades Per Day Check
            try:
                # Reset counter if new day
                if time.time() - self._daily_trade_reset_ts > 86400:  # 24 hours
                    self._daily_trade_count = 0
                    self._daily_trade_reset_ts = time.time()

                max_daily_trades = getattr(self.config, 'MAX_TRADES_PER_DAY', 120)

                limit_checks.append({
                    "limit": "max_trades_per_day",
                    "value": self._daily_trade_count,
                    "threshold": max_daily_trades,
                    "hit": self._daily_trade_count >= max_daily_trades
                })
            except Exception as e:
                logger.debug(f"Failed to check daily trade limit: {e}")

            # 6. Minimum Free Cash Reserve Check
            try:
                cash_reserve = getattr(self.config, 'CASH_RESERVE_USDT', 50.0)
                free_after_trade = self.portfolio.my_budget - order_value_usdt

                limit_checks.append({
                    "limit": "min_cash_reserve",
                    "value": free_after_trade,
                    "threshold": cash_reserve,
                    "hit": free_after_trade < cash_reserve
                })
            except Exception as e:
                logger.debug(f"Failed to check cash reserve: {e}")

        except Exception as e:
            logger.error(f"Risk limits check failed for {symbol}: {e}")
            # Return conservative result on error
            return False, [{
                "limit": "check_error",
                "value": 0,
                "threshold": 0,
                "hit": True,
                "error": str(e)
            }]

        # Determine if all limits passed
        all_passed = all(not check['hit'] for check in limit_checks)

        return all_passed, limit_checks

    def increment_daily_trade_count(self):
        """Increment daily trade counter after successful order."""
        if time.time() - self._daily_trade_reset_ts > 86400:
            self._daily_trade_count = 0
            self._daily_trade_reset_ts = time.time()

        self._daily_trade_count += 1


def evaluate_all_entry_guards(
    symbol: str,
    order_value_usdt: float,
    ask: float,
    portfolio,
    config,
    market_data_provider=None
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Phase 8: Consolidated entry guard evaluation.

    Checks all entry-side risk guards in one place:
    - Portfolio risk limits (positions, exposure, drawdown, etc.)
    - Market quality guards (spread, depth)
    - Any other configured pre-trade checks

    Args:
        symbol: Trading pair
        order_value_usdt: Order value in USDT
        ask: Current ask price
        portfolio: PortfolioManager instance
        config: Config module
        market_data_provider: Optional MarketDataProvider for spread/depth checks

    Returns:
        (all_passed: bool, block_reason: str, guard_ctx: Dict)
        - all_passed: True if all guards pass
        - block_reason: First blocking reason if any guard fails (empty string if all pass)
        - guard_ctx: Full context with all guard results
    """
    guard_ctx: Dict[str, Any] = {
        "symbol": symbol,
        "order_value_usdt": order_value_usdt
    }

    # 1. Portfolio Risk Limits
    try:
        risk_checker = RiskLimitChecker(portfolio, config)
        limits_passed, limit_checks = risk_checker.check_limits(symbol, order_value_usdt)

        guard_ctx["risk_limits"] = limit_checks
        guard_ctx["risk_limits_passed"] = limits_passed

        if not limits_passed:
            # Find first failed limit
            failed_limit = next((check for check in limit_checks if check['hit']), None)
            if failed_limit:
                block_reason = f"RISK_LIMIT_{failed_limit['limit'].upper()}"
                logger.warning(
                    f"Risk limit blocked order for {symbol}: {failed_limit['limit']} "
                    f"({failed_limit['value']:.4f} > {failed_limit['threshold']:.4f})"
                )
                return False, block_reason, guard_ctx
    except Exception as e:
        logger.error(f"Error checking risk limits for {symbol}: {e}")
        guard_ctx["risk_limits_error"] = str(e)
        # Conservative: block on error
        return False, "RISK_LIMITS_ERROR", guard_ctx

    # 2. Market Quality Guards (Spread/Depth)
    enable_spread_guard = getattr(config, 'ENABLE_SPREAD_GUARD_ENTRY', False)
    enable_depth_guard = getattr(config, 'ENABLE_DEPTH_GUARD_ENTRY', False)

    if market_data_provider and (enable_spread_guard or enable_depth_guard):
        try:
            # Spread guard
            if enable_spread_guard:
                has_spread = hasattr(market_data_provider, 'get_spread')
                if has_spread:
                    spread_bps = market_data_provider.get_spread(symbol)
                    if spread_bps is not None:
                        guard_ctx["spread_bps"] = float(spread_bps)
                        max_spread = getattr(config, 'MAX_SPREAD_BPS_ENTRY', 10)

                        if spread_bps > max_spread:
                            logger.warning(
                                f"Spread guard blocked order for {symbol}: "
                                f"{spread_bps:.1f} bps > {max_spread} bps"
                            )
                            return False, "WIDE_SPREAD", guard_ctx

            # Depth guard
            if enable_depth_guard:
                has_depth = hasattr(market_data_provider, 'get_top5_depth')
                if has_depth:
                    bid_depth, ask_depth = market_data_provider.get_top5_depth(symbol, levels=5)
                    guard_ctx["bid_depth_usd"] = float(bid_depth)
                    guard_ctx["ask_depth_usd"] = float(ask_depth)

                    min_depth = getattr(config, 'DEPTH_MIN_NOTIONAL_USD', 200)
                    if ask_depth < min_depth:
                        logger.warning(
                            f"Depth guard blocked order for {symbol}: "
                            f"${ask_depth:.2f} < ${min_depth}"
                        )
                        return False, "THIN_DEPTH", guard_ctx

        except Exception as e:
            logger.error(f"Error checking market quality guards for {symbol}: {e}")
            guard_ctx["market_quality_error"] = str(e)
            # Non-critical: allow order to proceed if market quality checks fail
            # (risk limits are more critical)

    # All guards passed
    guard_ctx["all_passed"] = True
    return True, "", guard_ctx
