# Testing and Monitoring Guide

This guide covers the regression testing and monitoring infrastructure implemented as part of the critical bug fixes.

## Regression Testing

### Quick Start

Run a 5-minute regression test to validate all bug fixes:

```bash
./scripts/run_paper_session.sh 300
```

### What It Tests

The regression test validates all 4 critical bug fixes:

1. **ORDER_FILLED Events** (Bug Fix 9.1)
   - Checks that orders are actually being filled
   - Calculates fill rate
   - Fails if 100% failure rate detected (pre-fix behavior)

2. **Stale Intent Cleanup** (Bug Fix 9.2)
   - Verifies pending_buy_intents are being cleared
   - Fails if >50 stale intents detected (pre-fix behavior)
   - Warns if >10 intents pending

3. **Error Observability** (Bug Fix 9.1)
   - Confirms ORDER_FAILED events are logged at ERROR level
   - Detects silent failures (orders failing without logs)

4. **No Crashes** (Bug Fix 9.4)
   - Checks for stackdump.txt (threading deadlock indicator)
   - Validates budget refresh timeout protection

### Test Output

```bash
=== Trading Bot Regression Test ===
Duration: 300s

[TEST 1] Checking ORDER_FILLED events...
  ORDER_SENT:   12
  ORDER_FILLED: 8
  ORDER_FAILED: 4
  Fill Rate:    66.7%
  ✓ PASS: Orders are being filled (66.7% fill rate)

[TEST 2] Checking for stale pending_buy_intents...
  Pending Intents: 4
  Positions:       8
  ✓ PASS: Pending intents under control (4)

[TEST 3] Checking for ERROR-level observability...
  ERROR logs:       4
  ORDER_FAILED logs: 4
  ✓ PASS: Error logging is working

[TEST 4] Checking for crashes...
  ✓ PASS: No crashes detected

========================================
✓ ALL TESTS PASSED
========================================
```

### CI/CD Integration

Add to your CI pipeline:

```yaml
# .github/workflows/test.yml
- name: Run regression test
  run: |
    ./scripts/run_paper_session.sh 300
```

## Monitoring

### Metrics Collection

The bot now collects comprehensive metrics via `core/monitoring/metrics.py`.

### Enabling Metrics

In your startup code (e.g., `main.py`):

```python
from core.monitoring import init_metrics

# Initialize with local JSON export (always enabled)
metrics = init_metrics(session_dir=SESSION_DIR)

# Optional: Enable Prometheus
metrics = init_metrics(
    session_dir=SESSION_DIR,
    enable_prometheus=True
)

# Optional: Enable StatsD
metrics = init_metrics(
    session_dir=SESSION_DIR,
    enable_statsd=True,
    statsd_host="localhost",
    statsd_port=8125
)
```

### Using Metrics

```python
from core.monitoring import get_metrics

metrics = get_metrics()

# Record order execution
metrics.record_order_sent(symbol, side)
metrics.record_order_filled(symbol, side, latency_ms=123.4)
metrics.record_order_failed(symbol, side, error_code="INSUFFICIENT_BALANCE")

# Record intent lifecycle
metrics.record_intent_pending(count=5)
metrics.record_intent_cleared(reason="order_filled")
metrics.record_stale_intent(age_seconds=65.0)

# Record performance
metrics.record_budget_refresh(duration_ms=234.5, timed_out=False)
metrics.record_logging_timeout()

# Export metrics
metrics.export_json()  # Writes to session_dir/metrics/summary.json
metrics.log_summary()  # Logs summary to console
```

### Metrics Tracked

#### Order Execution
- `orders_sent_total` - Total orders sent to exchange
- `orders_filled_total` - Total successful fills
- `orders_failed_total` - Total failed orders
- `order_fill_rate` - Current fill rate (0.0 to 1.0)
- `order_execution_latency_seconds` - Histogram of order latency

#### Intent Lifecycle
- `intents_pending` - Current number of pending intents (gauge)
- `intents_cleared_total` - Total intents cleared
- `intents_stale_total` - Total stale intents detected
- `stale_intent_age_s` - Histogram of stale intent ages

#### Performance
- `budget_refresh_duration_seconds` - Budget refresh latency histogram
- `budget_refresh_timeouts_total` - Number of budget refresh timeouts

#### System Health
- `logging_timeouts_total` - Logging formatter timeout count

### Prometheus Integration

If Prometheus is enabled, metrics are exposed on the default port (usually 8000):

