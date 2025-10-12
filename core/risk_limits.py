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

import time
import logging
from typing import Tuple, List, Dict, Any

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
            max_positions = getattr(self.config, 'max_trades', 10)

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
                cash_reserve = getattr(self.config, 'cash_reserve_usdt', 50.0)
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
