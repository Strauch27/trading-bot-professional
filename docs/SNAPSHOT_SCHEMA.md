# MarketSnapshot Schema - V9_3

**Version:** 1
**Last Updated:** 2025-10-17

---

## Overview

The `MarketSnapshot` is the canonical data structure for real-time market state in the V9_3 architecture. It unifies:

- **Price Data**: Last, bid, ask, mid prices
- **Liquidity Metrics**: Spread, depth, order book imbalance
- **Rolling Windows**: Anchor, peak, trough, drop/rise percentages
- **Derived Features**: Technical indicators, volatility, momentum
- **System State**: Timestamp, version, flags
- **Quality Indicators**: Data staleness, guard status

Snapshots are:
- Generated every `SNAPSHOT_MIN_PERIOD_MS` (default: 500ms)
- Persisted to `state/snapshots/snapshots_YYYYMMDD.jsonl`
- Published to EventBus for buy/sell decision modules
- Used by Dashboard for real-time display

---

## Schema Definition

```python
{
    # === METADATA ===
    "v": 1,                    # Schema version (int)
    "ts": 1729166400.123,      # Unix timestamp (float, seconds.microseconds)
    "symbol": "BTC/USDT",      # Trading symbol (str)

    # === PRICE DATA ===
    "price": {
        "last": 105164.22,     # Last trade price (float)
        "bid": 105163.50,      # Best bid price (float or None)
        "ask": 105165.00,      # Best ask price (float or None)
        "mid": 105164.25       # Mid price: (bid + ask) / 2 (float or None)
    },

    # === LIQUIDITY METRICS ===
    "liquidity": {
        "spread_bps": 1.43,           # Bid-ask spread in basis points (float or None)
        "spread_pct": 0.0143,         # Bid-ask spread in percent (float or None)
        "depth_usd": 5678.45,         # Total order book depth in USD (float or None)
        "imbalance": 0.23,            # Order book imbalance: (bid_depth - ask_depth) / total (float, -1 to +1, or None)
        "bid_depth_usd": 3456.78,     # Cumulative bid depth in USD (float or None)
        "ask_depth_usd": 2221.67      # Cumulative ask depth in USD (float or None)
    },

    # === ROLLING WINDOWS (V9_3) ===
    "windows": {
        "anchor": 105164.22,          # V9_3 anchor price (float or None)
        "peak": 105200.00,            # Rolling window peak price (float or None)
        "trough": 104800.00,          # Rolling window trough price (float or None)
        "drop_pct": -1.23,            # Drop from anchor: 100 × (last - anchor) / anchor (float or None)
        "rise_pct": 0.35,             # Rise from trough: 100 × (last - trough) / trough (float or None)
        "window_lookback_s": 300      # Lookback period in seconds (int)
    },

    # === DERIVED FEATURES ===
    "features": {
        "volatility_bps": 45.6,       # Short-term volatility in basis points (float or None)
        "momentum_score": 0.67,       # Momentum indicator (float, -1 to +1, or None)
        "volume_24h_usd": 1234567.89, # 24h volume in USD (float or None)
        "rsi_14": 52.3,               # 14-period RSI (float, 0-100, or None)
        "ema_ratio": 1.002            # Price / EMA ratio (float or None)
    },

    # === STATE & METADATA ===
    "state": {
        "session_start": 104950.00,   # Session start price (float or None)
        "session_peak": 105200.00,    # Session peak price (float or None)
        "anchor_ts": 1729166300.456,  # Anchor timestamp (float or None)
        "anchor_source": "persistent", # Anchor calculation mode: "session_peak", "rolling_peak", "hybrid", "persistent" (str or None)
        "anchor_stale": false,        # Anchor stale flag (bool)
        "data_age_ms": 120            # Data age in milliseconds (int or None)
    },

    # === FLAGS & GUARDS ===
    "flags": {
        "stale": false,               # Data staleness flag (bool)
        "spread_guard_fail": false,   # Spread exceeds MAX_SPREAD_BPS (bool)
        "depth_guard_fail": false,    # Depth below MIN_DEPTH_USD (bool)
        "ticker_valid": true,         # Ticker passed validation guards (bool)
        "partial_data": false         # Missing bid/ask or depth data (bool)
    }
}
```

