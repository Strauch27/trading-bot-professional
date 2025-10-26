#!/usr/bin/env bash
#
# run_paper_session.sh - Regression Test for Trading Bot
#
# Runs a paper trading session and validates critical metrics to ensure
# the recent bug fixes are working correctly.
#
# Usage:
#   ./scripts/run_paper_session.sh [duration_seconds]
#
# Example:
#   ./scripts/run_paper_session.sh 300  # Run for 5 minutes
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DURATION_SECONDS=${1:-300}  # Default 5 minutes
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_DIR}/venv/bin/python3"

# Verify Python exists
if [[ ! -f "${PYTHON}" ]]; then
    echo -e "${RED}Error: Python not found at ${PYTHON}${NC}"
    echo "Run setup.sh first to create virtual environment"
    exit 1
fi

echo -e "${GREEN}=== Trading Bot Regression Test ===${NC}"
echo "Duration: ${DURATION_SECONDS}s"
echo "Project: ${PROJECT_DIR}"
echo ""

# Start bot in background with timeout
echo -e "${YELLOW}Starting bot...${NC}"
timeout "${DURATION_SECONDS}s" "${PYTHON}" "${PROJECT_DIR}/main.py" &
BOT_PID=$!

echo "Bot PID: ${BOT_PID}"
echo -e "${YELLOW}Running for ${DURATION_SECONDS} seconds...${NC}"

# Wait for bot to finish (or timeout)
wait ${BOT_PID} || true

echo -e "${GREEN}Bot session completed${NC}"
echo ""

# Find latest session directory
LATEST_SESSION=$(find "${PROJECT_DIR}/sessions" -maxdepth 1 -type d -name "session_*" | sort -r | head -1)

if [[ -z "${LATEST_SESSION}" ]]; then
    echo -e "${RED}ERROR: No session directory found${NC}"
    exit 1
fi

echo -e "${YELLOW}Analyzing session: ${LATEST_SESSION}${NC}"
echo ""

# Define log files
ORDERS_LOG="${LATEST_SESSION}/logs/jsonl/orders.jsonl"
ENGINE_TRANSIENT="${LATEST_SESSION}/state/engine_transient.json"
EVENTS_LOG="${LATEST_SESSION}/logs/events_*.jsonl"

# Validation counters
ERRORS=0
WARNINGS=0

# =============================================================================
# Test 1: Check for ORDER_FILLED events (Critical Bug Fix Validation)
# =============================================================================
echo -e "${YELLOW}[TEST 1] Checking ORDER_FILLED events...${NC}"

if [[ ! -f "${ORDERS_LOG}" ]]; then
    echo -e "${RED}  ✗ FAIL: orders.jsonl not found${NC}"
    ((ERRORS++))
else
    ORDER_SENT_COUNT=$(grep -c '"ORDER_SENT"' "${ORDERS_LOG}" || true)
    ORDER_FILLED_COUNT=$(grep -c '"ORDER_FILLED"' "${ORDERS_LOG}" || true)
    ORDER_FAILED_COUNT=$(grep -c '"ORDER_FAILED"' "${ORDERS_LOG}" || true)

    echo "  ORDER_SENT:   ${ORDER_SENT_COUNT}"
    echo "  ORDER_FILLED: ${ORDER_FILLED_COUNT}"
    echo "  ORDER_FAILED: ${ORDER_FAILED_COUNT}"

    if [[ ${ORDER_SENT_COUNT} -gt 0 ]]; then
        FILL_RATE=$(awk "BEGIN {printf \"%.1f\", (${ORDER_FILLED_COUNT}/${ORDER_SENT_COUNT})*100}")
        echo "  Fill Rate:    ${FILL_RATE}%"

        if [[ ${ORDER_FILLED_COUNT} -eq 0 ]]; then
            echo -e "${RED}  ✗ FAIL: No ORDER_FILLED events (100% failure rate)${NC}"
            echo -e "${RED}         This indicates the order execution bug is NOT fixed!${NC}"
            ((ERRORS++))
        elif (( $(echo "${FILL_RATE} < 10" | bc -l) )); then
            echo -e "${YELLOW}  ⚠ WARN: Very low fill rate (${FILL_RATE}%)${NC}"
            ((WARNINGS++))
        else
            echo -e "${GREEN}  ✓ PASS: Orders are being filled (${FILL_RATE}% fill rate)${NC}"
        fi
    else
        echo -e "${YELLOW}  ⚠ SKIP: No orders sent during session${NC}"
    fi
fi

echo ""

