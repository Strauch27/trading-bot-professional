# FSM Debugging Guide

## Quick Diagnosis

### Symptoms → Root Cause

| Symptom | Likely Phase | Common Causes |
|---------|-------------|---------------|
| No buys happening | IDLE, ENTRY_EVAL | Guards failing, no signals, max trades reached |
| Buy placed but no fill | WAIT_FILL | Order timeout, price moved away, insufficient liquidity |
| Position not exiting | POSITION, EXIT_EVAL | TP/SL not reached, trailing stop not triggered |
| Sell order not filling | WAIT_SELL_FILL | Price moved away, IOC order expired |
| Symbol stuck in phase | Any | Handler exception, timeout not configured, missing transition |
| High error count | ERROR | API errors, network issues, invalid order params |

## Debugging Tools

### 1. Rich Terminal Status Table

**Enable**:
```python
# config.py
FSM_ENABLED = True
ENABLE_RICH_TABLE = True
RICH_TABLE_REFRESH_HZ = 2.0
RICH_TABLE_SHOW_IDLE = False  # Hide idle symbols
```

**Reading the Table**:
```
Symbol    Phase           Dec ID      Age    Price       Amount      PnL       Note
─────────────────────────────────────────────────────────────────────────────────
BTC/USDT  WAIT_FILL ⏰    a3f8b2c1    12s    42150.50    0.000237    -         order_placed: 12345678
ETH/USDT  POSITION 💎     d9e2a4f7    3m     2100.25     0.004762    +1.23     -
SOL/USDT  ERROR ⚠️        f4c8b1a3    45s    -           -           -         API error: rate_limit
XRP/USDT  IDLE            -           -      0.5123      -           -         -

Active: 3 | Positions: 1 | Errors: 1 | Total PnL: +1.23
```

**Phase Icons**:
- 💎 POSITION - Holding position
- ⏰ WAIT_FILL / WAIT_SELL_FILL - Waiting too long (>30s)
- 📤 PLACE_BUY / PLACE_SELL - Placing order
- 🔍 ENTRY_EVAL / EXIT_EVAL - Evaluating
- ❄️ COOLDOWN - In cooldown
- ✅ POST_TRADE - Trade complete
- ⚠️ ERROR - Error state

**Priority Sorting**:
Most important phases appear first:
1. ERROR
2. WAIT_FILL / WAIT_SELL_FILL
3. PLACE_BUY / PLACE_SELL
4. POSITION
5. Other phases

### 2. Phase Event Logs (JSONL)

**Location**:
```
sessions/session_<timestamp>/logs/phase_events_<timestamp>.jsonl
```

**Event Format**:
```json
{
  "event_type": "phase_change",
  "ts_ms": 1704067200000,
  "symbol": "BTC/USDT",
  "from_phase": "idle",
  "to_phase": "entry_eval",
  "decision_id": "a3f8b2c1",
  "note": "checking entry",
  "duration_ms": 0
}
```

**Useful Queries**:

**Show all phase transitions for a symbol**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT") | {ts: .ts_ms, from: .from_phase, to: .to_phase, note: .note}'
```

**Find stuck symbols (age > 60s in non-idle phases)**:
```bash
cat phase_events_*.jsonl | jq 'select(.event_type == "phase_change" and .to_phase != "idle" and .to_phase != "cooldown")' | \
  jq -s 'group_by(.symbol) | map({symbol: .[0].symbol, last_phase: .[-1].to_phase, last_ts: .[-1].ts_ms})' | \
  jq "map(select((now * 1000) - .last_ts > 60000))"
```

**Calculate average phase durations**:
```bash
cat phase_events_*.jsonl | jq 'select(.event_type == "phase_change" and .duration_ms > 0)' | \
  jq -s 'group_by(.from_phase) | map({phase: .[0].from_phase, avg_duration_ms: (map(.duration_ms) | add / length)})'
