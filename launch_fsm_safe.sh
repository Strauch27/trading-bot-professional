#!/usr/bin/env bash
#
# SAFE FSM LAUNCH SCRIPT
# ======================
# Launches trading bot in FSM mode with all safety checks
#
# USAGE:
#   ./launch_fsm_safe.sh --paper-trading
#   ./launch_fsm_safe.sh --live  # Only after all checks pass!
#
# REQUIREMENTS:
# - All P0-P3 fixes implemented
# - config_production_ready.patch applied
# - All smoke tests passed
# - Pre-flight checklist completed
#

set -euo pipefail  # Exit on error, undefined vars, pipe failures

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_section() {
    echo ""
    echo "================================================================"
    echo "$1"
    echo "================================================================"
}

# Check if running in paper trading or live mode
MODE="unknown"
FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --paper-trading)
            MODE="paper"
            shift
            ;;
        --live)
            MODE="live"
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--paper-trading|--live] [--force]"
            exit 1
            ;;
    esac
done

if [[ "$MODE" == "unknown" ]]; then
    log_error "Mode not specified. Use --paper-trading or --live"
    exit 1
fi

log_section "FSM SAFE LAUNCH - MODE: $MODE"

# ===================================================================
# PHASE 1: PRE-FLIGHT CHECKS
# ===================================================================

log_section "PHASE 1: Pre-Flight Checks"

# Check Python version
log_info "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 8 ]]; then
    log_error "Python 3.8+ required (found: $PYTHON_VERSION)"
    exit 1
fi
log_success "Python $PYTHON_VERSION detected"

# Check required files exist
log_info "Checking required files..."
REQUIRED_FILES=(
    "config.py"
    "main.py"
    "services/buy_service.py"
    "services/sell_service.py"
    "services/order_service.py"
    "core/fsm/actions.py"
    "core/fsm/fsm_machine.py"
    "core/fsm/recovery.py"
    "tests/smoke_tests.py"
)

for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        log_error "Required file missing: $file"
        exit 1
    fi
done
log_success "All required files present"

# Check critical fixes are in place (P0-P3)
log_info "Verifying critical fixes..."

# P0 Fix #1: Preflight in Legacy Buy
if grep -q "CRITICAL FIX (P0 Issue #1)" services/buy_service.py; then
    log_success "P0 Fix #1: Legacy Preflight - VERIFIED"
else
    log_error "P0 Fix #1: Legacy Preflight - MISSING"
    exit 1
fi

# P0 Fix #2: PARTIAL Fill Handling
if grep -q "CRITICAL FIX (P0 Issue #2)" services/buy_service.py; then
    log_success "P0 Fix #2: PARTIAL Fill Handling - VERIFIED"
else
    log_error "P0 Fix #2: PARTIAL Fill Handling - MISSING"
    exit 1
fi

# P1 Fix #3: FSM Preflight Abort
if grep -q "FSMTransitionAbort" core/fsm/exceptions.py; then
    log_success "P1 Fix #3: FSM Preflight Abort - VERIFIED"
else
    log_error "P1 Fix #3: FSM Preflight Abort - MISSING"
    exit 1
fi

# P1 Fix #4: COID Duplicate Check
if grep -q "_fetch_order_by_coid" services/order_service.py; then
    log_success "P1 Fix #4: COID Duplicate Recovery - VERIFIED"
else
    log_error "P1 Fix #4: COID Duplicate Recovery - MISSING"
    exit 1
fi

# P1 Fix #8: Sell Slippage Pre-Check
if grep -q "FLASH_CRASH_PROTECTION" core/fsm/actions.py; then
    log_success "P1 Fix #8: Flash Crash Protection - VERIFIED"
else
    log_error "P1 Fix #8: Flash Crash Protection - MISSING"
    exit 1
fi

# P1 Fix #9: Portfolio Cleanup Atomicity
if grep -q "with self.engine._lock:" engine/exit_handler.py; then
    log_success "P1 Fix #9: Atomic Portfolio Cleanup - VERIFIED"
else
    log_warn "P1 Fix #9: Atomic Portfolio Cleanup - MAY BE MISSING (check manually)"
fi

# P2 Fix #7: Position TTL
if grep -q "check_position_ttl" core/fsm/timeouts.py; then
    log_success "P2 Fix #7: Position TTL - VERIFIED"
else
    log_warn "P2 Fix #7: Position TTL - MAY BE MISSING"
fi

# ===================================================================
# PHASE 2: CONFIG VALIDATION
# ===================================================================

log_section "PHASE 2: Configuration Validation"

log_info "Checking config.py settings..."

# Check FSM_ENABLED
FSM_ENABLED=$(grep "^FSM_ENABLED" config.py | cut -d= -f2 | tr -d ' ')
if [[ "$FSM_ENABLED" == "True" ]]; then
    log_success "FSM_ENABLED = True"
