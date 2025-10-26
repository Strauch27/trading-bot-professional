# heartbeat_telemetry.py
# V10-Style Enhanced Heartbeat Telemetry: mehr Metriken, weniger Rauschen, bessere Insights

import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Tuple, Union


@dataclass
class TradingMetrics:
    """Core Trading-Metriken für Heartbeat"""
    timestamp: float

    # Budget & Positions
    budget_quote: float
    positions_open: int
    gross_exposure: float
    net_exposure: float

    # PnL Tracking
    pnl_realized_session: float
    pnl_unrealized: float
    pnl_fees_session: float
    equity_delta: float

    # Performance Metrics
    session_high_equity: float
    session_drawdown_current: float
    session_drawdown_peak: float

    # Trading Activity
    fills_last_hour: int
    fills_last_5min: int
    avg_fill_size_quote: float

    # Slippage & Execution
    avg_slippage_bp_last_hour: float
    worst_slippage_bp_last_hour: float
    execution_success_rate: float

    # Risk Metrics
    position_concentration_max: float
    correlation_risk_score: float
    volatility_score_portfolio: float

    # System Health
    api_latency_avg_ms: float
    error_rate_last_hour: float
    retry_rate_last_hour: float

@dataclass
class SymbolMetrics:
    """Symbol-spezifische Metriken"""
    symbol: str
    position_size: float
    unrealized_pnl: float
    avg_entry_price: float
    current_price: float
    hold_duration_hours: float
    drawdown_from_peak: float

@dataclass
class MarketHealthMetrics:
    """Market-Health Indicators"""
    overall_trend: str          # "bullish", "bearish", "sideways", "volatile"
    volatility_regime: str      # "low", "normal", "high", "extreme"
    volume_profile: str         # "low", "normal", "high", "unusual"
    correlation_breakdown: float # 0-1 score
    fear_greed_proxy: float     # -1 to +1
    market_breadth: float       # -1 to +1