```

**Find errors**:
```bash
cat phase_events_*.jsonl | jq 'select(.to_phase == "error") | {symbol, note, ts: .ts_ms}'
```

### 3. Prometheus Metrics

**Enable**:
```python
# config.py
FSM_ENABLED = True
ENABLE_PROMETHEUS = True
PROMETHEUS_PORT = 8000
```

**Access**: http://localhost:8000/metrics

**Key Metrics**:

**Current phase distribution**:
```promql
phase_code{symbol="BTC/USDT"}
```

**Phase change rate**:
```promql
rate(phase_changes_total[5m])
```

**Stuck symbols (>60s in phase)**:
```promql
stuck_in_phase_seconds{phase!="idle",phase!="cooldown"} > 60
```

**Error rate by phase**:
```promql
rate(phase_errors_total[5m])
```

**Phase duration P95**:
```promql
histogram_quantile(0.95, rate(phase_duration_seconds_bucket[5m]))
```

See [FSM_METRICS.md](FSM_METRICS.md) for full metrics reference.

### 4. Hybrid Mode Validation

**Enable**:
```python
# config.py
FSM_ENABLED = True
FSM_MODE = "both"  # Run legacy + FSM in parallel
HYBRID_LOG_DIVERGENCES = True
```

**Check for divergences**:
```bash
grep "DIVERGENCE DETECTED" sessions/session_*/logs/bot_log_*.jsonl
```

**Manual comparison**:
```python
# In Telegram commands or CLI
/compare_engines

# Returns:
# Position Count:
#   Legacy: 3
#   FSM:    3
#   Match:  True
# Symbols:
#   In Both:       {BTC/USDT, ETH/USDT, SOL/USDT}
#   Legacy Only:   {}
#   FSM Only:      {}
```

## Common Issues

### Issue 1: Symbol Stuck in ENTRY_EVAL

**Symptoms**:
- Symbol stays in ENTRY_EVAL for >10s
- No transition to PLACE_BUY or IDLE
- Age counter keeps increasing

**Root Causes**:
1. **Guards failing**: Check which guards are blocking
2. **No price updates**: Market data not flowing
3. **Exception in handler**: Check error logs

**Debug Steps**:

1. **Check phase event log**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and .to_phase == "entry_eval")' | tail -1
```
Look at `note` field - it should say which guard failed.

2. **Check guard status**:
```bash
grep "GUARD_BLOCK" sessions/*/logs/bot_log_*.jsonl | grep "BTC/USDT"
```

3. **Check if price is updating**:
```python
# In engine code or via debug logging
logger.info(f"BTC/USDT price: {state.current_price}, age: {state.age_seconds()}s")
```

4. **Check for exceptions**:
```bash
grep "entry_eval.*ERROR" sessions/*/logs/bot_log_*.jsonl
```

**Solutions**:
- If guards failing: Adjust guard thresholds in config or disable temporarily
- If no price updates: Check market data provider
- If exception: Fix handler bug and restart

### Issue 2: Order Timeout in WAIT_FILL

**Symptoms**:
- Buy order placed but times out after 30s
- Symbol returns to IDLE without fill
- Order canceled by bot

**Root Causes**:
1. **Price moved away**: Limit order no longer competitive
2. **Low liquidity**: Not enough volume at price
3. **Timeout too short**: Need more time for fill

**Debug Steps**:

1. **Check order details**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and .to_phase == "wait_fill")' | tail -1
```
Note the `order_id` from the transition.

2. **Check order status on exchange**:
```python
# Manual check via exchange API
exchange.fetch_order(order_id, "BTC/USDT")
```

3. **Check price movement**:
```bash
grep "BTC/USDT.*entry_eval" sessions/*/logs/bot_log_*.jsonl | tail -5
```
Compare entry price to current price.

4. **Check order book depth**:
```bash
# If using depth logging
grep "BTC/USDT.*orderbook" sessions/*/logs/bot_log_*.jsonl | tail -1
```

**Solutions**:
- Increase `BUY_ORDER_TIMEOUT_SECONDS` to 60
- Use IOC orders (`USE_PREDICTIVE_BUYS=True`) for immediate fill or cancel
- Adjust `PREDICTIVE_BUY_ZONE_BPS` to bid more aggressively
- Increase position size if below minimum notional

### Issue 3: Position Not Exiting

**Symptoms**:
- Symbol stays in POSITION phase for >60 min
- TP/SL thresholds not reached
- Trailing stop not triggering

**Root Causes**:
1. **TP/SL too wide**: Price not reaching thresholds
2. **Trailing stop misconfigured**: Activation threshold too high
3. **Max hold timeout not set**: No forced exit

**Debug Steps**:

1. **Check position state**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and .to_phase == "position")' | tail -1
```

2. **Check current vs entry price**:
```python
# From Rich status table or logs
# Look at PnL column and compare to TP/SL thresholds
```

3. **Check trailing stop status**:
```bash
grep "BTC/USDT.*trailing" sessions/*/logs/bot_log_*.jsonl | tail -5
```

