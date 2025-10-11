# ml_gatekeeper.py
# V10-Style ML Gatekeeper für intelligente Budget-Skalierung ohne harte Blocks

import time
import threading
import numpy as np
from typing import Dict, Optional, Tuple, List, Union, Any, Callable
from dataclasses import dataclass, asdict
from collections import deque, defaultdict
from enum import Enum
import pickle
import json

class ScalingStrategy(Enum):
    """Verschiedene Scaling-Strategien"""
    LINEAR = "linear"           # Lineare Skalierung zwischen min/max
    EXPONENTIAL = "exponential" # Exponenzielle Skalierung
    THRESHOLD = "threshold"     # Threshold-basiert
    SIGMOID = "sigmoid"        # S-Curve Skalierung

@dataclass
class FeatureConfig:
    """Konfiguration für Feature-Extraction"""
    name: str
    source: str  # "market", "symbol", "portfolio", "time"
    weight: float = 1.0
    normalize: bool = True
    clip_range: Tuple[float, float] = (-3.0, 3.0)  # Z-score clipping
    description: str = ""

@dataclass
class GatekeeperConfig:
    """Konfiguration für ML Gatekeeper"""
    # Budget scaling parameters
    min_budget_multiplier: float = 0.3    # Minimum: 30% of base budget
    max_budget_multiplier: float = 1.5    # Maximum: 150% of base budget
    safe_budget_multiplier: float = 1.0   # Safe default: 100%

    # Confidence thresholds
    min_confidence: float = 0.1           # Below this: use safe_budget
    high_confidence: float = 0.8          # Above this: allow max scaling

    # Feature weights
    market_regime_weight: float = 0.3
    volatility_weight: float = 0.2
    trend_weight: float = 0.2
    volume_weight: float = 0.15
    symbol_performance_weight: float = 0.15

    # Scaling strategy
    scaling_strategy: ScalingStrategy = ScalingStrategy.SIGMOID
    update_frequency_seconds: float = 30.0

@dataclass
class MarketFeatures:
    """Market-basierte Features"""
    volatility_zscore: float = 0.0      # VIX-like volatility measure
    trend_strength: float = 0.0         # -1 (strong down) to +1 (strong up)
    market_regime: str = "neutral"      # "bull", "bear", "neutral", "volatile"
    volume_anomaly: float = 0.0         # Volume vs average
    correlation_breakdown: float = 0.0   # Market correlation breakdown indicator

@dataclass
class SymbolFeatures:
    """Symbol-spezifische Features"""
    relative_strength: float = 0.0      # Performance vs market
    momentum_score: float = 0.0         # Technical momentum
    mean_reversion: float = 0.0         # Distance from mean
    liquidity_score: float = 0.0        # Liquidity measure
    news_sentiment: float = 0.0         # News sentiment if available

@dataclass
class PortfolioFeatures:
    """Portfolio-basierte Features"""
    current_drawdown: float = 0.0       # Current drawdown %
    win_rate_recent: float = 0.5        # Recent win rate
    sharpe_estimate: float = 0.0        # Running Sharpe ratio
    position_concentration: float = 0.0  # Portfolio concentration risk
    available_buying_power: float = 1.0  # Available buying power ratio

@dataclass
class ScalingDecision:
    """Gatekeeper Decision mit Audit-Trail"""
    timestamp: float
    symbol: str
    base_budget: float
    scaled_budget: float
    multiplier: float
    confidence: float
    features_used: Dict[str, float]
    scaling_reason: str
    model_version: str = "heuristic_v1"