else
    log_error "FSM_ENABLED = $FSM_ENABLED (should be True)"
    if [[ "$FORCE" == false ]]; then
        exit 1
    else
        log_warn "Forcing launch despite FSM_ENABLED=$FSM_ENABLED"
    fi
fi

# Check NEVER_MARKET_SELLS (critical for safety)
NEVER_MARKET_SELLS=$(grep "^NEVER_MARKET_SELLS" config.py | cut -d= -f2 | tr -d ' ')
if [[ "$NEVER_MARKET_SELLS" == "True" ]]; then
    log_success "NEVER_MARKET_SELLS = True (SAFE)"
elif [[ "$MODE" == "paper" ]]; then
    log_warn "NEVER_MARKET_SELLS = $NEVER_MARKET_SELLS (should be True for paper trading)"
else
    log_error "NEVER_MARKET_SELLS = $NEVER_MARKET_SELLS (DANGEROUS for live)"
    if [[ "$FORCE" == false ]]; then
        exit 1
    fi
fi

# Check MAX_SLIPPAGE_BPS_EXIT
MAX_SLIPPAGE_EXIT=$(grep "^MAX_SLIPPAGE_BPS_EXIT" config.py | cut -d= -f2 | tr -d ' ')
if [[ "$MAX_SLIPPAGE_EXIT" -ge 500 ]]; then
    log_success "MAX_SLIPPAGE_BPS_EXIT = $MAX_SLIPPAGE_EXIT (OK)"
else
    log_warn "MAX_SLIPPAGE_BPS_EXIT = $MAX_SLIPPAGE_EXIT (recommended: 500)"
fi

# Check TRADE_TTL_MIN
TRADE_TTL=$(grep "^TRADE_TTL_MIN" config.py | cut -d= -f2 | tr -d ' ')
if [[ "$TRADE_TTL" -gt 0 ]]; then
    log_success "TRADE_TTL_MIN = $TRADE_TTL minutes"
else
    log_warn "TRADE_TTL_MIN not set or zero"
fi

# ===================================================================
# PHASE 3: SMOKE TESTS
# ===================================================================

log_section "PHASE 3: Smoke Tests"

log_info "Running smoke tests (A-E)..."

if python3 tests/smoke_tests.py --all --symbol BLESS/USDT; then
    log_success "All smoke tests PASSED"
else
    log_error "Smoke tests FAILED - fix issues before launch"
    exit 1
fi

# ===================================================================
# PHASE 4: ENVIRONMENT SETUP
# ===================================================================

log_section "PHASE 4: Environment Setup"

# Check API keys
log_info "Checking API credentials..."
if [[ -z "${MEXC_API_KEY:-}" ]]; then
    log_error "MEXC_API_KEY environment variable not set"
    exit 1
fi

if [[ -z "${MEXC_API_SECRET:-}" ]]; then
    log_error "MEXC_API_SECRET environment variable not set"
    exit 1
fi

log_success "API credentials configured"

# Create session directories
log_info "Creating session directories..."
mkdir -p sessions/
mkdir -p logs/
mkdir -p state/
mkdir -p reports/
log_success "Directories created"

# ===================================================================
# PHASE 5: FINAL CONFIRMATION
# ===================================================================

log_section "PHASE 5: Final Confirmation"

if [[ "$MODE" == "live" ]]; then
    log_warn "!!! LIVE MODE - REAL MONEY AT RISK !!!"
    log_warn "Have you completed the Pre-Flight Checklist?"
    log_warn "docs/PRE_FLIGHT_CHECKLIST.md"
    echo ""
    read -p "Type 'YES I AM READY' to continue with live trading: " CONFIRM

    if [[ "$CONFIRM" != "YES I AM READY" ]]; then
        log_error "Launch aborted by user"
        exit 1
    fi
else
    log_info "Paper Trading Mode - Minimal risk"
    log_info "Position size: Conservative (BLESS/USDT only)"
    echo ""
    read -p "Press ENTER to launch in paper trading mode... " CONFIRM
fi

# ===================================================================
# PHASE 6: LAUNCH
# ===================================================================

log_section "PHASE 6: Launching FSM Trading Bot"

# Prepare launch command
PYTHON_CMD="python3 main.py"
LAUNCH_ARGS=""

# Add symbol restriction for paper trading
if [[ "$MODE" == "paper" ]]; then
    LAUNCH_ARGS="$LAUNCH_ARGS --symbol BLESS/USDT"
fi

# Log launch details
log_info "Mode: $MODE"
log_info "Command: $PYTHON_CMD $LAUNCH_ARGS"
log_info "Config: config.py"
log_info "Logs: sessions/<timestamp>/logs/"

echo ""
log_success "Starting bot..."
echo ""

# Launch with monitoring
$PYTHON_CMD $LAUNCH_ARGS 2>&1 | tee -a launch.log

# Capture exit code
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    log_success "Bot exited cleanly"
else
    log_error "Bot exited with error code: $EXIT_CODE"
    log_error "Check launch.log for details"
fi

exit $EXIT_CODE