---

## Field Details

### Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `v` | int | ✅ | Schema version (currently 1) |
| `ts` | float | ✅ | Unix timestamp with microsecond precision |
| `symbol` | str | ✅ | Trading symbol in CCXT format (e.g., "BTC/USDT") |

### Price Data

All price fields are in quote currency (e.g., USDT).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `price.last` | float | ✅ | Last trade price from ticker |
| `price.bid` | float | ⚠️ | Best bid price (None if not available) |
| `price.ask` | float | ⚠️ | Best ask price (None if not available) |
| `price.mid` | float | ⚠️ | Mid price (None if bid/ask unavailable) |

**Fallback Logic:**
- If `bid`/`ask` are unavailable, `mid = last`
- Spread calculation uses fallback to 0 if bid/ask missing

### Liquidity Metrics

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `liquidity.spread_bps` | float | ⚠️ | Bid-ask spread in basis points (10000 × spread / mid) |
| `liquidity.spread_pct` | float | ⚠️ | Bid-ask spread as percentage (100 × spread / mid) |
| `liquidity.depth_usd` | float | ⚠️ | Total order book depth (sum of bid + ask depth) |
| `liquidity.imbalance` | float | ⚠️ | Order book imbalance: (bid - ask) / (bid + ask), range: [-1, +1] |
| `liquidity.bid_depth_usd` | float | ⚠️ | Cumulative bid depth in USD |
| `liquidity.ask_depth_usd` | float | ⚠️ | Cumulative ask depth in USD |

**Calculation Notes:**
- `spread_bps = 10000 × (ask - bid) / mid`
- `imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)`
- All liquidity fields are `None` if order book data unavailable

### Rolling Windows (V9_3)

These fields implement the V9_3 anchor-based drop trigger system.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `windows.anchor` | float | ⚠️ | V9_3 anchor price (mode-dependent calculation) |
| `windows.peak` | float | ⚠️ | Rolling window peak price (last `WINDOW_LOOKBACK_S` seconds) |
| `windows.trough` | float | ⚠️ | Rolling window trough price (last `WINDOW_LOOKBACK_S` seconds) |
| `windows.drop_pct` | float | ⚠️ | Drop percentage from anchor: `100 × (last - anchor) / anchor` |
| `windows.rise_pct` | float | ⚠️ | Rise percentage from trough: `100 × (last - trough) / trough` |
| `windows.window_lookback_s` | int | ✅ | Lookback period in seconds (from config.WINDOW_LOOKBACK_S) |

**V9_3 Drop Formula:**
```python
drop_pct = 100.0 × (last - anchor) / anchor
```

**Anchor Calculation (Mode-dependent):**
- **Mode 1 (Session-High):** `anchor = session_peak`
- **Mode 2 (Rolling-High):** `anchor = rolling_peak`
- **Mode 3 (Hybrid):** `anchor = max(session_peak, rolling_peak)`
- **Mode 4 (Persistent):** `anchor = persistent_anchor` (with clamps and stale-reset)

**Fallback:**
- If `anchor` is unavailable, uses `peak` for `drop_pct` calculation
- If both unavailable, `drop_pct = None`

### Derived Features

Optional technical indicators and metrics. May be `None` if not calculated.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `features.volatility_bps` | float | ⚠️ | Short-term price volatility in basis points |
| `features.momentum_score` | float | ⚠️ | Momentum indicator (range: -1 to +1) |
| `features.volume_24h_usd` | float | ⚠️ | 24-hour trading volume in USD |
| `features.rsi_14` | float | ⚠️ | 14-period Relative Strength Index (0-100) |
| `features.ema_ratio` | float | ⚠️ | Price / EMA ratio (for trend detection) |