class RollingStats:
    """Rolling Statistiken für Performance-Tracking"""

    def __init__(self, maxlen: int = 1000):
        self.maxlen = maxlen
        self.data = deque(maxlen=maxlen)
        self.lock = threading.RLock()

    def add(self, value: Union[float, Dict], timestamp: float = None):
        """Fügt Datenpunkt hinzu"""
        if timestamp is None:
            timestamp = time.time()

        with self.lock:
            self.data.append({"value": value, "timestamp": timestamp})

    def get_recent(self, seconds: float) -> List:
        """Gibt Daten der letzten X Sekunden zurück"""
        cutoff = time.time() - seconds
        with self.lock:
            return [item for item in self.data if item["timestamp"] >= cutoff]

    def get_stats(self, seconds: float = 3600) -> Dict:
        """Berechnet Statistiken für Zeitfenster"""
        recent = self.get_recent(seconds)

        if not recent:
            return {"count": 0}

        values = []
        for item in recent:
            if isinstance(item["value"], (int, float)):
                values.append(item["value"])
            elif isinstance(item["value"], dict) and "value" in item["value"]:
                values.append(item["value"]["value"])

        if not values:
            return {"count": len(recent)}

        return {
            "count": len(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
            "std": statistics.stdev(values) if len(values) > 1 else 0,
            "p90": statistics.quantiles(values, n=10)[8] if len(values) >= 10 else max(values),
            "p95": statistics.quantiles(values, n=20)[18] if len(values) >= 20 else max(values)
        }

class TelemetryAggregator:
    """
    Aggregiert und verarbeitet Telemetrie-Daten für kompakte Heartbeats.
    Fokus auf actionable insights statt Daten-Dump.
    """

    def __init__(self):
        self.lock = threading.RLock()

        # Core data streams
        self.fills = RollingStats(maxlen=500)
        self.slippage = RollingStats(maxlen=500)
        self.api_latencies = RollingStats(maxlen=200)
        self.errors = RollingStats(maxlen=100)
        self.retries = RollingStats(maxlen=100)

        # Session tracking
        self.session_start = time.time()
        self.session_high_equity = 0.0
        self.session_peak_drawdown = 0.0

        # State tracking
        self.last_heartbeat = 0.0
        self.current_positions = {}
        self.market_health_cache = None
        self.market_health_cache_time = 0

        # Performance tracking
        self.successful_orders = 0
        self.failed_orders = 0
        self.total_volume_quote = 0.0

    def record_fill(self, symbol: str, side: str, qty: float, price: float,
                   slippage_bp: float, fill_time: float = None):
        """Zeichnet Trade-Fill auf"""
        if fill_time is None:
            fill_time = time.time()

        fill_data = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "notional": abs(qty * price),
            "slippage_bp": slippage_bp
        }

        with self.lock:
            self.fills.add(fill_data, fill_time)
            self.slippage.add(slippage_bp, fill_time)
            self.total_volume_quote += abs(qty * price)
            self.successful_orders += 1

    def record_api_latency(self, latency_ms: float, endpoint: str = "unknown"):
        """Zeichnet API-Latency auf"""
        latency_data = {
            "latency_ms": latency_ms,
            "endpoint": endpoint
        }
        self.api_latencies.add(latency_data)

    def record_error(self, error_type: str, details: Dict = None):
        """Zeichnet Fehler auf"""
        error_data = {
            "error_type": error_type,
            "details": details or {}
        }
        with self.lock:
            self.errors.add(error_data)
            if error_type not in ["retry", "rate_limit"]:  # Don't count retries as failed orders
                self.failed_orders += 1

    def record_retry(self, function_name: str, attempt: int, reason: str):
        """Zeichnet Retry-Versuch auf"""
        retry_data = {
            "function": function_name,
            "attempt": attempt,
            "reason": reason
        }
        self.retries.add(retry_data)

    def update_positions(self, positions: Dict[str, Dict]):
        """Aktualisiert aktuelle Positionen"""
        with self.lock:
            self.current_positions = positions.copy()

    def update_market_health(self, health_metrics: MarketHealthMetrics):
        """Aktualisiert Market-Health Cache"""
        with self.lock:
            self.market_health_cache = health_metrics
            self.market_health_cache_time = time.time()

    def update_session_equity(self, current_equity_delta: float):
        """Aktualisiert Session-Equity für Drawdown-Tracking"""
        with self.lock:
            # Update session high
            if current_equity_delta > self.session_high_equity:
                self.session_high_equity = current_equity_delta

            # Calculate current drawdown
            current_drawdown = current_equity_delta - self.session_high_equity

            # Update peak drawdown (most negative)
            if current_drawdown < self.session_peak_drawdown:
                self.session_peak_drawdown = current_drawdown

    def _calculate_portfolio_risk_score(self, positions: Dict) -> Tuple[float, float, float]:
        """Berechnet Portfolio-Risk-Scores"""
        if not positions:
            return 0.0, 0.0, 0.0

        # Position concentration (largest position as % of total)
        total_exposure = sum(abs(pos.get("notional", 0)) for pos in positions.values())
        max_position = max(abs(pos.get("notional", 0)) for pos in positions.values()) if positions else 0
        concentration = (max_position / total_exposure) if total_exposure > 0 else 0

        # Correlation risk (simplified: diversity of symbols)
        unique_bases = set()
        for symbol in positions.keys():
            if "/" in symbol:
                base = symbol.split("/")[0]
                unique_bases.add(base)

        correlation_risk = max(0, 1.0 - len(unique_bases) / max(1, len(positions)))

        # Volatility score (placeholder - would need price history)
        volatility_score = min(1.0, len(positions) / 10.0)  # Simplified

        return concentration, correlation_risk, volatility_score

    def generate_heartbeat(self, pnl_snapshot: Dict, market_prices: Dict[str, float] = None) -> TradingMetrics:
        """
        Generiert kompakten Heartbeat mit allen relevanten Metriken.

        Args:
            pnl_snapshot: PnL snapshot from PnLTracker
            market_prices: Current market prices for positions

        Returns:
            TradingMetrics object
        """
        current_time = time.time()
        market_prices = market_prices or {}

        with self.lock:
            # Fill statistics
            fills_1h = self.fills.get_recent(3600)
            fills_5m = self.fills.get_recent(300)

            fill_sizes = [f["value"]["notional"] for f in fills_1h if isinstance(f["value"], dict)]
            avg_fill_size = statistics.mean(fill_sizes) if fill_sizes else 0.0

            # Slippage statistics
            slippage_stats_1h = self.slippage.get_stats(3600)

            # Execution success rate
            total_attempts = self.successful_orders + self.failed_orders
            success_rate = (self.successful_orders / total_attempts) if total_attempts > 0 else 1.0

            # API performance
            latency_stats = self.api_latencies.get_stats(600)  # 10 minutes

            # Error rates
            errors_1h = len(self.errors.get_recent(3600))
            retries_1h = len(self.retries.get_recent(3600))

            # Portfolio metrics
            gross_exposure = sum(abs(pos.get("notional", 0)) for pos in self.current_positions.values())
            net_exposure = sum(pos.get("notional", 0) for pos in self.current_positions.values())

            concentration, correlation_risk, volatility_score = self._calculate_portfolio_risk_score(
                self.current_positions
            )

            # Update session tracking
            equity_delta = pnl_snapshot.get("equity_delta", 0.0)
            self.update_session_equity(equity_delta)

            heartbeat = TradingMetrics(
                timestamp=current_time,

                # Budget & Positions
                budget_quote=pnl_snapshot.get("budget_available", 0.0),
                positions_open=len(self.current_positions),
                gross_exposure=gross_exposure,
                net_exposure=net_exposure,

                # PnL
                pnl_realized_session=pnl_snapshot.get("pnl_realized", 0.0),
                pnl_unrealized=pnl_snapshot.get("pnl_unrealized", 0.0),
                pnl_fees_session=pnl_snapshot.get("fees_quote", 0.0),
                equity_delta=equity_delta,

                # Performance
                session_high_equity=self.session_high_equity,
                session_drawdown_current=equity_delta - self.session_high_equity,
                session_drawdown_peak=self.session_peak_drawdown,

                # Activity
                fills_last_hour=len(fills_1h),
                fills_last_5min=len(fills_5m),
                avg_fill_size_quote=avg_fill_size,

                # Execution
                avg_slippage_bp_last_hour=slippage_stats_1h.get("mean", 0.0),
                worst_slippage_bp_last_hour=slippage_stats_1h.get("max", 0.0),
                execution_success_rate=success_rate,

                # Risk
                position_concentration_max=concentration,
                correlation_risk_score=correlation_risk,
                volatility_score_portfolio=volatility_score,

                # System
                api_latency_avg_ms=latency_stats.get("mean", 0.0),
                error_rate_last_hour=errors_1h / 3600.0,  # errors per second
                retry_rate_last_hour=retries_1h / 3600.0   # retries per second
            )

            self.last_heartbeat = current_time
            return heartbeat