4. **Check exit checks**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and .to_phase == "exit_eval")' | tail -5
```

**Solutions**:
- Adjust TP/SL thresholds: `TAKE_PROFIT_THRESHOLD`, `STOP_LOSS_THRESHOLD`
- Lower trailing stop activation: `TRAILING_STOP_ACTIVATION_PCT`
- Enable max hold timeout: `MAX_POSITION_HOLD_MINUTES=60`
- Manually exit via Telegram: `/exit BTC/USDT`

### Issue 4: High ERROR Phase Count

**Symptoms**:
- Many symbols in ERROR phase
- `error_count` increasing
- Logs show repeated API errors

**Root Causes**:
1. **API rate limits**: Too many requests
2. **Network issues**: Connection timeouts
3. **Invalid parameters**: Bad order params
4. **Exchange downtime**: MEXC API unavailable

**Debug Steps**:

1. **Check error details**:
```bash
cat phase_events_*.jsonl | jq 'select(.to_phase == "error") | {symbol, note, ts: .ts_ms}' | tail -10
```

2. **Check error type distribution**:
```bash
grep "phase_error" sessions/*/logs/bot_log_*.jsonl | cut -d'"' -f8 | sort | uniq -c | sort -rn
```

3. **Check API response times**:
```bash
grep "API_CALL_DURATION" sessions/*/logs/bot_log_*.jsonl | tail -20
```

4. **Check exchange status**:
```bash
curl https://www.mexc.com/api/v3/ping
```

**Solutions**:
- Reduce trading frequency: Increase cycle sleep time
- Add backoff: Increase `FSM_BACKOFF_BASE_SECONDS`
- Check API credentials: Verify .env file
- Wait for exchange recovery: Pause trading if widespread issue
- Reduce watchlist size: Trade fewer symbols

### Issue 5: Partial Sells Not Completing

**Symptoms**:
- Symbol stuck in WAIT_SELL_FILL → PLACE_SELL loop
- Partial fills but never reaching 95% threshold
- Remaining amount getting smaller but never zero

**Root Causes**:
1. **Low liquidity**: Not enough buyers at price
2. **Min notional issue**: Remaining amount below minimum
3. **IOC timeout too short**: Order expires before fill

**Debug Steps**:

1. **Check partial fill pattern**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and (.to_phase == "place_sell" or .to_phase == "wait_sell_fill"))' | tail -10
```
Look for repeated WAIT_SELL_FILL → PLACE_SELL transitions.

2. **Check remaining amount**:
```bash
# From Rich status table "Amount" column
# Or from phase event notes
cat phase_events_*.jsonl | jq 'select(.symbol == "BTC/USDT" and .to_phase == "place_sell" and (.note | contains("partial")))' | tail -5
```

3. **Check minimum notional**:
```python
# Calculate: remaining_amount * current_price
# Compare to MIN_NOTIONAL_USDT (default 5.0)
```

**Solutions**:
- Lower partial fill threshold: Modify `_handle_wait_sell_fill` to accept 90% instead of 95%
- Force market sell on dust: Add logic to use market order if remaining < min notional
- Increase `SELL_ORDER_TIMEOUT_SECONDS` to give more time
- Manually exit via Telegram: `/force_exit BTC/USDT`

### Issue 6: Hybrid Mode Divergence

**Symptoms**:
- `DIVERGENCE DETECTED` in logs
- Position counts differ between legacy and FSM
- Different symbols held

**Root Causes**:
1. **Race condition**: Legacy and FSM see different market state
2. **Guard logic difference**: FSM guards stricter than legacy
3. **Timing difference**: Orders placed at slightly different times
4. **Bug in FSM**: Logic error in phase handler

**Debug Steps**:

1. **Get divergence details**:
```bash
grep "DIVERGENCE" sessions/*/logs/bot_log_*.jsonl | tail -5
```

2. **Compare positions**:
```python
# Via HybridEngine
comparison = hybrid_engine.compare_engines()
print(comparison)
```

3. **Check what legacy has that FSM doesn't**:
```bash
# From comparison output
# "legacy_only": {"XRP/USDT", "DOGE/USDT"}
```

4. **Trace why FSM didn't buy**:
```bash
cat phase_events_*.jsonl | jq 'select(.symbol == "XRP/USDT") | {ts: .ts_ms, phase: .to_phase, note: .note}' | tail -20
```

**Solutions**:
- If consistent divergence: Investigate FSM logic bug
- If random divergence: Accept as timing variance
- If legacy always ahead: FSM may be more conservative (good)
- If FSM always ahead: Check if legacy is hitting rate limits

## Debugging Workflow