class FeatureExtractor:
    """
    Extrahiert Features für ML-basierte Entscheidungen.
    Kann sowohl heuristische als auch ML-basierte Features bereitstellen.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.market_history = deque(maxlen=1000)
        self.symbol_history = defaultdict(lambda: deque(maxlen=500))
        self.portfolio_history = deque(maxlen=1000)

        # Feature normalization parameters
        self.feature_stats = defaultdict(lambda: {"mean": 0.0, "std": 1.0, "count": 0})

    def update_market_data(self, market_features: MarketFeatures):
        """Aktualisiert Market-Features"""
        with self.lock:
            market_features.timestamp = time.time()
            self.market_history.append(market_features)

    def update_symbol_data(self, symbol: str, symbol_features: SymbolFeatures):
        """Aktualisiert Symbol-Features"""
        with self.lock:
            symbol_features.timestamp = time.time()
            self.symbol_history[symbol].append(symbol_features)

    def update_portfolio_data(self, portfolio_features: PortfolioFeatures):
        """Aktualisiert Portfolio-Features"""
        with self.lock:
            portfolio_features.timestamp = time.time()
            self.portfolio_history.append(portfolio_features)

    def _calculate_volatility_regime(self, lookback_periods: int = 20) -> Tuple[float, str]:
        """Berechnet Volatility-Regime basierend auf Historie"""
        if len(self.market_history) < lookback_periods:
            return 0.0, "unknown"

        recent_vol = [m.volatility_zscore for m in list(self.market_history)[-lookback_periods:]]
        avg_vol = np.mean(recent_vol)
        vol_trend = np.mean(np.diff(recent_vol)) if len(recent_vol) > 1 else 0.0

        if avg_vol > 2.0:
            regime = "high_vol"
        elif avg_vol < -1.0:
            regime = "low_vol"
        elif vol_trend > 0.5:
            regime = "vol_expanding"
        elif vol_trend < -0.5:
            regime = "vol_contracting"
        else:
            regime = "normal_vol"

        return avg_vol, regime

    def _calculate_trend_strength(self, symbol: str = None, lookback_periods: int = 10) -> float:
        """Berechnet Trend-Stärke für Symbol oder Market"""
        if symbol:
            if symbol not in self.symbol_history or len(self.symbol_history[symbol]) < lookback_periods:
                return 0.0
            recent_data = list(self.symbol_history[symbol])[-lookback_periods:]
            trend_scores = [d.momentum_score for d in recent_data]
        else:
            if len(self.market_history) < lookback_periods:
                return 0.0
            recent_data = list(self.market_history)[-lookback_periods:]
            trend_scores = [d.trend_strength for d in recent_data]

        if not trend_scores:
            return 0.0

        # Weighted average mit mehr Gewicht auf recent data
        weights = np.linspace(0.5, 1.0, len(trend_scores))
        weighted_avg = np.average(trend_scores, weights=weights)

        return float(np.clip(weighted_avg, -1.0, 1.0))

    def _update_feature_stats(self, feature_name: str, value: float):
        """Aktualisiert laufende Statistiken für Feature-Normalisierung"""
        stats = self.feature_stats[feature_name]
        count = stats["count"]
        old_mean = stats["mean"]

        # Online mean and variance calculation (Welford's algorithm)
        new_count = count + 1
        delta = value - old_mean
        new_mean = old_mean + delta / new_count

        if count > 0:
            delta2 = value - new_mean
            new_m2 = stats.get("m2", 0) + delta * delta2
            new_var = new_m2 / new_count if new_count > 1 else 0
            new_std = np.sqrt(new_var) if new_var > 0 else 1.0
        else:
            new_std = 1.0

        stats.update({
            "count": new_count,
            "mean": new_mean,
            "std": max(new_std, 0.01),  # Prevent division by zero
            "m2": stats.get("m2", 0) + delta * (value - new_mean) if count > 0 else 0
        })

    def _normalize_feature(self, feature_name: str, value: float, clip_range: Tuple[float, float] = (-3, 3)) -> float:
        """Normalisiert Feature zu Z-Score mit Clipping"""
        self._update_feature_stats(feature_name, value)
        stats = self.feature_stats[feature_name]

        z_score = (value - stats["mean"]) / stats["std"]
        return float(np.clip(z_score, clip_range[0], clip_range[1]))

    def extract_features(self, symbol: str, context: Dict = None) -> Dict[str, float]:
        """
        Extrahiert alle Features für Gatekeeper-Entscheidung.

        Args:
            symbol: Trading symbol
            context: Additional context (current price, etc.)

        Returns:
            Dict mit normalisierten Features
        """
        context = context or {}
        features = {}

        with self.lock:
            # Market features
            if self.market_history:
                latest_market = self.market_history[-1]
                features["market_volatility"] = self._normalize_feature("market_vol", latest_market.volatility_zscore)
                features["market_trend"] = self._normalize_feature("market_trend", latest_market.trend_strength)
                features["volume_anomaly"] = self._normalize_feature("volume_anomaly", latest_market.volume_anomaly)

                # Regime features (categorical -> numeric encoding)
                regime_score = {
                    "bull": 1.0, "bear": -1.0, "neutral": 0.0,
                    "volatile": -0.5, "unknown": 0.0
                }.get(latest_market.market_regime, 0.0)
                features["market_regime"] = regime_score
            else:
                features.update({
                    "market_volatility": 0.0,
                    "market_trend": 0.0,
                    "volume_anomaly": 0.0,
                    "market_regime": 0.0
                })

            # Symbol features
            if symbol in self.symbol_history and self.symbol_history[symbol]:
                latest_symbol = self.symbol_history[symbol][-1]
                features["symbol_momentum"] = self._normalize_feature("symbol_momentum", latest_symbol.momentum_score)
                features["relative_strength"] = self._normalize_feature("relative_strength", latest_symbol.relative_strength)
                features["mean_reversion"] = self._normalize_feature("mean_reversion", latest_symbol.mean_reversion)
                features["liquidity_score"] = self._normalize_feature("liquidity", latest_symbol.liquidity_score)
            else:
                features.update({
                    "symbol_momentum": 0.0,
                    "relative_strength": 0.0,
                    "mean_reversion": 0.0,
                    "liquidity_score": 0.0
                })

            # Portfolio features
            if self.portfolio_history:
                latest_portfolio = self.portfolio_history[-1]
                features["drawdown"] = self._normalize_feature("drawdown", latest_portfolio.current_drawdown)
                features["win_rate"] = self._normalize_feature("win_rate", latest_portfolio.win_rate_recent - 0.5)  # Center around 0
                features["sharpe"] = self._normalize_feature("sharpe", latest_portfolio.sharpe_estimate)
                features["concentration"] = self._normalize_feature("concentration", latest_portfolio.position_concentration)
                features["buying_power"] = self._normalize_feature("buying_power", latest_portfolio.available_buying_power - 0.5)
            else:
                features.update({
                    "drawdown": 0.0,
                    "win_rate": 0.0,
                    "sharpe": 0.0,
                    "concentration": 0.0,
                    "buying_power": 0.0
                })

            # Derived features
            vol_regime_score, _ = self._calculate_volatility_regime()
            features["volatility_regime"] = self._normalize_feature("vol_regime", vol_regime_score)

            trend_strength = self._calculate_trend_strength(symbol)
            features["trend_strength"] = trend_strength  # Already normalized

            # Time-based features
            hour_of_day = time.gmtime().tm_hour
            features["hour_sin"] = np.sin(2 * np.pi * hour_of_day / 24)
            features["hour_cos"] = np.cos(2 * np.pi * hour_of_day / 24)

            day_of_week = time.gmtime().tm_wday
            features["day_sin"] = np.sin(2 * np.pi * day_of_week / 7)
            features["day_cos"] = np.cos(2 * np.pi * day_of_week / 7)

            return features

class MLGatekeeper:
    """
    ML-basierter Gatekeeper für intelligente Budget-Skalierung.
    Unterstützt sowohl heuristische als auch ML-basierte Modelle.
    """

    def __init__(self, config: GatekeeperConfig = None, model_path: str = None):
        self.config = config or GatekeeperConfig()
        self.model = None
        self.model_path = model_path

        # Feature extraction
        self.feature_extractor = FeatureExtractor()

        # Decision tracking
        self.decision_history = deque(maxlen=1000)
        self.performance_tracking = defaultdict(list)

        # Thread safety
        self.lock = threading.RLock()

        # Load model if path provided
        if model_path:
            self.load_model(model_path)

        # Default heuristic weights
        self.heuristic_weights = {
            "market_trend": 0.25,
            "symbol_momentum": 0.20,
            "volatility_regime": -0.15,  # Negative: reduce size in high vol
            "win_rate": 0.15,
            "drawdown": -0.10,            # Negative: reduce size in drawdown
            "relative_strength": 0.10,
            "liquidity_score": 0.05,
            "buying_power": 0.10
        }

    def load_model(self, model_path: str) -> bool:
        """
        Lädt ML-Modell von Datei.

        Args:
            model_path: Pfad zur Modell-Datei

        Returns:
            True if successful
        """
        try:
            with open(model_path, 'rb') as f:
                self.model = pickle.load(f)
            self.model_path = model_path
            return True
        except Exception as e:
            print(f"Failed to load model: {e}")
            return False

    def save_model(self, model_path: str) -> bool:
        """
        Speichert aktuelles Modell.

        Args:
            model_path: Pfad für Modell-Datei

        Returns:
            True if successful
        """
        if self.model is None:
            return False

        try:
            with open(model_path, 'wb') as f:
                pickle.dump(self.model, f)
            self.model_path = model_path
            return True
        except Exception as e:
            print(f"Failed to save model: {e}")
            return False

    def _calculate_heuristic_score(self, features: Dict[str, float]) -> Tuple[float, Dict]:
        """
        Berechnet heuristischen Score basierend auf Features.

        Args:
            features: Extracted features

        Returns:
            (score, contribution_details)
        """
        score = 0.0
        contributions = {}

        for feature_name, weight in self.heuristic_weights.items():
            if feature_name in features:
                contribution = features[feature_name] * weight
                score += contribution
                contributions[feature_name] = contribution

        # Apply sigmoid to get score between 0 and 1
        sigmoid_score = 1.0 / (1.0 + np.exp(-score))

        return float(sigmoid_score), contributions

    def _calculate_ml_score(self, features: Dict[str, float]) -> Tuple[float, Dict]:
        """
        Berechnet ML-basierten Score.

        Args:
            features: Extracted features

        Returns:
            (score, model_details)
        """
        if self.model is None:
            return self._calculate_heuristic_score(features)

        try:
            # Prepare feature vector
            feature_vector = np.array([features.get(name, 0.0) for name in sorted(features.keys())])
            feature_vector = feature_vector.reshape(1, -1)

            # Get prediction
            if hasattr(self.model, 'predict_proba'):
                # Classifier with probability output
                probabilities = self.model.predict_proba(feature_vector)[0]
                score = float(probabilities[1] if len(probabilities) > 1 else probabilities[0])
            else:
                # Regressor
                prediction = self.model.predict(feature_vector)[0]
                score = float(np.clip(prediction, 0.0, 1.0))

            model_details = {
                "model_type": type(self.model).__name__,
                "features_used": len(feature_vector[0]),
                "raw_prediction": score
            }

            return score, model_details

        except Exception as e:
            print(f"ML model prediction failed: {e}")
            return self._calculate_heuristic_score(features)

    def _apply_scaling_strategy(self, score: float, confidence: float) -> float:
        """
        Wendet Scaling-Strategie an um Budget-Multiplikator zu bestimmen.

        Args:
            score: Model score (0-1)
            confidence: Confidence in score (0-1)

        Returns:
            Budget multiplier
        """
        # Apply confidence weighting
        if confidence < self.config.min_confidence:
            return self.config.safe_budget_multiplier

        # Scale based on strategy
        if self.config.scaling_strategy == ScalingStrategy.LINEAR:
            multiplier = self.config.min_budget_multiplier + \
                        (self.config.max_budget_multiplier - self.config.min_budget_multiplier) * score

        elif self.config.scaling_strategy == ScalingStrategy.EXPONENTIAL:
            # Exponential scaling: more aggressive for high scores
            exp_score = (score ** 2)
            multiplier = self.config.min_budget_multiplier + \
                        (self.config.max_budget_multiplier - self.config.min_budget_multiplier) * exp_score

        elif self.config.scaling_strategy == ScalingStrategy.THRESHOLD:
            # Threshold-based: only scale significantly above certain threshold
            if score > 0.7:
                multiplier = self.config.max_budget_multiplier
            elif score > 0.4:
                multiplier = self.config.safe_budget_multiplier
            else:
                multiplier = self.config.min_budget_multiplier

        else:  # SIGMOID (default)
            # S-curve scaling: smooth transition with more conservative scaling
            centered_score = (score - 0.5) * 4  # Center around 0, expand range
            sigmoid_adjusted = 1.0 / (1.0 + np.exp(-centered_score))
            multiplier = self.config.min_budget_multiplier + \
                        (self.config.max_budget_multiplier - self.config.min_budget_multiplier) * sigmoid_adjusted

        # Apply confidence scaling
        confidence_adjusted_multiplier = (
            self.config.safe_budget_multiplier * (1 - confidence) +
            multiplier * confidence
        )

        return float(np.clip(confidence_adjusted_multiplier,
                           self.config.min_budget_multiplier,
                           self.config.max_budget_multiplier))

    def evaluate_trade_opportunity(self, symbol: str, base_budget: float,
                                 context: Dict = None) -> ScalingDecision:
        """
        Evaluiert Trade-Gelegenheit und skaliert Budget entsprechend.

        Args:
            symbol: Trading symbol
            base_budget: Base budget before scaling
            context: Additional context

        Returns:
            ScalingDecision mit allen Details
        """
        timestamp = time.time()
        context = context or {}

        with self.lock:
            # Extract features
            features = self.feature_extractor.extract_features(symbol, context)

            # Calculate score and confidence
            if self.model is not None:
                score, score_details = self._calculate_ml_score(features)
                confidence = min(1.0, len(features) / 12.0)  # Simple confidence based on feature availability
                model_version = f"ml_model_{getattr(self.model, '__class__', 'unknown').__name__}"
            else:
                score, score_details = self._calculate_heuristic_score(features)
                confidence = 0.8  # Higher confidence in heuristic model
                model_version = "heuristic_v1"

            # Apply scaling strategy
            multiplier = self._apply_scaling_strategy(score, confidence)
            scaled_budget = base_budget * multiplier

            # Determine scaling reason
            if score > 0.7:
                reason = "high_confidence_positive"
            elif score > 0.5:
                reason = "moderate_positive"
            elif score > 0.3:
                reason = "slight_positive"
            else:
                reason = "defensive_scaling"

            if confidence < self.config.min_confidence:
                reason += "_low_confidence"

            # Create decision
            decision = ScalingDecision(
                timestamp=timestamp,
                symbol=symbol,
                base_budget=base_budget,
                scaled_budget=scaled_budget,
                multiplier=multiplier,
                confidence=confidence,
                features_used=features.copy(),
                scaling_reason=reason,
                model_version=model_version
            )

            # Track decision
            self.decision_history.append(decision)

            return decision

    def update_market_regime(self, volatility_zscore: float, trend_strength: float,
                           regime: str = "neutral", volume_anomaly: float = 0.0):
        """Update market-level features"""
        features = MarketFeatures(
            volatility_zscore=volatility_zscore,
            trend_strength=trend_strength,
            market_regime=regime,
            volume_anomaly=volume_anomaly
        )
        self.feature_extractor.update_market_data(features)

    def update_symbol_metrics(self, symbol: str, momentum_score: float,
                            relative_strength: float = 0.0, liquidity_score: float = 1.0):
        """Update symbol-specific features"""
        features = SymbolFeatures(
            momentum_score=momentum_score,
            relative_strength=relative_strength,
            liquidity_score=liquidity_score
        )
        self.feature_extractor.update_symbol_data(symbol, features)

    def update_portfolio_state(self, current_drawdown: float, win_rate: float,
                             sharpe_estimate: float = 0.0, buying_power_ratio: float = 1.0):
        """Update portfolio-level features"""
        features = PortfolioFeatures(
            current_drawdown=current_drawdown,
            win_rate_recent=win_rate,
            sharpe_estimate=sharpe_estimate,
            available_buying_power=buying_power_ratio
        )
        self.feature_extractor.update_portfolio_data(features)

    def get_recent_decisions(self, hours: int = 24) -> List[ScalingDecision]:
        """Gibt recent decisions zurück"""
        cutoff_time = time.time() - (hours * 3600)
        with self.lock:
            return [d for d in self.decision_history if d.timestamp >= cutoff_time]

    def get_performance_stats(self) -> Dict:
        """Gibt Performance-Statistiken zurück"""
        recent_decisions = self.get_recent_decisions(24)

        if not recent_decisions:
            return {"decisions_count": 0}

        multipliers = [d.multiplier for d in recent_decisions]
        confidences = [d.confidence for d in recent_decisions]

        stats = {
            "decisions_count": len(recent_decisions),
            "avg_multiplier": np.mean(multipliers),
            "std_multiplier": np.std(multipliers),
            "min_multiplier": np.min(multipliers),
            "max_multiplier": np.max(multipliers),
            "avg_confidence": np.mean(confidences),
            "model_version": recent_decisions[-1].model_version if recent_decisions else "unknown"
        }

        # Scaling reason distribution
        reason_counts = {}
        for decision in recent_decisions:
            reason_counts[decision.scaling_reason] = reason_counts.get(decision.scaling_reason, 0) + 1

        stats["scaling_reasons"] = reason_counts
        return stats

# Global ML Gatekeeper Instance
_ml_gatekeeper = None

def get_ml_gatekeeper(config: GatekeeperConfig = None, model_path: str = None) -> MLGatekeeper:
    """Singleton Pattern für globalen ML Gatekeeper"""
    global _ml_gatekeeper
    if _ml_gatekeeper is None:
        _ml_gatekeeper = MLGatekeeper(config, model_path)
    return _ml_gatekeeper

# Convenience Functions
def scale_budget(symbol: str, base_budget: float, context: Dict = None) -> Tuple[float, ScalingDecision]:
    """
    Scale budget using ML gatekeeper.

    Args:
        symbol: Trading symbol
        base_budget: Base budget amount
        context: Additional context

    Returns:
        (scaled_budget, scaling_decision)
    """
    gatekeeper = get_ml_gatekeeper()
    decision = gatekeeper.evaluate_trade_opportunity(symbol, base_budget, context)
    return decision.scaled_budget, decision

def update_market_data(volatility_zscore: float, trend_strength: float, regime: str = "neutral"):
    """Update market-level data"""
    gatekeeper = get_ml_gatekeeper()
    gatekeeper.update_market_regime(volatility_zscore, trend_strength, regime)

def update_portfolio_metrics(drawdown_pct: float, win_rate: float, sharpe: float = 0.0):
    """Update portfolio metrics"""
    gatekeeper = get_ml_gatekeeper()
    gatekeeper.update_portfolio_state(drawdown_pct, win_rate, sharpe)