class HeartbeatManager:
    """
    Zentraler Manager für Enhanced Heartbeat Telemetrie.
    Koordiniert verschiedene Datenquellen und emittiert kompakte, aussagekräftige Heartbeats.
    """

    def __init__(self, log_function: Callable = None, heartbeat_interval: float = 60.0):
        self.log_function = log_function or self._default_log
        self.heartbeat_interval = heartbeat_interval

        self.aggregator = TelemetryAggregator()
        self.lock = threading.RLock()

        # Auto-heartbeat thread
        self.heartbeat_thread = None
        self.running = False

        # Alert thresholds
        self.alert_thresholds = {
            "max_drawdown_pct": -5.0,        # Alert if drawdown > 5%
            "min_success_rate": 0.85,        # Alert if success rate < 85%
            "max_avg_latency_ms": 2000,      # Alert if latency > 2s
            "max_error_rate_per_hour": 10,   # Alert if > 10 errors/hour
            "max_slippage_bp": 50            # Alert if slippage > 0.5%
        }

        # Performance tracking
        self.heartbeat_history = deque(maxlen=100)

    def _default_log(self, event_type: str, **kwargs):
        """Default logging fallback"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[{event_type}] {kwargs}",
                   extra={'event_type': event_type, **kwargs})

    def start_auto_heartbeat(self):
        """Startet automatische Heartbeat-Emission"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return

        self.running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    def stop_auto_heartbeat(self):
        """Stoppt automatische Heartbeat-Emission"""
        self.running = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=5.0)

    def _heartbeat_loop(self):
        """Heartbeat-Loop für automatische Emission"""
        try:
            from services.shutdown_coordinator import get_shutdown_coordinator
            shutdown_coordinator = get_shutdown_coordinator()
        except ImportError:
            shutdown_coordinator = None

        while self.running:
            try:
                # Generate and emit heartbeat
                # Note: Requires PnL snapshot from external source
                self.emit_heartbeat({})  # Empty snapshot as fallback

                # Wait for next interval (shutdown-aware)
                if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=self.heartbeat_interval):
                    self.log_function("HEARTBEAT_SHUTDOWN", message="Shutdown requested, stopping heartbeat loop")
                    break
                elif not shutdown_coordinator:
                    # Fallback if shutdown coordinator not available
                    time.sleep(self.heartbeat_interval)

            except Exception as e:
                self.log_function("HEARTBEAT_ERROR", error=str(e))
                # Error recovery delay (shutdown-aware)
                if shutdown_coordinator and shutdown_coordinator.wait_for_shutdown(timeout=10.0):
                    break
                elif not shutdown_coordinator:
                    time.sleep(10)

    def record_trade_fill(self, symbol: str, side: str, qty: float, price: float, slippage_bp: float = 0.0):
        """Record a trade fill for telemetry"""
        self.aggregator.record_fill(symbol, side, qty, price, slippage_bp)

    def record_api_call(self, latency_ms: float, endpoint: str = "unknown"):
        """Record API call latency"""
        self.aggregator.record_api_latency(latency_ms, endpoint)

    def record_error(self, error_type: str, details: Dict = None):
        """Record an error"""
        self.aggregator.record_error(error_type, details)

    def record_retry(self, function_name: str, attempt: int, reason: str):
        """Record a retry attempt"""
        self.aggregator.record_retry(function_name, attempt, reason)

    def update_positions(self, positions: Dict[str, Dict]):
        """Update current positions"""
        self.aggregator.update_positions(positions)

    def update_market_health(self, trend: str = "sideways", volatility: str = "normal",
                           volume: str = "normal", fear_greed: float = 0.0):
        """Update market health indicators"""
        health = MarketHealthMetrics(
            overall_trend=trend,
            volatility_regime=volatility,
            volume_profile=volume,
            correlation_breakdown=0.0,  # Placeholder
            fear_greed_proxy=fear_greed,
            market_breadth=0.0  # Placeholder
        )
        self.aggregator.update_market_health(health)

    def emit_heartbeat(self, pnl_snapshot: Dict, market_prices: Dict[str, float] = None,
                      force: bool = False) -> TradingMetrics:
        """
        Emittiert Heartbeat mit allen relevanten Metriken.

        Args:
            pnl_snapshot: Current PnL snapshot
            market_prices: Current market prices
            force: Force emission even if interval hasn't passed

        Returns:
            Generated TradingMetrics
        """
        current_time = time.time()

        # Check if enough time has passed (unless forced)
        if not force and (current_time - self.aggregator.last_heartbeat) < self.heartbeat_interval:
            return None

        with self.lock:
            # Generate comprehensive heartbeat
            heartbeat = self.aggregator.generate_heartbeat(pnl_snapshot, market_prices)

            # Phase 3: Log portfolio_snapshot event
            try:
                from core.event_schemas import PortfolioSnapshot
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace

                # Build exposure dict from current positions
                exposure = {}
                for symbol, pos in self.aggregator.current_positions.items():
                    exposure[symbol] = pos.get('notional', 0)

                portfolio_snapshot = PortfolioSnapshot(
                    equity_total=pnl_snapshot.get('budget_available', 0) + pnl_snapshot.get('pnl_unrealized', 0),
                    cash_free=pnl_snapshot.get('budget_available', 0),
                    positions_count=len(self.aggregator.current_positions),
                    exposure=exposure,
                    realized_pnl=pnl_snapshot.get('pnl_realized', 0),
                    unrealized_pnl=pnl_snapshot.get('pnl_unrealized', 0)
                )

                with Trace():
                    log_event(DECISION_LOG(), "portfolio_snapshot", **portfolio_snapshot.model_dump())

            except Exception as e:
                # Don't fail heartbeat if logging fails
                import logging
                logger = logging.getLogger(__name__)
                logger.debug(f"Failed to log portfolio_snapshot: {e}")

            # Check for alerts
            alerts = self._check_alerts(heartbeat)

            # Create log payload
            log_payload = {
                **asdict(heartbeat),
                "session_duration_hours": (current_time - self.aggregator.session_start) / 3600,
                "alerts": alerts
            }

            # Round values for cleaner output
            for key, value in log_payload.items():
                if isinstance(value, float):
                    log_payload[key] = round(value, 4)

            # Emit heartbeat
            self.log_function("HEARTBEAT", **log_payload)

            # Store in history
            self.heartbeat_history.append(heartbeat)

            return heartbeat

    def _check_alerts(self, heartbeat: TradingMetrics) -> List[Dict]:
        """Prüft Heartbeat gegen Alert-Thresholds"""
        alerts = []

        # Drawdown alert
        if heartbeat.session_drawdown_current < self.alert_thresholds["max_drawdown_pct"]:
            alerts.append({
                "type": "HIGH_DRAWDOWN",
                "message": f"Session drawdown: {heartbeat.session_drawdown_current:.2f}%",
                "severity": "warning"
            })

        # Success rate alert
        if heartbeat.execution_success_rate < self.alert_thresholds["min_success_rate"]:
            alerts.append({
                "type": "LOW_SUCCESS_RATE",
                "message": f"Execution success rate: {heartbeat.execution_success_rate:.1%}",
                "severity": "warning"
            })

        # Latency alert
        if heartbeat.api_latency_avg_ms > self.alert_thresholds["max_avg_latency_ms"]:
            alerts.append({
                "type": "HIGH_LATENCY",
                "message": f"API latency: {heartbeat.api_latency_avg_ms:.0f}ms",
                "severity": "warning"
            })

        # Error rate alert
        error_count_per_hour = heartbeat.error_rate_last_hour * 3600
        if error_count_per_hour > self.alert_thresholds["max_error_rate_per_hour"]:
            alerts.append({
                "type": "HIGH_ERROR_RATE",
                "message": f"Error rate: {error_count_per_hour:.0f}/hour",
                "severity": "critical"
            })

        # Slippage alert
        if heartbeat.worst_slippage_bp_last_hour > self.alert_thresholds["max_slippage_bp"]:
            alerts.append({
                "type": "HIGH_SLIPPAGE",
                "message": f"Worst slippage: {heartbeat.worst_slippage_bp_last_hour:.1f}bp",
                "severity": "warning"
            })

        return alerts

    def get_performance_summary(self, hours: int = 24) -> Dict:
        """Gibt Performance-Zusammenfassung zurück"""
        cutoff_time = time.time() - (hours * 3600)
        recent_heartbeats = [h for h in self.heartbeat_history if h.timestamp >= cutoff_time]

        if not recent_heartbeats:
            return {"error": "no_recent_data", "hours": hours}

        # Aggregate statistics
        pnl_values = [h.equity_delta for h in recent_heartbeats]
        slippage_values = [h.avg_slippage_bp_last_hour for h in recent_heartbeats if h.avg_slippage_bp_last_hour > 0]

        summary = {
            "period_hours": hours,
            "heartbeats_count": len(recent_heartbeats),
            "pnl_summary": {
                "current": recent_heartbeats[-1].equity_delta if recent_heartbeats else 0,
                "peak": max(pnl_values) if pnl_values else 0,
                "trough": min(pnl_values) if pnl_values else 0,
                "volatility": statistics.stdev(pnl_values) if len(pnl_values) > 1 else 0
            },
            "execution_summary": {
                "avg_success_rate": statistics.mean([h.execution_success_rate for h in recent_heartbeats]),
                "avg_slippage_bp": statistics.mean(slippage_values) if slippage_values else 0,
                "total_fills": sum(h.fills_last_hour for h in recent_heartbeats),
                "avg_api_latency": statistics.mean([h.api_latency_avg_ms for h in recent_heartbeats])
            },
            "risk_summary": {
                "max_concentration": max(h.position_concentration_max for h in recent_heartbeats),
                "avg_positions": statistics.mean([h.positions_open for h in recent_heartbeats]),
                "max_gross_exposure": max(h.gross_exposure for h in recent_heartbeats)
            }
        }

        return summary

    def reset_session(self):
        """Reset session statistics"""
        with self.lock:
            self.aggregator.session_start = time.time()
            self.aggregator.session_high_equity = 0.0
            self.aggregator.session_peak_drawdown = 0.0
            self.aggregator.successful_orders = 0
            self.aggregator.failed_orders = 0
            self.aggregator.total_volume_quote = 0.0

