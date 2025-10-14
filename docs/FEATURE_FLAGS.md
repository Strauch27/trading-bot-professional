# Feature Flags Reference

**Version**: 1.0
**Date**: 2025-10-14
**Branch**: feat/order-flow-hardening

---

## 📋 Overview

This document provides a comprehensive reference for all feature flags in the trading bot configuration. Feature flags allow for gradual rollout, A/B testing, and safe deployment of new features.

---

## 🎯 Memory Management & Performance

### ENABLE_COALESCING
**Location**: `services/market_data.py`
**Type**: Boolean
**Default**: `True`
**Description**: Enables request coalescing to deduplicate concurrent API calls for the same data.

**Impact**:
- ✅ Reduces API calls by ~80-95%
- ✅ Prevents duplicate work across threads
- ✅ Improves response times (10-50ms → <1ms for cached requests)

**Rollback**: Set to `False` to disable coalescing (no breaking changes)

---

### ENABLE_RATE_LIMITING
**Location**: `services/market_data.py`
**Type**: Boolean
**Default**: `True`
**Description**: Enables token bucket rate limiting for API requests.

**Impact**:
- ✅ Prevents API bans from exceeding rate limits (Bybit: 120 req/s)
- ✅ Automatic throttling under high load
- ⚠️ May introduce small delays (~10-50ms) when bucket empty

**Rollback**: Set to `False` to disable rate limiting (risk of API bans)

**Monitoring**:
- Track `throttle_rate` metric (should be <10%)
- Alert if `throttle_rate` > 20% (indicates capacity issues)

---

### ENABLE_SOFT_TTL_CACHE
**Location**: `services/market_data.py`
**Type**: Boolean
**Default**: `True` (implicit)
**Description**: Enables soft-TTL caching with background refresh for stale data.

**Impact**:
- ✅ Serves stale data while refreshing in background
- ✅ Reduces latency spikes from cache misses
- ✅ Improves availability during API slowdowns

**Metrics**:
- `cache_hit_rate`: Target >70%
- `stale_hit_rate`: Should be <30%

---

## 🛡️ Order Flow Hardening

### ENABLE_COID_MANAGER
**Location**: `config.py` (line 162)
**Type**: Boolean
**Default**: `True`
**Description**: Enables Client Order ID (COID) idempotency manager with persistent state.

**Impact**:
- ✅ Prevents duplicate order submissions
- ✅ Persistent state across restarts
- ✅ Automatic cleanup of terminal states

**Dependencies**: Requires writable `state/coid_registry.json`

**Rollback**: Set to `False` (⚠️ risk of duplicate orders)

---

### ENABLE_STARTUP_RECONCILE
**Location**: `config.py` (line 163)
**Type**: Boolean
**Default**: `True`
**Description**: Reconciles pending COIDs with exchange state on startup.

**Impact**:
- ✅ Recovers from crashes without manual intervention
- ✅ Synchronizes local state with exchange
- ⚠️ Adds ~2-5s to startup time

**Rollback**: Safe to disable if startup speed critical

---

### ENABLE_ENTRY_SLIPPAGE_GUARD
**Location**: `config.py` (line 166)
**Type**: Boolean
**Default**: `True`
**Description**: Validates entry slippage against expected price.

**Impact**:
- ✅ Blocks entries with excessive slippage (>15 bps default)
- ✅ Protects against flash crashes and wick entries
- ⚠️ May reduce fill rate by ~5-10% in volatile markets

**Rollback**: Set to `False` if fill rate drops too much

---

### USE_FIRST_FILL_TS_FOR_TTL
**Location**: `config.py` (line 170)
**Type**: Boolean
**Default**: `True`
**Description**: Uses `first_fill_ts` instead of order submission time for TTL calculation.

**Impact**:
- ✅ More accurate position age tracking
- ✅ Prevents premature TTL exits from slow fills
- ℹ️ No downside

**Rollback**: Not recommended (would make TTL less accurate)

---

### ENABLE_SYMBOL_LOCKS
**Location**: `config.py` (line 173)
**Type**: Boolean
**Default**: `True`
**Description**: Enables per-symbol RLock for thread-safe portfolio operations.

**Impact**:
- ✅ Prevents race conditions in multi-threaded environments
- ✅ Ensures consistent portfolio state
- ⚠️ Serializes operations on same symbol (acceptable)

**Rollback**: Set to `False` only in single-threaded mode

---

### ENABLE_SPREAD_GUARD_ENTRY
**Location**: `config.py` (line 176)
**Type**: Boolean
**Default**: `True`
**Description**: Blocks buy orders when bid-ask spread exceeds threshold.

**Impact**:
- ✅ Prevents entries in illiquid markets
- ✅ Reduces adverse selection
- ⚠️ May reduce opportunities in thin markets

**Threshold**: `MAX_SPREAD_BPS_ENTRY` (default: 10 bps)

**Rollback**: Set to `False` if too restrictive

---

### ENABLE_DEPTH_GUARD_ENTRY
**Location**: `config.py` (line 177)
**Type**: Boolean
**Default**: `True`
**Description**: Blocks buy orders when orderbook depth insufficient.

