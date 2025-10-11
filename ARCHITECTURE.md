# Trading Bot - Clean Architecture

## 📁 Projektstruktur

### Root (Minimal - nur Entry Points)
```
├── main.py                 # Haupteinstiegspunkt
├── config.py               # Zentrale Konfiguration
├── requirements.txt        # Python Dependencies
└── requirements-dev.txt    # Development Dependencies
```

## 🏗️ Architektur-Ebenen

### 1. Core Layer (`core/`)
Fundamentale Funktionalität ohne Business-Logic:

#### `core/logging/` - Logging Infrastructure
- `logger.py` - JsonlLogger, Decision ID Generation
- `logger_setup.py` - Logger Configuration
- `loggingx.py` - Extended Logging
- `log_manager.py` - Log File Management
- `adaptive_logger.py` - Adaptive Logging
- `debug_tracer.py` - Debug Tracing System

#### `core/utils/` - Utilities & Helpers
- `utils.py` - General Utilities
- `helpers_filters.py` - Market Filters & Helpers
- `order_flow.py` - Order Flow Processing
- `pnl.py` - PnL Tracking
- `telemetry.py` - Telemetry & Metrics
- `heartbeat_telemetry.py` - Heartbeat System
- `numpy_fix.py` - NumPy Compatibility

#### `core/portfolio/` - Portfolio & Risk
- `portfolio.py` - Portfolio Manager
- `risk_guards.py` - Risk Guards (ATR, Trailing)
- `trade_analyzer.py` - Trade Analysis

#### `core/events/` - Event System
- `event_bus.py` - Event Bus (Pub/Sub)

### 2. Business Logic Layer

#### `engine/` - Trading Engine (Refactored)
Pure Orchestration Layer:
- `engine.py` - Main Engine (768 lines, orchestration only)
- `buy_decision.py` - Buy Signal Evaluation & Execution
- `exit_handler.py` - Exit Processing & Fill Handling
- `position_manager.py` - Position Management & Trailing Stops
- `monitoring.py` - Performance Metrics & Statistics
- `engine_config.py` - Engine Configuration

#### `trading/` - Trading Functions (Refactored)
Order Management & Execution:
- `orders.py` - Order Placement (Limit, IOC, Market)
- `settlement.py` - Settlement & Balance Management
- `helpers.py` - Trading Helpers
- `orderbook.py` - Orderbook Analysis & Depth Sweep
- `portfolio_reset.py` - Portfolio Reset Logic

#### `services/` - Service Layer (Drops 1-5)
Business Services:
- Drop 1: PnL Service
- Drop 2: Trailing Stops & Signals
- Drop 3: Exchange Adapter & Orders
- Drop 4: Exit Management & Market Data
- Drop 5: Buy Signals & Market Guards

#### `signals/` - Signal Processing
Signal Generation & Processing:
- Drop Trigger System
- Rolling Windows
- Signal Confirmation (Stabilizer)

#### `adapters/` - Exchange Adapters
Exchange Integration Layer:
- ExchangeAdapter
- MockExchange for Testing

### 3. Integration Layer

#### `integrations/telegram/` - Telegram Bot
External Integration:
- `telegram_commands.py` - Command Handler
- `telegram_notify.py` - Notification Service
- `telegram_service_adapter.py` - Service Adapter

### 4. ML Layer

#### `ml/` - Machine Learning
- `ml_gatekeeper.py` - ML-based Decision Gatekeeper

### 5. Utilities

#### `scripts/` - Utility Scripts
Development & Maintenance Scripts:
- `add_buy_flow_logging.py` - Add Buy Flow Logging
- `config_lint.py` - Configuration Validation
- `extract_exchange_slice_configured.py` - Exchange Extraction

#### `backup_legacy/` - Legacy Code Backup
Archived monolithic code (pre-refactoring):
- `engine_legacy.py` (2011 lines) → `engine/` package
- `trading_legacy.py` (1500+ lines) → `trading/` package

#### `MDs/` - Documentation
Project Documentation (Markdown files)

## 📊 Refactoring Übersicht

### Engine: 2011 → 768 Zeilen + Handler
```
engine_legacy.py (2011 Zeilen, monolithisch)
    ↓
engine/ (modularer, 6 Dateien)
├── engine.py (768 Zeilen - reine Orchestrierung)
├── buy_decision.py (Buy Logic)
├── exit_handler.py (Exit Logic)
├── position_manager.py (Position Management)
├── monitoring.py (Metrics)
└── engine_config.py (Config)
```

### Trading: 1500+ → 5 spezialisierte Module
```
trading_legacy.py (1500+ Zeilen, monolithisch)
    ↓
trading/ (modularer, 5 Dateien)
├── orders.py (Order Placement)
├── settlement.py (Settlement)
├── helpers.py (Helpers)
├── orderbook.py (Orderbook Analysis)
└── portfolio_reset.py (Reset Logic)
```

## 🎯 Design Principles

1. **Separation of Concerns** - Jede Datei hat eine klare Verantwortung
2. **Clean Architecture** - Abhängigkeiten zeigen nach innen
3. **Modularity** - Wiederverwendbare, testbare Module
4. **Minimal Root** - Nur Entry Points im Root-Verzeichnis
5. **Package Structure** - Logische Gruppierung mit `__init__.py`

## 📈 Statistik

- **Root**: 2 Python-Dateien (main.py, config.py)
- **core/**: 17 Python-Dateien in 4 Subpackages
- **Business Logic**: 29 Python-Dateien
- **Integrations**: 3 Python-Dateien
- **ML**: 1 Python-Datei
- **Scripts**: 3 Python-Dateien

**Total**: ~60 Python-Dateien, sauber organisiert in 13 logischen Packages

## 🚀 Usage

### Import Examples

```python
# Core Logging
from core.logging import JsonlLogger, new_decision_id, get_adaptive_logger

# Core Utils
from core.utils import PnLTracker, RollingStats, heartbeat_emit

# Core Portfolio
from core.portfolio import PortfolioManager, atr_stop_hit

# Core Events
from core.events import subscribe, publish

# Engine
from engine import TradingEngine, EngineConfig

# Trading
from trading import place_limit_ioc_buy, refresh_budget_from_exchange

# Services
from services import PnLService, BuySignalService, ExitManager

# Integrations
from integrations.telegram import TelegramServiceAdapter
```

## 📝 Migration Notes

- Alle Legacy-Code sicher in `backup_legacy/` gesichert
- Alle Funktionalität vollständig migriert
- Keine Platzhalter oder TODOs mehr
- Alle Module syntax-validiert
- Package `__init__.py` für saubere Exports

---
**Refactoring abgeschlossen**: 2025-10-11