```bash
# Install prometheus_client
pip install prometheus_client

# Metrics available at http://localhost:8000/metrics
```

Example Prometheus queries:

```promql
# Fill rate over last 5 minutes
rate(orders_filled_total[5m]) / rate(orders_sent_total[5m])

# Average order latency (p95)
histogram_quantile(0.95, rate(order_execution_latency_seconds_bucket[5m]))

# Stale intent rate
rate(intents_stale_total[1h])
```

### StatsD Integration

If StatsD is enabled, metrics are sent to the configured server:

```bash
# Install statsd client
pip install statsd

# Metrics sent to localhost:8125 by default
```

Metrics are prefixed with `trading_bot.`:
- `trading_bot.orders.sent`
- `trading_bot.orders.filled`
- `trading_bot.orders.fill_rate`
- `trading_bot.intents.pending`
- etc.

### Local JSON Export

Metrics are always exported to `{session_dir}/metrics/summary.json`:

```json
{
  "timestamp": 1698765432.123,
  "counters": {
    "orders_sent": 67,
    "orders_filled": 45,
    "orders_failed": 22,
    "intents_cleared": 45,
    "intents_stale": 3
  },
  "gauges": {
    "fill_rate": 0.671,
    "intents_pending": 2
  },
  "histograms": {
    "order_latency_ms": {
      "count": 45,
      "min": 123.4,
      "max": 2345.6,
      "avg": 567.8
    },
    "budget_refresh_ms": {
      "count": 120,
      "min": 45.2,
      "max": 4987.3,
      "avg": 234.5
    }
  }
}
```

## Alerting

### Fill Rate Alert

Monitor fill rate and alert if it drops below threshold:

```python
summary = metrics.get_summary()
fill_rate = summary['gauges'].get('fill_rate', 0)

if fill_rate < 0.3:  # Less than 30% fills
    send_alert(f"Low fill rate: {fill_rate:.1%}")
```

### Stale Intent Alert

Alert on excessive stale intents:

```python
pending = summary['gauges'].get('intents_pending', 0)
stale = summary['counters'].get('intents_stale', 0)

if pending > 50 or stale > 10:
    send_alert(f"Intent cleanup issues: {pending} pending, {stale} stale")
```

### Performance Degradation

Monitor budget refresh timeouts:

```python
timeouts = summary['counters'].get('budget_refresh_timeouts', 0)

if timeouts > 5:
    send_alert(f"Budget refresh degraded: {timeouts} timeouts")
```

## Post-Deployment Validation

After deploying the bug fixes, run these validation steps:

1. **Run regression test** (5-10 minutes):
   ```bash
   ./scripts/run_paper_session.sh 600
   ```

2. **Check metrics summary**:
   ```bash
   cat sessions/session_*/metrics/summary.json | jq .
   ```

3. **Verify fill rate** (should be >0):
   ```bash
   jq -r '.gauges.fill_rate' sessions/session_*/metrics/summary.json
   ```

4. **Check for stale intents** (should be <10):
   ```bash
   jq -r '.counters.intents_stale' sessions/session_*/metrics/summary.json
   ```

5. **Verify ERROR logs exist** for failures:
   ```bash
   grep -c '"level":"ERROR"' sessions/session_*/logs/events_*.jsonl
   ```

## Troubleshooting

### Test Failures

**"No ORDER_FILLED events"**
- Check exchange connectivity
- Verify GLOBAL_TRADING=True in config.py
- Check orderbook depth and liquidity
- Review MAX_SLIPPAGE_BPS_ENTRY and MAX_SPREAD_BPS_ENTRY settings

**"Excessive stale intents"**
- Check for order.failed event subscription in engine.py
- Verify clear_intent() is being called
- Review stale intent cleanup cron

**"Orders failing silently"**
- Verify ORDER_FAILED event logging in order_router.py
- Check logger level is set to ERROR
- Ensure TRACE_SAMPLE_RATE is not filtering ERROR logs

### Metrics Not Working

**Prometheus metrics not exposed**
- Install: `pip install prometheus_client`
- Check port 8000 is not in use
- Verify `enable_prometheus=True`

**StatsD metrics not received**
- Install: `pip install statsd`
- Check StatsD server is running
- Verify network connectivity to statsd_host:statsd_port
- Test with: `echo "test.metric:1|c" | nc -u -w0 localhost 8125`

**JSON export missing**
- Check session_dir exists and is writable
- Verify metrics.export_json() is being called
- Check logs for export errors