**Impact**:
- ✅ Prevents entries in thin markets
- ✅ Reduces slippage risk
- ⚠️ May reduce fill rate in low-liquidity symbols

**Threshold**: `DEPTH_MIN_NOTIONAL_USD` (default: 200 USD)

**Rollback**: Set to `False` or reduce threshold

---

### ENABLE_CONSOLIDATED_ENTRY_GUARDS
**Location**: `config.py` (line 181)
**Type**: Boolean
**Default**: `True`
**Description**: Uses consolidated `evaluate_all_entry_guards()` for unified guard evaluation.

**Impact**:
- ✅ Single evaluation point for all guards
- ✅ Consistent guard logic
- ✅ Better logging and debugging

**Rollback**: Not recommended (no downside)

---

### ENABLE_FILL_TELEMETRY
**Location**: `config.py` (line 184)
**Type**: Boolean
**Default**: `True`
**Description**: Tracks comprehensive fill metrics (rates, latency, slippage).

**Impact**:
- ✅ Detailed performance insights
- ✅ P50/P95/P99 latency tracking
- ✅ Fill rate by order type
- ⚠️ Small memory overhead (~10MB for 10k orders)

**Threshold**: `FILL_TELEMETRY_MAX_HISTORY` (default: 10000)

**Rollback**: Set to `False` to disable tracking

---

### ENABLE_CONSOLIDATED_EXITS
**Location**: `config.py` (line 190)
**Type**: Boolean
**Default**: `True`
**Description**: Uses unified exit evaluation (ATR, trailing, profit targets).

**Impact**:
- ✅ Single evaluation point for all exit conditions
- ✅ Consistent exit logic
- ✅ Better logging

**Rollback**: Not recommended (no downside)

---

### ENABLE_ORDER_FLOW_HARDENING
**Location**: `config.py` (line 193)
**Type**: Boolean (Master Switch)
**Default**: `True`
**Description**: Master switch for all order flow hardening features.

**Impact**:
- ✅ Enables all phases (2-12) when `True`
- ⚠️ Disabling this flag disables ALL hardening features

**Rollback**: Set to `False` to fully disable order flow hardening

---

## 🎨 Terminal UI

### ENABLE_RICH_LOGGING
**Location**: `config.py` (line 368)
**Type**: Boolean
**Default**: `True`
**Description**: Uses Rich Console for colored structured logging output.

**Impact**:
- ✅ Beautiful colored terminal output
- ✅ Structured log formatting
- ✅ Better readability
- ⚠️ Requires `rich` library (fallback to plain text if missing)

**Rollback**: Set to `False` for plain text logging

---

### ENABLE_LIVE_MONITORS
**Location**: `config.py` (line 565)
**Type**: Boolean
**Default**: `True`
**Description**: Enables Rich Live Monitors (Heartbeat, Drop, Portfolio).

**Impact**:
- ✅ Real-time terminal dashboards
- ✅ Live metrics visualization
- ⚠️ Requires `rich` library

**Rollback**: Set to `False` to disable live monitors

---

### ENABLE_LIVE_HEARTBEAT
**Location**: `config.py` (line 566)
**Type**: Boolean
**Default**: `True`
**Description**: Shows live system + API + fill metrics panel.

**Impact**:
- ✅ Real-time system health monitoring
- ✅ API performance metrics
- ✅ Fill telemetry visualization

**Rollback**: Set to `False` to disable heartbeat panel

---

### ENABLE_LIVE_DASHBOARD
**Location**: `config.py` (line 567)
**Type**: Boolean
**Default**: `True`
**Description**: Combined dashboard with all monitors.

**Impact**:
- ✅ All-in-one monitoring view
- ✅ Live updates every 2 seconds
- ⚠️ Higher CPU usage (negligible)

**Rollback**: Set to `False` to disable dashboard

---

### LIVE_MONITOR_REFRESH_S
**Location**: `config.py` (line 568)
**Type**: Float
**Default**: `2.0`
**Description**: Update interval for live monitors (seconds).

**Impact**:
- Lower values = more frequent updates (higher CPU)
- Higher values = less frequent updates (lower CPU)

**Recommended**: 1.0 - 5.0 seconds

---

## 📊 Feature Flag Decision Matrix

