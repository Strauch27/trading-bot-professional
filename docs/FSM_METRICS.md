# FSM Metrics Reference

## Overview

The FSM engine exposes metrics via Prometheus for monitoring and alerting. Metrics are available at `http://localhost:8000/metrics` when `ENABLE_PROMETHEUS=True`.

## Enabling Metrics

```python
# config.py
FSM_ENABLED = True
ENABLE_PROMETHEUS = True
PROMETHEUS_PORT = 8000
```

## Metric Types

- **Counter**: Monotonically increasing value (e.g., total phase changes)
- **Gauge**: Value that can go up or down (e.g., current phase code)
- **Histogram**: Distribution of values (e.g., phase durations)

## Core Metrics

### 1. phase_changes_total (Counter)

**Description**: Total number of phase transitions

**Labels**:
- `symbol`: Trading symbol (e.g., "BTC/USDT")
- `phase`: Target phase (e.g., "entry_eval")

**Example**:
```promql
phase_changes_total{symbol="BTC/USDT", phase="entry_eval"}
```

**Use Cases**:
- Track trading activity per symbol
- Detect phase transition rate anomalies
- Calculate phase success rates

**Queries**:

**Phase transition rate (5m average)**:
```promql
rate(phase_changes_total[5m])
```

**Most active symbols**:
```promql
topk(10, sum by (symbol) (rate(phase_changes_total[1h])))
```

**Phase distribution**:
```promql
sum by (phase) (rate(phase_changes_total[1h]))
```

### 2. phase_code (Gauge)

**Description**: Current phase as numeric code (for graphing)

**Labels**:
- `symbol`: Trading symbol

**Phase Codes**:
```
0  = WARMUP
1  = IDLE
2  = ENTRY_EVAL
3  = PLACE_BUY
4  = WAIT_FILL
5  = POSITION
6  = EXIT_EVAL
7  = PLACE_SELL
8  = WAIT_SELL_FILL
9  = POST_TRADE
10 = COOLDOWN
11 = ERROR
```

**Example**:
```promql
phase_code{symbol="BTC/USDT"}
```

**Use Cases**:
- Visualize phase over time (timeline graph)
- Detect phase oscillations
- Monitor phase distribution

**Queries**:

**Current phase for all symbols**:
```promql
phase_code
```

**Symbols in error state**:
```promql
phase_code == 11
```

**Symbols in active trading (not idle/cooldown)**:
```promql
phase_code > 1 and phase_code < 10
```

**Phase distribution histogram**:
```promql
count by (phase_code) (phase_code)
```

### 3. phase_duration_seconds (Histogram)

**Description**: Time spent in each phase (seconds)

**Labels**:
- `phase`: Phase name (e.g., "wait_fill")

**Buckets**: 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, +Inf

**Example**:
```promql
phase_duration_seconds_bucket{phase="wait_fill"}
```

**Use Cases**:
- Detect slow phases
- Calculate percentiles (P50, P95, P99)
- Set timeout thresholds

**Queries**:

**Average duration per phase**:
```promql
rate(phase_duration_seconds_sum[1h]) / rate(phase_duration_seconds_count[1h])
```

**P95 duration per phase**:
```promql
histogram_quantile(0.95, rate(phase_duration_seconds_bucket[5m]))
```

**P99 duration per phase**:
```promql
histogram_quantile(0.99, rate(phase_duration_seconds_bucket[5m]))
```

**Slow phases (P95 > 30s)**:
```promql
histogram_quantile(0.95, rate(phase_duration_seconds_bucket[5m])) > 30
```

### 4. stuck_in_phase_seconds (Gauge)

**Description**: Time currently stuck in phase (seconds)

**Labels**:
- `symbol`: Trading symbol
- `phase`: Current phase

**Example**:
```promql
stuck_in_phase_seconds{symbol="BTC/USDT", phase="wait_fill"}
```

**Use Cases**:
- Alerting on stuck symbols
- Identify timeout issues
- Monitor phase health

**Queries**:

**Symbols stuck >60s (excluding idle/cooldown)**:
```promql
stuck_in_phase_seconds{phase!="idle", phase!="cooldown"} > 60
```

**Longest stuck symbol**:
```promql
topk(1, stuck_in_phase_seconds)
```

**Stuck symbols per phase**:
```promql
count by (phase) (stuck_in_phase_seconds{phase!="idle"} > 60)
```