### State & Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `state.session_start` | float | ⚠️ | First price of the current session |
| `state.session_peak` | float | ⚠️ | Highest price since session start |
| `state.anchor_ts` | float | ⚠️ | Timestamp when anchor was last updated (Mode 4 only) |
| `state.anchor_source` | str | ⚠️ | Anchor calculation mode ("session_peak", "rolling_peak", "hybrid", "persistent") |
| `state.anchor_stale` | bool | ✅ | True if anchor is stale (older than `ANCHOR_STALE_MINUTES`) |
| `state.data_age_ms` | int | ⚠️ | Age of ticker data in milliseconds |

### Flags & Guards

Quality indicators for buy/sell decision gating.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `flags.stale` | bool | ✅ | True if data age exceeds threshold (2 × POLL_MS) |
| `flags.spread_guard_fail` | bool | ✅ | True if spread > `MAX_SPREAD_BPS` |
| `flags.depth_guard_fail` | bool | ✅ | True if depth < `MIN_DEPTH_USD` |
| `flags.ticker_valid` | bool | ✅ | True if ticker passed all validation guards |
| `flags.partial_data` | bool | ✅ | True if bid/ask or depth data is missing |

**Guard Logic:**
- `ticker_valid = not (stale or spread_guard_fail or depth_guard_fail or last <= 0 or isnan(last))`
- Buy signals are blocked if `ticker_valid == false`

---

## Versioning

### Version 1 (Current)

- Initial schema with V9_3 anchor support
- All fields documented above
- Backward-compatible with legacy peak-based drops

### Future Versions

Schema versioning allows safe evolution:
- New fields can be added without breaking existing readers
- Readers should check `v` field and handle unknown fields gracefully
- Breaking changes require version bump

---

## Persistence Format

Snapshots are persisted to JSONL (JSON Lines) format:

**File Pattern:** `state/snapshots/snapshots_YYYYMMDD.jsonl`

**Rotation:**
- Daily rotation at midnight UTC
- Size-based rotation at `MAX_FILE_MB` (default: 50MB)
- Rotated files: `snapshots_YYYYMMDD_001.jsonl`, `snapshots_YYYYMMDD_002.jsonl`, etc.

**Example JSONL:**
```jsonl
{"v":1,"ts":1729166400.123,"symbol":"BTC/USDT","price":{"last":105164.22,"bid":105163.5,"ask":105165.0,"mid":105164.25},"liquidity":{"spread_bps":1.43,"depth_usd":5678.45,"imbalance":0.23},"windows":{"anchor":105164.22,"peak":105200.0,"trough":104800.0,"drop_pct":-1.23,"rise_pct":0.35,"window_lookback_s":300},"features":{},"state":{"session_start":104950.0,"session_peak":105200.0,"anchor_source":"persistent","anchor_stale":false},"flags":{"stale":false,"spread_guard_fail":false,"depth_guard_fail":false,"ticker_valid":true,"partial_data":false}}
{"v":1,"ts":1729166400.623,"symbol":"ETH/USDT","price":{"last":3987.45,"bid":3987.0,"ask":3988.0,"mid":3987.5},"liquidity":{"spread_bps":2.51,"depth_usd":3456.78,"imbalance":-0.12},"windows":{"anchor":3990.0,"peak":3995.0,"trough":3980.0,"drop_pct":-0.06,"rise_pct":0.19,"window_lookback_s":300},"features":{},"state":{"session_start":3985.0,"session_peak":3995.0,"anchor_source":"persistent","anchor_stale":false},"flags":{"stale":false,"spread_guard_fail":false,"depth_guard_fail":false,"ticker_valid":true,"partial_data":false}}
```

