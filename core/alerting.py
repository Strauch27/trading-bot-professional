#!/usr/bin/env python3
"""
Alert Management System

Provides real-time monitoring and alerting for:
- Portfolio drawdown
- Order execution issues
- Rate limit hits
- Position exposure
- Exchange connectivity
- System health

Usage:
    from core.alerting import AlertManager, AlertRule, AlertSeverity

    alert_manager = AlertManager(telegram_bot=telegram)

    # Add rule
    alert_manager.add_rule(AlertRule(
        name="portfolio_drawdown",
        condition=lambda ctx: ctx.get('daily_pnl_pct', 0) < -5.0,
        severity=AlertSeverity.ERROR,
        message_template="Portfolio drawdown: {daily_pnl_pct:.2f}%",
        cooldown_seconds=600
    ))

    # Check alerts periodically
    context = {
        'daily_pnl_pct': -6.5,
        'portfolio_exposure_pct': 0.75
    }
    alert_manager.check_alerts(context)
"""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    """Alert severity levels (ordered by importance)"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AlertRule:
    """
    Alert rule definition with condition and action.

    Attributes:
        name: Unique rule identifier
        condition: Function that evaluates context and returns True if alert should fire
        severity: Alert severity level
        message_template: Message format string (supports .format(**context))
        cooldown_seconds: Minimum time between alerts for same rule
        enabled: Whether rule is active
    """
    name: str
    condition: Callable[[Dict], bool]
    severity: AlertSeverity
    message_template: str
    cooldown_seconds: int = 300  # 5 minutes default
    enabled: bool = True


class AlertManager:
    """
    Manage alert rules and notifications.

    Responsibilities:
    - Register and store alert rules
    - Evaluate rules against current state
    - Enforce cooldown periods
    - Send notifications via multiple channels (Telegram, logs)
    - Track alert history for analytics
    """

    def __init__(self, telegram_bot=None):
        """
        Initialize alert manager.

        Args:
            telegram_bot: Optional Telegram bot for notifications
        """
        self.telegram_bot = telegram_bot
        self.rules: List[AlertRule] = []
        self.alert_history: Dict[str, float] = {}  # rule_name → last_fired_timestamp
        self.alert_count: Dict[str, int] = {}  # rule_name → total_fires
        self._lock = threading.RLock()

        logger.info("Alert manager initialized")

    def add_rule(self, rule: AlertRule):
        """
        Register alert rule.

        Args:
            rule: AlertRule instance to register
        """
        with self._lock:
            # Check for duplicate rule names
            if any(r.name == rule.name for r in self.rules):
                logger.warning(f"Alert rule '{rule.name}' already exists, replacing")
                self.rules = [r for r in self.rules if r.name != rule.name]

            self.rules.append(rule)
            logger.info(f"Alert rule registered: {rule.name} ({rule.severity.value})")

    def remove_rule(self, rule_name: str) -> bool:
        """
        Remove alert rule by name.

        Args:
            rule_name: Name of rule to remove

        Returns:
            True if rule was removed
        """
        with self._lock:
            initial_count = len(self.rules)
            self.rules = [r for r in self.rules if r.name != rule_name]
            removed = len(self.rules) < initial_count

            if removed:
                logger.info(f"Alert rule removed: {rule_name}")
                self.alert_history.pop(rule_name, None)
                self.alert_count.pop(rule_name, None)

            return removed

    def check_alerts(self, context: Dict):
        """
        Check all alert rules against current context.

        Args:
            context: Dictionary with current state (portfolio, positions, metrics, etc.)
                    Example: {'daily_pnl_pct': -6.5, 'cancel_rate': 0.35, ...}
        """
        with self._lock:
            for rule in self.rules:
                if not rule.enabled:
                    continue

                try:
                    # Check cooldown
                    last_fired = self.alert_history.get(rule.name, 0)
                    if time.time() - last_fired < rule.cooldown_seconds:
                        continue

                    # Evaluate condition
                    if rule.condition(context):
                        # Fire alert
                        try:
                            message = rule.message_template.format(**context)
                        except KeyError as e:
                            logger.error(f"Alert template error for {rule.name}: missing key {e}")
                            message = f"{rule.message_template} (template error)"

                        self._fire_alert(rule, message, context)
                        self.alert_history[rule.name] = time.time()
                        self.alert_count[rule.name] = self.alert_count.get(rule.name, 0) + 1

                except Exception as e:
                    logger.error(f"Error evaluating alert rule {rule.name}: {e}", exc_info=True)

    def _fire_alert(self, rule: AlertRule, message: str, context: Dict):
        """
        Send alert via configured channels.

        Args:
            rule: Alert rule that triggered
            message: Formatted alert message
            context: Context dict that triggered the alert
        """
        # Get severity emoji
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.ERROR: "❌",
            AlertSeverity.CRITICAL: "🚨"
        }

        emoji = severity_emoji.get(rule.severity, "")
        full_message = f"{emoji} {rule.severity.value.upper()}: {message}"

        # Log alert to console
        log_level = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.ERROR: logging.ERROR,
            AlertSeverity.CRITICAL: logging.CRITICAL
        }.get(rule.severity, logging.WARNING)

        logger.log(log_level, f"ALERT FIRED: {rule.name} - {message}")

        # Send to Telegram if available
        if self.telegram_bot:
            try:
                self.telegram_bot.send_message(full_message)
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")

        # Phase 5: Log structured alert_fired event
        try:
            from core.logger_factory import HEALTH_LOG, log_event
            from core.trace_context import Trace

            with Trace():
                log_event(
                    HEALTH_LOG(),
                    "alert_fired",
                    alert_name=rule.name,
                    severity=rule.severity.value,
                    message=message,
                    cooldown_seconds=rule.cooldown_seconds,
                    fire_count=self.alert_count.get(rule.name, 1),
                    level=log_level
                )
        except Exception as e:
            logger.debug(f"Failed to log alert_fired event: {e}")

    def get_alert_stats(self) -> Dict:
        """
        Get alert statistics.

        Returns:
            Dict with alert counts and history
        """
        with self._lock:
            return {
                'total_rules': len(self.rules),
                'enabled_rules': sum(1 for r in self.rules if r.enabled),
                'total_alerts_fired': sum(self.alert_count.values()),
                'alerts_by_rule': dict(self.alert_count),
                'last_alerts': {
                    name: time.time() - ts
                    for name, ts in self.alert_history.items()
                }
            }

    def reset_cooldowns(self):
        """Reset all alert cooldowns (for testing or manual intervention)"""
        with self._lock:
            self.alert_history.clear()
            logger.info("Alert cooldowns reset")


# ============================================================================
# Pre-configured Alert Rules for Common Scenarios
# ============================================================================

def create_default_alerts(telegram_bot=None) -> AlertManager:
    """
    Create alert manager with default production rules.

    Args:
        telegram_bot: Optional Telegram bot for notifications

    Returns:
        Configured AlertManager instance
    """
    alert_manager = AlertManager(telegram_bot)

    # Rule 1: Portfolio Drawdown Alert
    alert_manager.add_rule(AlertRule(
        name="portfolio_drawdown",
        condition=lambda ctx: ctx.get('daily_pnl_pct', 0) < -5.0,
        severity=AlertSeverity.ERROR,
        message_template="Portfolio drawdown: {daily_pnl_pct:.2f}%",
        cooldown_seconds=600  # 10 minutes
    ))

    # Rule 2: High Order Cancel Rate
    alert_manager.add_rule(AlertRule(
        name="high_cancel_rate",
        condition=lambda ctx: ctx.get('cancel_rate', 0) > 0.50 and ctx.get('total_orders', 0) >= 5,
        severity=AlertSeverity.WARNING,
        message_template="High order cancel rate: {cancel_rate:.1%} ({cancel_count}/{total_orders} orders)",
        cooldown_seconds=300  # 5 minutes
    ))

    # Rule 3: Rate Limit Hits
    alert_manager.add_rule(AlertRule(
        name="rate_limit_threshold",
        condition=lambda ctx: ctx.get('rate_limit_hits_1min', 0) >= 3,
        severity=AlertSeverity.CRITICAL,
        message_template="Rate limit hit {rate_limit_hits_1min}x in 1 minute - throttling required!",
        cooldown_seconds=180  # 3 minutes
    ))

    # Rule 4: High Portfolio Exposure
    alert_manager.add_rule(AlertRule(
        name="high_exposure",
        condition=lambda ctx: ctx.get('portfolio_exposure_pct', 0) > 0.85,
        severity=AlertSeverity.WARNING,
        message_template="Portfolio exposure high: {portfolio_exposure_pct:.1%} - nearing limit",
        cooldown_seconds=600  # 10 minutes
    ))

    # Rule 5: Exchange Connection Lost
    alert_manager.add_rule(AlertRule(
        name="exchange_connection_lost",
        condition=lambda ctx: not ctx.get('exchange_connected', True),
        severity=AlertSeverity.CRITICAL,
        message_template="Exchange connection lost! Bot may be unable to trade.",
        cooldown_seconds=120  # 2 minutes
    ))

    # Rule 6: Low Cash Balance
    alert_manager.add_rule(AlertRule(
        name="low_cash_balance",
        condition=lambda ctx: ctx.get('cash_usdt', float('inf')) < 50.0,
        severity=AlertSeverity.WARNING,
        message_template="Low cash balance: {cash_usdt:.2f} USDT - trades may fail",
        cooldown_seconds=900  # 15 minutes
    ))

    # Rule 7: Losing Streak
    alert_manager.add_rule(AlertRule(
        name="losing_streak",
        condition=lambda ctx: ctx.get('consecutive_losses', 0) >= 5,
        severity=AlertSeverity.WARNING,
        message_template="Losing streak detected: {consecutive_losses} consecutive losses",
        cooldown_seconds=1800  # 30 minutes
    ))

    logger.info(f"Default alert rules created: {alert_manager.get_alert_stats()['total_rules']} rules")

    return alert_manager