### Step-by-Step Diagnosis

1. **Identify Problem Scope**:
   - Single symbol or many?
   - Single phase or multiple?
   - Recent or ongoing?

2. **Check Rich Status Table**:
   - What phase is symbol in?
   - How long has it been there?
   - What's the note?

3. **Check Phase Event Log**:
   - What was the last transition?
   - What triggered it?
   - Are there repeated transitions?

4. **Check Error Logs**:
   - Any exceptions?
   - API errors?
   - Validation errors?

5. **Check Prometheus Metrics** (if enabled):
   - Is this symbol stuck longer than others?
   - Is this phase normally slow?
   - Are errors increasing?

6. **Compare with Legacy** (if in hybrid mode):
   - Does legacy engine have same issue?
   - Is this FSM-specific?

7. **Check Configuration**:
   - Are timeouts too short?
   - Are thresholds unrealistic?
   - Are guards too strict?

8. **Apply Fix**:
   - Adjust config
   - Manual intervention (Telegram)
   - Code fix if bug found

9. **Verify Fix**:
   - Watch for transition
   - Check metrics improve
   - Monitor for recurrence

## Emergency Actions

### Force Exit All Positions
```python
# Via Telegram
/exit_all

# Or via code
for symbol in engine.get_positions():
    engine.exit_position(symbol, reason="MANUAL_EMERGENCY_EXIT")
```

### Reset Stuck Symbol
```python
# Via phase event API (if implemented)
engine.fsm.reset_symbol("BTC/USDT", to_phase=Phase.IDLE, reason="manual_reset")
```

### Disable FSM (Emergency Rollback)
```python
# config.py
FSM_ENABLED = False  # Fall back to legacy engine
# Restart bot
```

### Clear Error State
```python
# Via code or Telegram command
for symbol, state in engine.fsm.get_states_by_phase(Phase.ERROR):
    set_phase(state, Phase.IDLE, note="manual_error_clear")
```

## Performance Debugging

### High CPU Usage

**Check**:
```bash
# Count phase transitions per second
cat phase_events_*.jsonl | jq -r '.ts_ms' | awk '{print int($1/1000)}' | uniq -c
```

**Common causes**:
- Too many symbols in watchlist
- Cycle sleep too short
- Rich table refresh rate too high

**Solutions**:
- Reduce watchlist size
- Increase cycle sleep (e.g., 0.5s → 1.0s)
- Lower `RICH_TABLE_REFRESH_HZ` (2.0 → 1.0)

### High Memory Usage

**Check**:
```python
# Via memory manager (if enabled)
memory_status = memory_manager.get_memory_status()
print(memory_status)
```

**Common causes**:
- Phase history accumulation
- Event log buffer not flushing
- Too many symbols tracked

**Solutions**:
- Limit `phase_history` size in CoinState
- Reduce `PHASE_LOG_BUFFER_SIZE`
- Reduce watchlist size

### High Disk I/O

**Check**:
```bash
# Monitor write rate
watch -n1 'lsof -p $(pgrep -f main.py) | grep phase_events'
```

**Common causes**:
- Buffer size too small
- Log level too verbose
- Metrics scraping too frequent

**Solutions**:
- Increase `PHASE_LOG_BUFFER_SIZE` (8192 → 16384)
- Reduce log level
- Increase Prometheus scrape interval

## Logging Best Practices

### What to Log

**Always log**:
- Phase transitions (automatic)
- Order placements and fills
- Errors and exceptions
- Manual interventions

**Conditionally log** (debug mode):
- Guard evaluations
- Price updates
- Market data fetches
- API call durations

**Never log** (security):
- API keys
- Order IDs with full details
- Account balances (in production)

### Log Levels

- **DEBUG**: Phase evaluation details, guard checks
- **INFO**: Phase transitions, order events
- **WARNING**: Timeouts, retries, partial fills
- **ERROR**: Exceptions, API failures, invalid state

### Structured Logging

**Good**:
```python
logger.info("Order placed", extra={
    'event_type': 'ORDER_PLACED',
    'symbol': symbol,
    'order_id': order_id,
    'side': 'buy',
    'amount': amount,
    'price': price
})
```

**Bad**:
```python
logger.info(f"Placed buy order for {symbol}: {amount}@{price}")
```

## See Also
- [FSM Architecture](FSM_ARCHITECTURE.md) - Design and phase flow
- [FSM Metrics Reference](FSM_METRICS.md) - Prometheus metrics and queries
- [CONFIG_README.md](../CONFIG_README.md) - Configuration reference