**Atomic Writes:**
- All writes use `.tmp` + `rename()` pattern for crash safety
- No partial snapshots on disk

---

## Usage Examples

### Reading Snapshots (Python)

```python
import json
from pathlib import Path

def load_snapshots(file_path: str):
    """Load snapshots from JSONL file."""
    snapshots = []
    with open(file_path, 'r') as f:
        for line in f:
            snapshot = json.loads(line)
            snapshots.append(snapshot)
    return snapshots

# Read latest snapshot file
snapshot_file = Path("state/snapshots/snapshots_20251017.jsonl")
snapshots = load_snapshots(snapshot_file)

# Access fields
for snap in snapshots:
    symbol = snap['symbol']
    last = snap['price']['last']
    drop_pct = snap['windows']['drop_pct']
    anchor = snap['windows']['anchor']

    print(f"{symbol}: ${last:.2f} | Drop: {drop_pct:.2f}% | Anchor: ${anchor:.2f}")
```

### Filtering by Drop Percentage

```python
def get_top_drops(snapshots, threshold=-1.0, limit=10):
    """Get symbols with biggest drops below threshold."""
    filtered = [
        snap for snap in snapshots
        if snap['windows'].get('drop_pct') is not None
        and snap['windows']['drop_pct'] < threshold
    ]

    # Sort by drop_pct (most negative first)
    filtered.sort(key=lambda x: x['windows']['drop_pct'])

    return filtered[:limit]

# Get top 10 drops below -1%
top_drops = get_top_drops(snapshots, threshold=-1.0, limit=10)
```

### Warm-Start (Loading Last N Ticks)

```python
def load_last_n_ticks(file_path: str, n: int = 300):
    """Load last N ticks from JSONL file for warm-start."""
    ticks = []

    with open(file_path, 'r') as f:
        # Read all lines and take last N
        lines = f.readlines()
        for line in lines[-n:]:
            tick = json.loads(line)
            ticks.append(tick)

    return ticks

# Load last 300 ticks for warm-start
ticks = load_last_n_ticks("state/ticks/BTC_USDT_20251017.jsonl", n=300)
```

---

## Related Files

| File | Purpose |
|------|---------|
| `market/snapshot_builder.py` | Builds MarketSnapshot from ticker + windows |
| `services/market_data.py` | Generates and publishes snapshots |
| `io/jsonl.py` | RotatingJSONLWriter for persistence |
| `market/anchor_manager.py` | Calculates V9_3 anchor prices |
| `core/rolling_windows.py` | Manages rolling peak/trough windows |
| `services/buy_signals.py` | Consumes snapshots for buy decisions |
| `ui/dashboard.py` | Displays snapshots in terminal UI |

---

## Configuration

Relevant config parameters:

```python
# Snapshot generation
SNAPSHOT_MIN_PERIOD_MS = 500      # Minimum time between snapshots
PERSIST_SNAPSHOTS = True          # Enable snapshot persistence

# Persistence
MAX_FILE_MB = 50                  # JSONL rotation size threshold

# Rolling windows
WINDOW_LOOKBACK_S = 300           # 5-minute rolling window

# V9_3 Anchor
DROP_TRIGGER_MODE = 4             # 1=Session, 2=Rolling, 3=Hybrid, 4=Persistent
ANCHOR_STALE_MINUTES = 60         # Stale-reset threshold

# Feature flags
FEATURE_ANCHOR_ENABLED = True     # Enable anchor-based drops
FEATURE_PERSIST_STREAMS = True    # Enable JSONL persistence
```

---

## Change Log

### 2025-10-17 (Version 1)
- Initial schema documentation
- V9_3 anchor-based drop trigger support
- 4-stream persistence architecture
- Comprehensive liquidity metrics
- Quality flags and guards

---

**Document Version:** 1.0
**Author:** Claude Code (Sonnet 4.5)
**Last Updated:** 2025-10-17