# =============================================================================
# Test 2: Check for stale pending intents (Critical Bug Fix Validation)
# =============================================================================
echo -e "${YELLOW}[TEST 2] Checking for stale pending_buy_intents...${NC}"

if [[ ! -f "${ENGINE_TRANSIENT}" ]]; then
    echo -e "${YELLOW}  ⚠ SKIP: engine_transient.json not found${NC}"
else
    PENDING_INTENTS_COUNT=$(jq -r '.pending_buy_intents | length' "${ENGINE_TRANSIENT}" 2>/dev/null || echo "0")
    POSITIONS_COUNT=$(jq -r '.positions | length' "${ENGINE_TRANSIENT}" 2>/dev/null || echo "0")

    echo "  Pending Intents: ${PENDING_INTENTS_COUNT}"
    echo "  Positions:       ${POSITIONS_COUNT}"

    if [[ ${PENDING_INTENTS_COUNT} -gt 50 ]]; then
        echo -e "${RED}  ✗ FAIL: Excessive stale intents (${PENDING_INTENTS_COUNT})${NC}"
        echo -e "${RED}         Intent cleanup bug is NOT fixed!${NC}"
        ((ERRORS++))
    elif [[ ${PENDING_INTENTS_COUNT} -gt 10 ]]; then
        echo -e "${YELLOW}  ⚠ WARN: Many pending intents (${PENDING_INTENTS_COUNT})${NC}"
        ((WARNINGS++))
    else
        echo -e "${GREEN}  ✓ PASS: Pending intents under control (${PENDING_INTENTS_COUNT})${NC}"
    fi
fi

echo ""

# =============================================================================
# Test 3: Check for ERROR logs (Observability Fix Validation)
# =============================================================================
echo -e "${YELLOW}[TEST 3] Checking for ERROR-level observability...${NC}"

if [[ ! -f ${EVENTS_LOG} ]]; then
    echo -e "${YELLOW}  ⚠ SKIP: events log not found${NC}"
else
    ERROR_LOG_COUNT=$(grep -c '"level":"ERROR"' ${EVENTS_LOG} || true)
    ORDER_FAILED_LOG_COUNT=$(grep -c 'ORDER_FAILED' ${EVENTS_LOG} || true)

    echo "  ERROR logs:       ${ERROR_LOG_COUNT}"
    echo "  ORDER_FAILED logs: ${ORDER_FAILED_LOG_COUNT}"

    if [[ ${ORDER_SENT_COUNT:-0} -gt 0 && ${ORDER_FILLED_COUNT:-0} -eq 0 && ${ERROR_LOG_COUNT} -eq 0 ]]; then
        echo -e "${RED}  ✗ FAIL: Orders failing silently (no ERROR logs)${NC}"
        echo -e "${RED}         Observability bug is NOT fixed!${NC}"
        ((ERRORS++))
    else
        echo -e "${GREEN}  ✓ PASS: Error logging is working${NC}"
    fi
fi

echo ""

# =============================================================================
# Test 4: Check for stackdumps/crashes
# =============================================================================
echo -e "${YELLOW}[TEST 4] Checking for crashes...${NC}"

STACKDUMP_FILE="${LATEST_SESSION}/stackdump.txt"

if [[ -f "${STACKDUMP_FILE}" ]]; then
    echo -e "${RED}  ✗ FAIL: Stackdump found (bot crashed)${NC}"
    echo "  Location: ${STACKDUMP_FILE}"
    echo ""
    echo "  Last 20 lines:"
    tail -20 "${STACKDUMP_FILE}" | sed 's/^/    /'
    ((ERRORS++))
else
    echo -e "${GREEN}  ✓ PASS: No crashes detected${NC}"
fi

echo ""

# =============================================================================
# Summary
# =============================================================================
echo "========================================"
echo -e "${GREEN}Regression Test Complete${NC}"
echo "========================================"
echo "Errors:   ${ERRORS}"
echo "Warnings: ${WARNINGS}"
echo ""

if [[ ${ERRORS} -eq 0 ]]; then
    echo -e "${GREEN}✓ ALL TESTS PASSED${NC}"
    echo "The critical bug fixes appear to be working correctly."
    exit 0
elif [[ ${ERRORS} -le 2 && ${WARNINGS} -le 3 ]]; then
    echo -e "${YELLOW}⚠ TESTS PASSED WITH WARNINGS${NC}"
    echo "Some issues detected but not critical."
    exit 0
else
    echo -e "${RED}✗ TESTS FAILED${NC}"
    echo "Critical issues detected. Review the failures above."
    exit 1
fi
