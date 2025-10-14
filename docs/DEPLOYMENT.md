# Deployment Guide - Order Flow Hardening & Terminal UI

**Version**: 1.0
**Date**: 2025-10-14
**Branch**: `feat/order-flow-hardening`
**Target**: Production

---

## 📋 Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Deployment Steps](#deployment-steps)
3. [Rollout Strategy](#rollout-strategy)
4. [Monitoring & Verification](#monitoring--verification)
5. [Rollback Procedures](#rollback-procedures)
6. [Post-Deployment Tasks](#post-deployment-tasks)

---

## ✅ Pre-Deployment Checklist

### Code Quality
- [x] All integration tests passing (test_integration_order_flow.py)
- [x] All performance tests passing (test_performance_features.py)
- [x] All unit tests passing (test_order_flow_hardening.py)
- [x] Terminal UI validation tests passing (test_terminal_ui.py)
- [x] No critical linting errors
- [x] Code reviewed and approved

### Configuration
- [ ] Feature flags reviewed and documented (FEATURE_FLAGS.md)
- [ ] Config backup created (`config.py` → `config_backup.py`)
- [ ] State directories exist and writable:
  - [ ] `state/coid_registry.json`
  - [ ] `sessions/<timestamp>/`
- [ ] Log directories configured and writable
- [ ] Disk space sufficient (>5GB free recommended)

### Dependencies
- [ ] Python 3.10+ installed
- [ ] All dependencies installed (`requirements.txt`)
- [ ] Rich library available for terminal UI (optional but recommended)
- [ ] pythonjsonlogger available for logging (required)

### Backup
- [ ] Database backup completed (if applicable)
- [ ] Configuration backup completed
- [ ] Previous version tagged in git
- [ ] Rollback plan documented and tested

### Testing Environment
- [ ] Deployment tested in staging/paper trading mode
- [ ] Performance benchmarks established
- [ ] Load testing completed (100 parallel requests)
- [ ] Memory profiling completed

---

## 🚀 Deployment Steps

### Step 1: Prepare Environment

```bash
# 1. Navigate to project directory
cd "/path/to/trading-bot-professional"

# 2. Checkout deployment branch
git checkout feat/order-flow-hardening

# 3. Pull latest changes
git pull origin feat/order-flow-hardening

# 4. Verify branch and commit
git log -1
# Expected: d410df7 "feat: Complete Terminal UI implementation..."

# 5. Backup current config
cp config.py config_backup_$(date +%Y%m%d_%H%M%S).py

# 6. Create state directories if not exists
mkdir -p state
mkdir -p sessions
mkdir -p logs
```

### Step 2: Install Dependencies

```bash
# Install/update dependencies
pip install -r requirements.txt

# Verify critical dependencies
python3 -c "import rich; print('Rich:', rich.__version__)"
python3 -c "from pythonjsonlogger import jsonlogger; print('pythonjsonlogger: OK')"
```

### Step 3: Validate Configuration

```bash
# Run config validation
python3 -c "import config; config.validate_config_schema(); print('Config validation: PASSED')"

# Run validation tests
python3 tests/validate_terminal_ui.py
# Expected: "✓ All validations PASSED"
```

### Step 4: Run Test Suite

```bash
# Run integration tests
python3 tests/test_integration_order_flow.py
# Expected: "✓ All integration tests passed!"

# Run performance tests
python3 tests/test_performance_features.py
# Expected: "✓ All performance tests passed!"
```

### Step 5: Deploy with Feature Flags (Gradual Rollout)

#### Phase 1: Core Safety (Day 1)

**Enable in `config.py`**:
```python
# Phase 1: Core Safety Features
ENABLE_COID_MANAGER = True
ENABLE_STARTUP_RECONCILE = True
USE_FIRST_FILL_TS_FOR_TTL = True
ENABLE_SYMBOL_LOCKS = True

# Keep other features disabled initially
ENABLE_ENTRY_SLIPPAGE_GUARD = False
ENABLE_SPREAD_GUARD_ENTRY = False
ENABLE_DEPTH_GUARD_ENTRY = False
ENABLE_FILL_TELEMETRY = False
ENABLE_RICH_LOGGING = False
ENABLE_LIVE_MONITORS = False
```

**Start bot**:
```bash
# Start in observe mode for 1 hour
python3 main.py
# Monitor logs for any issues
```

**Verification**:
- Check `state/coid_registry.json` is created
- Verify no duplicate order logs
- Confirm symbol locks working (no race conditions)
- Monitor for 24 hours

#### Phase 2: Entry Guards (Day 2-3)

**Enable in `config.py`**:
```python
# Phase 2: Entry Guards
ENABLE_ENTRY_SLIPPAGE_GUARD = True
ENABLE_SPREAD_GUARD_ENTRY = True
ENABLE_DEPTH_GUARD_ENTRY = True
ENABLE_CONSOLIDATED_ENTRY_GUARDS = True
```

**Verification**:
- Monitor guard block reasons in logs
- Check fill rate impact (<10% reduction acceptable)
- Verify average entry slippage reduced
- Monitor for 48 hours

#### Phase 3: Telemetry & Exits (Day 4-5)

**Enable in `config.py`**:
```python
# Phase 3: Telemetry & Exits
ENABLE_FILL_TELEMETRY = True
ENABLE_CONSOLIDATED_EXITS = True
```

**Verification**:
- Check fill telemetry statistics
- Verify memory usage (<10MB increase)
- Monitor exit performance
- Monitor for 48 hours

#### Phase 4: Terminal UI (Day 6-7)

**Enable in `config.py`**:
```python
# Phase 4: Terminal UI
ENABLE_RICH_LOGGING = True
ENABLE_LIVE_MONITORS = True
ENABLE_LIVE_HEARTBEAT = True
ENABLE_LIVE_DASHBOARD = True
LIVE_MONITOR_REFRESH_S = 2.0
```

**Verification**:
- Check terminal output is colored and formatted
- Verify live monitors rendering correctly
- Monitor CPU usage (<5% increase)
- Monitor for 48 hours

### Step 6: Full Production (Day 8+)

**All features enabled**:
```python
# Full production configuration
ENABLE_COID_MANAGER = True
ENABLE_STARTUP_RECONCILE = True
USE_FIRST_FILL_TS_FOR_TTL = True
ENABLE_SYMBOL_LOCKS = True
ENABLE_ENTRY_SLIPPAGE_GUARD = True
ENABLE_SPREAD_GUARD_ENTRY = True
ENABLE_DEPTH_GUARD_ENTRY = True
ENABLE_CONSOLIDATED_ENTRY_GUARDS = True
ENABLE_FILL_TELEMETRY = True
ENABLE_CONSOLIDATED_EXITS = True
ENABLE_RICH_LOGGING = True
ENABLE_LIVE_MONITORS = True
ENABLE_LIVE_HEARTBEAT = True
ENABLE_LIVE_DASHBOARD = True
ENABLE_ORDER_FLOW_HARDENING = True  # Master switch
```

---

## 📊 Rollout Strategy

### Conservative Rollout (Recommended)

**Timeline**: 7-10 days

| Day | Phase | Features Enabled | Monitoring Focus |
|-----|-------|-----------------|------------------|
| 1 | Core Safety | COID, Locks, TTL | Duplicate orders, deadlocks |
| 2-3 | Entry Guards | Slippage, Spread, Depth | Fill rate, entry quality |
| 4-5 | Telemetry | Fill tracking, Exit eval | Memory usage, performance |
| 6-7 | Terminal UI | Rich logging, Live monitors | CPU usage, UX |
| 8+ | Full Production | All features | Overall stability |

**Success Criteria for Each Phase**:
- No critical errors for 24 hours
- Performance metrics within acceptable range
- User/operator feedback positive

---

### Aggressive Rollout (High Risk)

**Timeline**: 1-2 days

⚠️ **Only use if**:
- Extensive testing completed
- Rollback plan verified
- Team available for monitoring

**Day 1**: Enable all core features (Phases 1-2)
**Day 2**: Enable remaining features (Phases 3-4)

---

## 📈 Monitoring & Verification

### Key Metrics to Monitor

#### Order Flow
```bash
# Check for duplicate orders
grep "COID_DUPLICATE" logs/*.jsonl

# Check entry slippage
grep "ENTRY_SLIPPAGE" logs/*.jsonl | jq '.slippage_bps' | awk '{sum+=$1; count++} END {print "Avg slippage:", sum/count, "bps"}'

# Check fill rate
grep "ORDER_FILLED" logs/*.jsonl | wc -l
grep "ORDER_PARTIAL" logs/*.jsonl | wc -l
```

#### Performance
```bash
# Check API throttling
grep "RATE_LIMIT_THROTTLE" logs/*.jsonl | wc -l

# Check cache hit rate
grep "CACHE_HIT" logs/*.jsonl | wc -l
grep "CACHE_MISS" logs/*.jsonl | wc -l

# Check memory usage
ps aux | grep "main.py" | awk '{print $6/1024 " MB"}'
```

#### System Health
```bash
# Check for errors
grep "ERROR" logs/*.jsonl | tail -20

# Check for warnings
grep "WARNING" logs/*.jsonl | tail -20

# Check COID registry size
wc -l state/coid_registry.json
```

### Alert Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Duplicate Orders | >0 | >3 per hour | Disable COID Manager, investigate |
| Entry Slippage | >20 bps | >50 bps | Review slippage guard threshold |
| Fill Rate Drop | <80% | <70% | Review guard thresholds |
| API Throttle Rate | >15% | >30% | Reduce request rate |
| Memory Growth | >100MB/day | >500MB/day | Investigate leak |
| CPU Usage Increase | >10% | >25% | Disable live monitors |

---

## 🔄 Rollback Procedures

### Emergency Rollback (Critical Issues)

**Symptoms**: Duplicate orders, deadlocks, crashes

```bash
# 1. Stop bot immediately
pkill -f "python3 main.py"

# 2. Revert to previous version
git checkout main  # or previous stable tag
cp config_backup_YYYYMMDD_HHMMSS.py config.py

# 3. Restart bot
python3 main.py

# 4. Verify rollback successful
tail -f logs/bot_log_*.jsonl
```

### Partial Rollback (Performance Issues)

**Symptoms**: High latency, excessive throttling

```python
# Edit config.py - disable problematic features only
ENABLE_ENTRY_SLIPPAGE_GUARD = False  # If causing fill rate issues
ENABLE_SPREAD_GUARD_ENTRY = False
ENABLE_DEPTH_GUARD_ENTRY = False

# OR

ENABLE_LIVE_MONITORS = False  # If causing CPU issues
ENABLE_RICH_LOGGING = False
```

**Restart bot**:
```bash
# Graceful restart
pkill -SIGTERM -f "python3 main.py"
sleep 5
python3 main.py
```

### Rollback Decision Matrix

| Issue | Severity | Action | Rollback Type |
|-------|----------|--------|---------------|
| Duplicate Orders | Critical | Immediate stop | Full rollback |
| Deadlock | Critical | Immediate stop | Full rollback |
| Fill Rate <70% | High | Disable guards | Partial rollback |
| High CPU (>25%) | Medium | Disable UI | Partial rollback |
| Memory Leak | Medium | Disable telemetry | Partial rollback |
| Minor Errors | Low | Monitor | No rollback |

---

## 📝 Post-Deployment Tasks

### Day 1-7 (Monitoring Period)

**Daily Tasks**:
- [ ] Review error logs (`grep "ERROR" logs/*.jsonl`)
- [ ] Check key metrics dashboard
- [ ] Verify no duplicate orders
- [ ] Monitor memory usage trend
- [ ] Check fill rate vs baseline

**Weekly Tasks**:
- [ ] Generate performance report
- [ ] Review telemetry data
- [ ] Collect user feedback
- [ ] Update documentation if needed

### Week 2+ (Optimization Period)

**Tasks**:
- [ ] Fine-tune guard thresholds based on data
- [ ] Optimize cache TTL settings
- [ ] Adjust rate limit capacity if needed
- [ ] Review and cleanup old COID entries
- [ ] Generate deployment postmortem

### Documentation Updates

- [ ] Update `CHANGELOG.md` with deployment notes
- [ ] Document any issues encountered and resolutions
- [ ] Update `MONITORING.md` with new metrics
- [ ] Create runbook for common issues
- [ ] Update team training materials

---

## 🔗 Integration with CI/CD

### Automated Deployment Pipeline

```yaml
# .github/workflows/deploy.yml (example)
name: Deploy to Production

on:
  push:
    branches: [main]
    tags: ['v*']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Run Tests
        run: |
          python3 tests/test_integration_order_flow.py
          python3 tests/test_performance_features.py

      - name: Deploy to Production
        run: |
          # Custom deployment script
          ./scripts/deploy_production.sh

      - name: Verify Deployment
        run: |
          # Health check
          curl -f http://localhost:8000/health || exit 1
```

---

## 📞 Support & Escalation

### Issue Reporting

**During Deployment**:
- Monitor: `#trading-bot-ops` Slack channel
- Report issues: Create incident ticket
- Emergency contact: On-call engineer

**Post-Deployment**:
- Bug reports: GitHub Issues
- Feature requests: GitHub Discussions
- Questions: Team Wiki

### Escalation Path

1. **Level 1**: Operator notices issue → Check runbook
2. **Level 2**: Unable to resolve → Page on-call engineer
3. **Level 3**: Critical production issue → Initiate emergency rollback

---

## 📚 Additional Resources

- [Feature Flags Reference](./FEATURE_FLAGS.md)
- [Monitoring Guide](./MONITORING.md)
- [Troubleshooting Guide](./TROUBLESHOOTING.md)
- [Architecture Overview](./ARCHITECTURE.md)
- [API Documentation](./API.md)

---

## ✅ Deployment Sign-Off

**Deployed By**: _________________
**Date**: _________________
**Version**: feat/order-flow-hardening (commit d410df7)
**Rollout Phase**: _________________
**Issues Encountered**: _________________
**Resolution**: _________________

**Approved By**:
- [ ] Tech Lead
- [ ] DevOps
- [ ] QA

---

**Last Updated**: 2025-10-14
**Maintainer**: Trading Bot Team