**Alert Query**:
```promql
stuck_in_phase_seconds{phase="wait_fill"} > 60
  or stuck_in_phase_seconds{phase="wait_sell_fill"} > 30
  or stuck_in_phase_seconds{phase="entry_eval"} > 10
```

### 5. phase_errors_total (Counter)

**Description**: Total number of errors by phase

**Labels**:
- `symbol`: Trading symbol
- `phase`: Phase where error occurred
- `error_type`: Error category (e.g., "api_error", "timeout", "validation_error")

**Example**:
```promql
phase_errors_total{symbol="BTC/USDT", phase="place_buy", error_type="api_error"}
```

**Use Cases**:
- Monitor error rates
- Identify problematic phases
- Categorize errors

**Queries**:

**Error rate per phase (5m)**:
```promql
rate(phase_errors_total[5m])
```

**Top error types**:
```promql
topk(5, sum by (error_type) (rate(phase_errors_total[1h])))
```

**Symbols with highest error rate**:
```promql
topk(10, sum by (symbol) (rate(phase_errors_total[1h])))
```

**Phase error distribution**:
```promql
sum by (phase) (rate(phase_errors_total[1h]))
```

### 6. phase_entries_total (Counter)

**Description**: Total number of entries into each phase

**Labels**:
- `phase`: Phase name

**Example**:
```promql
phase_entries_total{phase="entry_eval"}
```

**Use Cases**:
- Calculate phase throughput
- Measure entry funnel
- Compute success rates

**Queries**:

**Entry rate per phase**:
```promql
rate(phase_entries_total[5m])
```

**Buy signal conversion rate (entry_eval → place_buy)**:
```promql
rate(phase_entries_total{phase="place_buy"}[1h]) / rate(phase_entries_total{phase="entry_eval"}[1h])
```

**Fill rate (wait_fill → position)**:
```promql
rate(phase_entries_total{phase="position"}[1h]) / rate(phase_entries_total{phase="wait_fill"}[1h])
```

### 7. phase_exits_total (Counter)

**Description**: Total number of exits from each phase

**Labels**:
- `phase`: Phase name
- `outcome`: Exit outcome ("success", "error", "timeout")

**Example**:
```promql
phase_exits_total{phase="wait_fill", outcome="success"}
```

**Use Cases**:
- Calculate success rates
- Monitor timeout frequency
- Track error exits

**Queries**:

**Success rate per phase**:
```promql
rate(phase_exits_total{outcome="success"}[1h]) / rate(phase_exits_total[1h])
```

**Timeout rate**:
```promql
rate(phase_exits_total{outcome="timeout"}[1h]) / rate(phase_exits_total[1h])
```

**Error exit rate**:
```promql
rate(phase_exits_total{outcome="error"}[1h]) / rate(phase_exits_total[1h])
```

## Derived Metrics

### Trading Performance

**Completed trades per hour**:
```promql
rate(phase_entries_total{phase="post_trade"}[1h]) * 3600
```

**Average time per trade (entry_eval → post_trade)**:
```promql
(
  rate(phase_duration_seconds_sum{phase="entry_eval"}[1h]) +
  rate(phase_duration_seconds_sum{phase="place_buy"}[1h]) +
  rate(phase_duration_seconds_sum{phase="wait_fill"}[1h]) +
  rate(phase_duration_seconds_sum{phase="position"}[1h]) +
  rate(phase_duration_seconds_sum{phase="exit_eval"}[1h]) +
  rate(phase_duration_seconds_sum{phase="place_sell"}[1h]) +
  rate(phase_duration_seconds_sum{phase="wait_sell_fill"}[1h]) +
  rate(phase_duration_seconds_sum{phase="post_trade"}[1h])
) / rate(phase_entries_total{phase="post_trade"}[1h])
```

**Entry success rate (entry_eval → position)**:
```promql
rate(phase_entries_total{phase="position"}[1h]) / rate(phase_entries_total{phase="entry_eval"}[1h])
```

**Exit success rate (exit_eval → post_trade)**:
```promql
rate(phase_entries_total{phase="post_trade"}[1h]) / rate(phase_entries_total{phase="exit_eval"}[1h])
```

### System Health

**Active symbols**:
```promql
count(phase_code > 1 and phase_code < 10)
```

**Symbols in error state**:
```promql
count(phase_code == 11)
```

**Overall error rate**:
```promql
sum(rate(phase_errors_total[5m]))
```

**Average phase transitions per symbol**:
```promql
avg(rate(phase_changes_total[1h]))
```

## Grafana Dashboards

### Dashboard 1: FSM Overview

**Panels**:

1. **Active Symbols (Gauge)**:
   ```promql
   count(phase_code > 1 and phase_code < 10)
   ```

2. **Phase Distribution (Pie Chart)**:
   ```promql
   sum by (phase_code) (phase_code >= 0)
   ```

3. **Phase Timeline (Heatmap)**:
   ```promql
   phase_code
   ```
   - X-axis: Time
   - Y-axis: Symbol
   - Color: Phase code

4. **Phase Transition Rate (Graph)**:
   ```promql
   sum(rate(phase_changes_total[1m]))
   ```

5. **Error Rate (Graph)**:
   ```promql
   sum(rate(phase_errors_total[1m])) by (error_type)
   ```

6. **Stuck Symbols (Table)**:
   ```promql
   stuck_in_phase_seconds{phase!="idle"} > 60
   ```

### Dashboard 2: Phase Performance

**Panels**:

1. **Phase Duration P95 (Graph)**:
   ```promql
   histogram_quantile(0.95, rate(phase_duration_seconds_bucket[5m])) by (phase)
   ```

2. **Phase Success Rate (Graph)**:
   ```promql
   rate(phase_exits_total{outcome="success"}[5m]) / rate(phase_exits_total[5m]) by (phase)
   ```

3. **Timeout Rate (Graph)**:
   ```promql
   rate(phase_exits_total{outcome="timeout"}[5m]) / rate(phase_exits_total[5m]) by (phase)
   ```

4. **Slowest Phases (Bar Gauge)**:
   ```promql
   avg by (phase) (rate(phase_duration_seconds_sum[1h]) / rate(phase_duration_seconds_count[1h]))
   ```

5. **Entry Funnel (Graph)**:
   ```promql
   rate(phase_entries_total{phase=~"entry_eval|place_buy|wait_fill|position"}[5m])
   ```

6. **Exit Funnel (Graph)**:
   ```promql
   rate(phase_entries_total{phase=~"exit_eval|place_sell|wait_sell_fill|post_trade"}[5m])
   ```

### Dashboard 3: Trading Activity

**Panels**:

1. **Trades Per Hour (Stat)**:
   ```promql
   rate(phase_entries_total{phase="post_trade"}[1h]) * 3600
   ```

2. **Active Positions (Gauge)**:
   ```promql
   count(phase_code == 5)
   ```

3. **Symbols by Phase (Bar Chart)**:
   ```promql
   count by (phase_code) (phase_code)
   ```

4. **Buy Signal Conversion (Graph)**:
   ```promql
   rate(phase_entries_total{phase="place_buy"}[5m]) / rate(phase_entries_total{phase="entry_eval"}[5m])
   ```

5. **Fill Rate (Graph)**:
   ```promql
   rate(phase_entries_total{phase="position"}[5m]) / rate(phase_entries_total{phase="wait_fill"}[5m])
   ```

6. **Most Active Symbols (Table)**:
   ```promql
   topk(20, sum by (symbol) (rate(phase_changes_total[1h])))
   ```

## Alerting Rules

### Alert 1: Symbol Stuck Too Long

**Severity**: Warning

**Query**:
```promql
stuck_in_phase_seconds{phase!="idle",phase!="cooldown"} > 300
```

**Description**: Symbol stuck in non-idle phase for >5 minutes

**Actions**:
- Check Rich status table
- Check phase event log for symbol
- Consider manual intervention

### Alert 2: High Error Rate

**Severity**: Warning

**Query**:
```promql
sum(rate(phase_errors_total[5m])) > 0.1
```

**Description**: Error rate >0.1 per second (6 errors/min)

**Actions**:
- Check error types in metrics
- Check API status
- Check network connectivity
- Consider reducing trading frequency

### Alert 3: Low Fill Rate

**Severity**: Info

**Query**:
```promql
rate(phase_entries_total{phase="position"}[10m]) / rate(phase_entries_total{phase="wait_fill"}[10m]) < 0.5
```

**Description**: Less than 50% of orders filling

**Actions**:
- Check order timeout settings
- Check order book depth
- Consider using IOC orders
- Adjust buy zone pricing

### Alert 4: Phase Timeout Spike

**Severity**: Warning

**Query**:
```promql
rate(phase_exits_total{outcome="timeout"}[5m]) > 0.05
```

**Description**: More than 3 timeouts per minute

**Actions**:
- Check which phase is timing out
- Increase timeout threshold
- Check exchange latency
- Check order book liquidity

### Alert 5: Trading Stalled

**Severity**: Critical

