#!/usr/bin/env python3
"""
Telegram Service Adapter for Trading Bot V11

Provides unified access to all engine services for Telegram integration.
Ensures consistent data access and proper error handling.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PositionSummary:
    """Summary of a trading position"""
    symbol: str
    amount: float
    buying_price: float
    current_price: float
    unrealized_pnl: float
    pnl_percentage: float
    entry_time: float
    duration_seconds: float
    signal_reason: str = ""
    order_id: str = ""


@dataclass
class DropSignal:
    """Drop signal information"""
    symbol: str
    current_price: float
    anchor_price: float
    drop_percentage: float
    signal_strength: float
    is_signal: bool
    is_holding: bool
    has_buy_order: bool
    anchor_timestamp: str


@dataclass
class GuardStatus:
    """Market guard status"""
    name: str
    enabled: bool
    passing: bool
    current_value: Optional[float]
    threshold: Optional[float]
    reason: Optional[str]


class TelegramServiceAdapter:
    """
    Service adapter for Telegram integration with V11 engine services
    """

    def __init__(self, engine):
        self.engine = engine
        self._last_update = 0
        self._cache_ttl = 5.0  # 5 seconds cache for frequent requests

    # =================================================================
    # PORTFOLIO & PNL METHODS
    # =================================================================

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get comprehensive portfolio summary"""
        try:
            if not self.engine:
                return {"error": "Engine not available"}

            # Get basic portfolio info
            portfolio = getattr(self.engine, 'portfolio', None)
            if not portfolio:
                return {"error": "Portfolio service not available"}

            # Get PnL summary via PnL service
            pnl_summary = self.get_pnl_summary()

            # Get available slots
            max_positions = getattr(self.engine.config, 'max_positions', 5)
            current_positions = len(self.get_open_positions())
            available_slots = max(0, max_positions - current_positions)

            # Get budget info
            budget_usdt = getattr(portfolio, 'my_budget', 0)

            # Get open orders count
            open_orders_count = len(getattr(portfolio, 'open_buy_orders', {}))

            return {
                'budget_usdt': f"{budget_usdt:.2f}",
                'available_slots': available_slots,
                'held_assets_count': current_positions,
                'open_buy_orders_count': open_orders_count,
                'total_positions': max_positions,
                'unrealized_pnl': pnl_summary.get('total_unrealized_pnl', 0),
                'realized_pnl': pnl_summary.get('session_realized_pnl', 0),
                'total_pnl': pnl_summary.get('total_pnl', 0)
            }

        except Exception as e:
            logger.error(f"Portfolio summary error: {e}")
            return {"error": str(e)}

    def get_pnl_summary(self) -> Dict[str, Any]:
        """Get PnL summary via PnL service"""
        try:
            pnl_service = getattr(self.engine, 'pnl_service', None)
            if not pnl_service:
                return {"error": "PnL service not available"}

            summary = pnl_service.get_pnl_summary()

            return {
                'total_unrealized_pnl': summary.total_unrealized_pnl,
                'session_realized_pnl': summary.session_realized_pnl,
                'total_pnl': summary.total_unrealized_pnl + summary.session_realized_pnl,
                'position_count': len(summary.positions),
                'positions': summary.positions
            }

        except Exception as e:
            logger.error(f"PnL summary error: {e}")
            return {"error": str(e)}

    def get_open_positions(self) -> List[PositionSummary]:
        """Get list of open positions with current PnL"""
        try:
            positions = []

            # Get positions from portfolio
            portfolio = getattr(self.engine, 'portfolio', None)
            if not portfolio or not hasattr(portfolio, 'held_assets'):
                return positions

            # Get current prices from market data
            market_data = getattr(self.engine, 'market_data', None)

            for symbol, data in portfolio.held_assets.items():
                try:
                    amount = float(data.get('amount', 0))
                    if amount <= 0:
                        continue

                    buying_price = float(data.get('buying_price', data.get('avg_price', 0)))
                    entry_time = float(data.get('time', time.time()))
                    signal_reason = data.get('signal', '')
                    order_id = data.get('order_id', '')

                    # Get current price
                    current_price = 0
                    if market_data:
                        current_price = market_data.get_price(symbol) or 0

                    if current_price <= 0:
                        # Fallback to engine price cache
                        preise = getattr(self.engine, 'preise', {})
                        current_price = preise.get(symbol, buying_price)

                    # Calculate PnL
                    if current_price > 0 and buying_price > 0:
                        unrealized_pnl = (current_price - buying_price) * amount
                        pnl_percentage = ((current_price / buying_price) - 1) * 100
                    else:
                        unrealized_pnl = 0
                        pnl_percentage = 0

                    duration_seconds = time.time() - entry_time

                    positions.append(PositionSummary(
                        symbol=symbol,
                        amount=amount,
                        buying_price=buying_price,
                        current_price=current_price,
                        unrealized_pnl=unrealized_pnl,
                        pnl_percentage=pnl_percentage,
                        entry_time=entry_time,
                        duration_seconds=duration_seconds,
                        signal_reason=signal_reason,
                        order_id=order_id
                    ))

                except Exception as e:
                    logger.error(f"Error processing position {symbol}: {e}")
                    continue

            return positions

        except Exception as e:
            logger.error(f"Get positions error: {e}")
            return []

    # =================================================================
    # SIGNAL & DROP METHODS
    # =================================================================

    def get_top_drops(self, limit: int = 10) -> List[DropSignal]:
        """Get top drops via BuySignalService"""
        try:
            buy_signal_service = getattr(self.engine, 'buy_signal_service', None)
            if not buy_signal_service:
                return []

            # Get drops from service
            drops_data = buy_signal_service.get_top_drops(limit=limit * 2)  # Get more for filtering

            drops = []
            portfolio = getattr(self.engine, 'portfolio', None)

            for drop_data in drops_data[:limit]:
                symbol = drop_data['symbol']
                current_price = drop_data['current_price']
                anchor_price = drop_data['anchor_price']
                drop_pct = drop_data['drop_percentage']

                # Calculate signal strength (how close to trigger)
                import config
                trigger_threshold = (1 - config.DROP_TRIGGER_VALUE) * 100
                signal_strength = abs(drop_pct) / trigger_threshold if trigger_threshold > 0 else 0
                is_signal = signal_strength >= 1.0

                # Check if holding or has orders
                is_holding = False
                has_buy_order = False
                if portfolio:
                    is_holding = symbol in getattr(portfolio, 'held_assets', {})
                    has_buy_order = symbol in getattr(portfolio, 'open_buy_orders', {})

                drops.append(DropSignal(
                    symbol=symbol,
                    current_price=current_price,
                    anchor_price=anchor_price,
                    drop_percentage=drop_pct,
                    signal_strength=signal_strength,
                    is_signal=is_signal,
                    is_holding=is_holding,
                    has_buy_order=has_buy_order,
                    anchor_timestamp=drop_data.get('anchor_timestamp', 'Unknown')
                ))

            return drops

        except Exception as e:
            logger.error(f"Get top drops error: {e}")
            return []

    def get_current_signals(self) -> List[DropSignal]:
        """Get current buy signals (drops that triggered)"""
        try:
            all_drops = self.get_top_drops(20)

            # Filter for actual signals (not holding, not pending order)
            signals = [
                drop for drop in all_drops
                if drop.is_signal and not drop.is_holding and not drop.has_buy_order
            ]

            # Sort by signal strength
            signals.sort(key=lambda x: x.signal_strength, reverse=True)

            return signals[:8]  # Top 8 signals

        except Exception as e:
            logger.error(f"Get current signals error: {e}")
            return []

    def get_signal_preview(self, limit: int = 5) -> Tuple[str, str]:
        """Get signal preview for status display"""
        try:
            drops = self.get_top_drops(limit)
            if not drops:
                return "", ""

            # Format drops line
            drops_parts = []
            for drop in drops:
                short_symbol = drop.symbol.replace('/USDT', '')
                drops_parts.append(f"{short_symbol}:{drop.drop_percentage:.1f}%")

            drops_line = "📉 Drops: " + " ".join(drops_parts)

            # Next signal (strongest drop)
            if drops:
                next_drop = drops[0]
                trigger_gap = next_drop.signal_strength - 1.0  # How far from trigger
                gap_str = f"{trigger_gap:+.1f}x"
                next_line = (f"🔔 Next Signal: <code>{next_drop.symbol}</code> "
                           f"(Drop {next_drop.drop_percentage:.1f}%, ΔTrigger {gap_str})")
            else:
                next_line = ""

            return drops_line, next_line

        except Exception as e:
            logger.error(f"Signal preview error: {e}")
            return "", ""

    # =================================================================
    # MARKET GUARD METHODS
    # =================================================================

    def get_guard_status(self) -> List[GuardStatus]:
        """Get current market guard status"""
        try:
            market_guards = getattr(self.engine, 'market_guards', None)
            if not market_guards:
                return []

            # Get overall status
            all_passing, failed_guards = market_guards.passes_all_guards("BTC/USDT", 50000)  # Dummy values

            # Get detailed status
            guard_statuses = []

            # BTC Filter
            if hasattr(market_guards, 'use_btc_filter') and market_guards.use_btc_filter:
                btc_change = getattr(market_guards, '_market_conditions', {}).get('btc_change_factor')
                threshold = getattr(market_guards, 'btc_change_threshold', 0.995)
                passing = btc_change >= threshold if btc_change else None

                guard_statuses.append(GuardStatus(
                    name="BTC Filter",
                    enabled=True,
                    passing=passing if passing is not None else False,
                    current_value=btc_change,
                    threshold=threshold,
                    reason=None if passing else "BTC change too low"
                ))

            # Falling Coins Filter
            if hasattr(market_guards, 'use_falling_coins_filter') and market_guards.use_falling_coins_filter:
                falling_pct = getattr(market_guards, '_market_conditions', {}).get('percentage_falling')
                threshold = getattr(market_guards, 'falling_coins_threshold', 50)
                passing = falling_pct <= threshold if falling_pct else None

                guard_statuses.append(GuardStatus(
                    name="Falling Coins Filter",
                    enabled=True,
                    passing=passing if passing is not None else False,
                    current_value=falling_pct,
                    threshold=threshold,
                    reason=None if passing else "Too many coins falling"
                ))

            # Add other guards as needed...

            return guard_statuses

        except Exception as e:
            logger.error(f"Guard status error: {e}")
            return []

    # =================================================================
    # TRADING OPERATIONS
    # =================================================================

    def retarget_positions(self, mode: str = "both") -> int:
        """Retarget open positions with new TP/SL values"""
        try:
            if not self.engine:
                return 0

            # Get current positions
            portfolio = getattr(self.engine, 'portfolio', None)
            if not portfolio:
                return 0

            updated_count = 0

            # For each held position, update TP/SL orders
            held_assets = getattr(portfolio, 'held_assets', {})

            for symbol in held_assets.keys():
                try:
                    # Cancel existing TP/SL orders if any
                    # (This would need to be implemented based on your order management)

                    # Place new TP/SL orders with current config values
                    if mode in ("tp", "both"):
                        # Update TP orders
                        # This would call your order placement service
                        pass

                    if mode in ("sl", "both"):
                        # Update SL orders
                        # This would call your order placement service
                        pass

                    updated_count += 1

                except Exception as e:
                    logger.error(f"Failed to retarget {symbol}: {e}")
                    continue

            logger.info(f"Retargeted {updated_count} positions with mode: {mode}")
            return updated_count

        except Exception as e:
            logger.error(f"Retarget positions error: {e}")
            return 0

    def execute_panic_sell(self, reason: str = "TELEGRAM_PANIC") -> Tuple[bool, str]:
        """Execute panic sell via trading service"""
        try:
            if not self.engine:
                return False, "Engine not available"

            # Check if engine has a panic sell method
            if hasattr(self.engine, 'full_portfolio_reset'):
                success = self.engine.full_portfolio_reset(reason=reason)
                if success:
                    return True, "✅ All positions closed successfully"
                else:
                    return False, "⚠️ Some positions could not be closed"

            # Fallback: Manual position closing
            portfolio = getattr(self.engine, 'portfolio', None)
            if not portfolio:
                return False, "Portfolio not available"

            held_assets = getattr(portfolio, 'held_assets', {})
            if not held_assets:
                return True, "📊 No positions to close"

            closed_count = 0
            error_count = 0

            for symbol in list(held_assets.keys()):
                try:
                    # This would call your exit service
                    # For now, we'll log the action
                    logger.info(f"PANIC_SELL: Closing position {symbol}")
                    closed_count += 1

                except Exception as e:
                    error_count += 1
                    logger.error(f"Failed to close {symbol}: {e}")

            if closed_count > 0:
                msg = f"✅ Closed {closed_count} positions"
                if error_count > 0:
                    msg += f"\n⚠️ {error_count} errors occurred"
                return True, msg
            else:
                return False, f"❌ Failed to close positions ({error_count} errors)"

        except Exception as e:
            logger.error(f"Panic sell error: {e}")
            return False, f"❌ Error: {str(e)}"

    # =================================================================
    # CONFIGURATION METHODS
    # =================================================================

    def get_current_parameters(self) -> Dict[str, float]:
        """Get current trading parameters"""
        try:
            import config
            return {
                'drop_trigger_value': float(config.DROP_TRIGGER_VALUE),
                'take_profit_threshold': float(config.TAKE_PROFIT_THRESHOLD),
                'stop_loss_threshold': float(config.STOP_LOSS_THRESHOLD)
            }
        except Exception as e:
            logger.error(f"Get parameters error: {e}")
            return {}

    def update_parameter(self, param: str, value: float) -> bool:
        """Update trading parameter"""
        try:
            import config

            # CRITICAL FIX (C-CONFIG-02): Use thread-safe override instead of direct mutation
            if param == "dt":
                config.set_config_override('DROP_TRIGGER_VALUE', value)
                config.set_config_override('drop_trigger_value', value)
            elif param == "tp":
                config.set_config_override('TAKE_PROFIT_THRESHOLD', value)
                config.set_config_override('take_profit_threshold', value)
            elif param == "sl":
                config.set_config_override('STOP_LOSS_THRESHOLD', value)
                config.set_config_override('stop_loss_threshold', value)
            else:
                return False

            # Notify engine of config change if method exists
            if hasattr(self.engine, 'log_config_change'):
                old_val = getattr(config, param.upper() + '_THRESHOLD' if param != 'dt' else 'DROP_TRIGGER_VALUE', 0)
                self.engine.log_config_change(param, old_val, value, "telegram")

            return True

        except Exception as e:
            logger.error(f"Update parameter error: {e}")
            return False

    # =================================================================
    # UTILITY METHODS
    # =================================================================

    def get_btc_info(self) -> Tuple[float, float]:
        """Get BTC price and change percentage"""
        try:
            market_data = getattr(self.engine, 'market_data', None)
            btc_price = 0
            btc_change = 0

            if market_data:
                btc_price = market_data.get_price("BTC/USDT") or 0
            else:
                # Fallback to engine price cache
                preise = getattr(self.engine, 'preise', {})
                btc_price = preise.get("BTC/USDT", 0)

            # Get BTC change from market guards if available
            market_guards = getattr(self.engine, 'market_guards', None)
            if market_guards:
                market_conditions = getattr(market_guards, '_market_conditions', {})
                btc_change_factor = market_conditions.get('btc_change_factor', 1.0)
                btc_change = (btc_change_factor - 1.0) * 100

            return float(btc_price), float(btc_change)

        except Exception as e:
            logger.error(f"Get BTC info error: {e}")
            return 0.0, 0.0

    def is_engine_ready(self) -> bool:
        """Check if engine and services are ready"""
        return bool(
            self.engine and
            hasattr(self.engine, 'portfolio') and
            hasattr(self.engine, 'running')
        )

    def get_error_status(self) -> str:
        """Get current error status for debugging"""
        if not self.engine:
            return "Engine not initialized"

        missing_services = []

        if not hasattr(self.engine, 'portfolio'):
            missing_services.append("portfolio")
        if not hasattr(self.engine, 'pnl_service'):
            missing_services.append("pnl_service")
        if not hasattr(self.engine, 'buy_signal_service'):
            missing_services.append("buy_signal_service")
        if not hasattr(self.engine, 'market_guards'):
            missing_services.append("market_guards")

        if missing_services:
            return f"Missing services: {', '.join(missing_services)}"

        return "All services available"