# Global Heartbeat Manager Instance
_heartbeat_manager = None

def get_heartbeat_manager(log_function: Callable = None) -> HeartbeatManager:
    """Singleton Pattern für globalen Heartbeat Manager"""
    global _heartbeat_manager
    if _heartbeat_manager is None:
        _heartbeat_manager = HeartbeatManager(log_function)
    return _heartbeat_manager

# Convenience Functions
def record_fill(symbol: str, side: str, qty: float, price: float, slippage_bp: float = 0.0):
    """Record a trade fill"""
    manager = get_heartbeat_manager()
    manager.record_trade_fill(symbol, side, qty, price, slippage_bp)

def record_api_latency(latency_ms: float, endpoint: str = "unknown"):
    """Record API latency"""
    manager = get_heartbeat_manager()
    manager.record_api_call(latency_ms, endpoint)

def emit_heartbeat(pnl_snapshot: Dict, market_prices: Dict[str, float] = None) -> TradingMetrics:
    """Emit heartbeat with current metrics"""
    manager = get_heartbeat_manager()
    return manager.emit_heartbeat(pnl_snapshot, market_prices)

def start_auto_heartbeat(interval_seconds: float = 60.0):
    """Start automatic heartbeat emission"""
    manager = get_heartbeat_manager()
    manager.heartbeat_interval = interval_seconds
    manager.start_auto_heartbeat()

def get_performance_summary(hours: int = 24) -> Dict:
    """Get performance summary"""
    manager = get_heartbeat_manager()
    return manager.get_performance_summary(hours)