**Query**:
```promql
rate(phase_entries_total{phase="post_trade"}[10m]) == 0
  and (count(phase_code > 1) > 0)
```

**Description**: No completed trades in 10 min, but symbols are active

**Actions**:
- Check if all symbols stuck
- Check for engine hang
- Check phase event log
- Restart bot if necessary

### Alert 6: All Symbols in Error

**Severity**: Critical

**Query**:
```promql
count(phase_code == 11) / count(phase_code) > 0.5
```

**Description**: More than 50% of symbols in ERROR phase

**Actions**:
- Check API status
- Check for common error type
- Check network connectivity
- Consider pausing trading

## Prometheus Configuration

### prometheus.yml

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'trading_bot_fsm'
    static_configs:
      - targets: ['localhost:8000']
    scrape_interval: 10s
```

### alerts.yml

```yaml
groups:
  - name: trading_bot_fsm
    interval: 30s
    rules:
      - alert: SymbolStuckTooLong
        expr: stuck_in_phase_seconds{phase!="idle",phase!="cooldown"} > 300
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Symbol {{ $labels.symbol }} stuck in {{ $labels.phase }} for {{ $value }}s"

      - alert: HighErrorRate
        expr: sum(rate(phase_errors_total[5m])) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High error rate: {{ $value | humanize }} errors/s"

      - alert: LowFillRate
        expr: rate(phase_entries_total{phase="position"}[10m]) / rate(phase_entries_total{phase="wait_fill"}[10m]) < 0.5
        for: 5m
        labels:
          severity: info
        annotations:
          summary: "Low fill rate: {{ $value | humanizePercentage }}"

      - alert: TradingStalled
        expr: rate(phase_entries_total{phase="post_trade"}[10m]) == 0 and (count(phase_code > 1) > 0)
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "No completed trades in 10 minutes"

      - alert: AllSymbolsInError
        expr: count(phase_code == 11) / count(phase_code) > 0.5
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "{{ $value | humanizePercentage }} of symbols in ERROR state"
```

## Querying Tips

### PromQL Basics

**Rate vs Increase**:
- `rate()`: Per-second rate over time window
- `increase()`: Total increase over time window

**Example**:
```promql
rate(phase_changes_total[5m])  # Changes per second (avg over 5m)
increase(phase_changes_total[5m])  # Total changes in 5m
```

**Aggregation**:
- `sum`: Sum across labels
- `avg`: Average across labels
- `max`/`min`: Maximum/minimum
- `count`: Count of time series

**Example**:
```promql
sum by (phase) (rate(phase_changes_total[5m]))  # Sum per phase
avg(rate(phase_changes_total[5m]))  # Average across all
```

**Filtering**:
- `==`: Equal
- `!=`: Not equal
- `=~`: Regex match
- `!~`: Regex not match

**Example**:
```promql
phase_code{symbol="BTC/USDT"}  # Specific symbol
phase_code{symbol=~"BTC.*"}  # Regex match
phase_code != 1  # Not idle
```

## Performance Impact

### Metrics Overhead

**CPU**: <1% overhead
- Metric updates are fast (atomic operations)
- No complex calculations in hot path

**Memory**: ~10KB per symbol
- Each symbol has ~10 metric time series
- Prometheus client buffers ~1000 samples

**Disk**: ~1MB per hour
- Depends on scrape interval and retention
- WAL compaction reduces size

### Optimization

**Reduce metric cardinality**:
- Avoid high-cardinality labels (e.g., decision_id)
- Use `error_type` instead of full error message
- Limit symbol count in watchlist

**Increase scrape interval**:
- 10s → 30s reduces load by 3x
- Acceptable for most use cases
- Alerts may be slightly delayed

**Use recording rules**:
- Pre-compute expensive queries
- Reduces query time from seconds to milliseconds

**Example recording rule**:
```yaml
groups:
  - name: fsm_recordings
    interval: 30s
    rules:
      - record: fsm:phase_changes:rate5m
        expr: rate(phase_changes_total[5m])

      - record: fsm:stuck_symbols:count
        expr: count(stuck_in_phase_seconds{phase!="idle"} > 60)
```

## See Also
- [FSM Architecture](FSM_ARCHITECTURE.md) - Design and phase flow
- [FSM Debugging Guide](FSM_DEBUGGING.md) - Troubleshooting and common issues
- [Prometheus Documentation](https://prometheus.io/docs/) - Official Prometheus docs
- [Grafana Documentation](https://grafana.com/docs/) - Official Grafana docs