| Feature | Production | Testing | Development | Notes |
|---------|-----------|---------|-------------|-------|
| **ENABLE_COID_MANAGER** | ✅ True | ✅ True | ✅ True | Critical - prevents duplicate orders |
| **ENABLE_STARTUP_RECONCILE** | ✅ True | ✅ True | ⚠️ Optional | Adds startup delay |
| **ENABLE_ENTRY_SLIPPAGE_GUARD** | ✅ True | ✅ True | ⚠️ Optional | May reduce fill rate |
| **USE_FIRST_FILL_TS_FOR_TTL** | ✅ True | ✅ True | ✅ True | No downside |
| **ENABLE_SYMBOL_LOCKS** | ✅ True | ✅ True | ✅ True | Critical for thread safety |
| **ENABLE_SPREAD_GUARD_ENTRY** | ✅ True | ✅ True | ⚠️ Optional | Trade-off: safety vs. opportunities |
| **ENABLE_DEPTH_GUARD_ENTRY** | ✅ True | ✅ True | ⚠️ Optional | Trade-off: safety vs. opportunities |
| **ENABLE_CONSOLIDATED_ENTRY_GUARDS** | ✅ True | ✅ True | ✅ True | No downside |
| **ENABLE_FILL_TELEMETRY** | ✅ True | ✅ True | ✅ True | Useful for monitoring |
| **ENABLE_CONSOLIDATED_EXITS** | ✅ True | ✅ True | ✅ True | No downside |
| **ENABLE_RICH_LOGGING** | ✅ True | ✅ True | ✅ True | Better DX |
| **ENABLE_LIVE_MONITORS** | ⚠️ Optional | ✅ True | ✅ True | Nice for monitoring |
| **ENABLE_LIVE_HEARTBEAT** | ⚠️ Optional | ✅ True | ✅ True | Nice for monitoring |
| **ENABLE_LIVE_DASHBOARD** | ⚠️ Optional | ✅ True | ✅ True | Nice for monitoring |

**Legend**:
- ✅ **Recommended**: Enable for this environment
- ⚠️ **Optional**: Enable based on specific needs
- ❌ **Not Recommended**: Disable for this environment

---

## 🔄 Gradual Rollout Strategy

### Phase 1: Core Safety Features (Week 1)
**Enable**:
- `ENABLE_COID_MANAGER = True`
- `ENABLE_STARTUP_RECONCILE = True`
- `USE_FIRST_FILL_TS_FOR_TTL = True`
- `ENABLE_SYMBOL_LOCKS = True`

**Monitor**:
- No duplicate orders in logs
- COID registry size stays <1000
- No deadlocks or race conditions

**Success Criteria**: 7 days without order duplicates

---

### Phase 2: Entry Guards (Week 2)
**Enable**:
- `ENABLE_ENTRY_SLIPPAGE_GUARD = True`
- `ENABLE_SPREAD_GUARD_ENTRY = True`
- `ENABLE_DEPTH_GUARD_ENTRY = True`
- `ENABLE_CONSOLIDATED_ENTRY_GUARDS = True`

**Monitor**:
- Fill rate impact (<10% reduction acceptable)
- Guard block reasons in logs
- Average entry slippage

**Success Criteria**: Slippage reduced by >50%, acceptable fill rate

---

### Phase 3: Telemetry & Exits (Week 3)
**Enable**:
- `ENABLE_FILL_TELEMETRY = True`
- `ENABLE_CONSOLIDATED_EXITS = True`

**Monitor**:
- Fill telemetry statistics
- Memory usage (<10MB increase)
- Exit performance

**Success Criteria**: Telemetry data flowing, no performance regression

---

### Phase 4: Terminal UI (Week 4)
**Enable**:
- `ENABLE_RICH_LOGGING = True`
- `ENABLE_LIVE_MONITORS = True`
- `ENABLE_LIVE_HEARTBEAT = True`
- `ENABLE_LIVE_DASHBOARD = True`

**Monitor**:
- CPU usage (<5% increase)
- Terminal rendering performance
- User experience feedback

**Success Criteria**: Improved developer experience, no performance issues

---

## 🚨 Emergency Rollback

If issues arise, follow this rollback sequence:

### Critical Issues (Duplicate Orders, Deadlocks)
```python
# Immediate rollback
ENABLE_COID_MANAGER = False
ENABLE_SYMBOL_LOCKS = False
ENABLE_ORDER_FLOW_HARDENING = False  # Master switch
```

### Performance Issues (High Latency, Throttling)
```python
# Disable performance features
ENABLE_COALESCING = False
ENABLE_RATE_LIMITING = False
ENABLE_FILL_TELEMETRY = False
```

### UI Issues (Terminal Problems)
```python
# Disable terminal UI
ENABLE_RICH_LOGGING = False
ENABLE_LIVE_MONITORS = False
ENABLE_LIVE_DASHBOARD = False
```

---

## 📈 Success Metrics

### Order Flow Hardening
- **Duplicate Orders**: 0 per day
- **Entry Slippage**: <15 bps on average
- **Fill Rate**: >85% (Limit), >95% (Market)
- **Order Latency**: P95 <200ms

### Performance
- **API Throttle Rate**: <10%
- **Cache Hit Rate**: >70%
- **Coalescing Rate**: >80%
- **Memory Growth**: <50MB per day

### Terminal UI
- **CPU Usage**: <5% increase
- **Render Rate**: >0.5 Hz (2s refresh)
- **User Satisfaction**: Positive feedback

---

## 📚 Related Documentation

- [Deployment Guide](./DEPLOYMENT.md)
- [Monitoring Guide](./MONITORING.md)
- [Troubleshooting](./TROUBLESHOOTING.md)
- [API Documentation](./API.md)

---

**Last Updated**: 2025-10-14
**Maintainer**: Trading Bot Team
