"""
Simple Buy Flow Logger - Logs every step for every coin

This logger creates a simple log file that shows all evaluation steps
for each symbol, making it easy to debug why symbols are bought or blocked.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class BuyFlowLogger:
    """
    Simple logger that tracks buy decision flow step-by-step.

    Creates logs like:
    [15:23:42.123] ========== BTC/USDT - Start Evaluation ==========
    [15:23:42.145] ✅ Step 1: Max Positions Check - PASS (0/10 positions)
    [15:23:42.156] ✅ Step 2: Symbol in Positions - PASS (not held)
    ...
    [15:23:42.789] ========== BTC/USDT - BUY COMPLETED (666ms) ==========
    """

    def __init__(self, log_file: Optional[str] = None):
        """
        Initialize BuyFlowLogger

        Args:
            log_file: Path to log file. If None, creates logs/buy_flow_{date}.log
        """
        if log_file is None:
            # Create logs directory if it doesn't exist
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)

            date_str = datetime.now().strftime("%Y-%m-%d")
            log_file = str(log_dir / f"buy_flow_{date_str}.log")

        self.log_file = log_file
        self.logger = logging.getLogger("BuyFlowLogger")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # Don't propagate to root logger

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # File handler with millisecond precision
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter(
            '[%(asctime)s.%(msecs)03d] %(message)s',
            datefmt='%H:%M:%S'
        )
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        # Track current symbol and start time
        self.current_symbol: Optional[str] = None
        self.start_time: Optional[float] = None
        self.current_status: str = "idle"  # idle, evaluating, buying, blocked, skipped

        # Symbol-specific status tracking for terminal display
        self._symbol_status: Dict[str, str] = {}

        self.logger.info("=" * 80)
        self.logger.info(f"Buy Flow Logger initialized - Log file: {log_file}")
        self.logger.info("=" * 80)

    def start_evaluation(self, symbol: str):
        """Start logging for a symbol"""
        import time
        self.current_symbol = symbol
        self.start_time = time.time()
        self.current_status = "evaluating"
        self._symbol_status[symbol] = "Step 1: Starting"
        self.logger.info(f"========== {symbol} - Start Evaluation ==========")

    def step(self, step_num: int, step_name: str, status: str, details: str = ""):
        """
        Log a single step in the evaluation process

        Args:
            step_num: Step number (1-16)
            step_name: Name of the step (e.g., "Max Positions Check")
            status: Step result - "PASS", "BLOCKED", "NO TRIGGER", "WAITING", "SUCCESS", "FAILED", "ERROR"
            details: Additional details to show in parentheses
        """
        # Choose icon based on status
        if status in ["PASS", "SUCCESS", "TRIGGERED"]:
            icon = "✅"
        elif status in ["BLOCKED", "FAILED", "ERROR"]:
            icon = "❌"
        elif status in ["NO TRIGGER", "WAITING"]:
            icon = "⏸️"
        else:
            icon = "ℹ️"

        # Build message
        msg = f"{icon} Step {step_num}: {step_name} - {status}"
        if details:
            msg += f" ({details})"

        self.logger.info(msg)

        # Update current status for terminal display
        if self.current_symbol:
            short_status = f"Step {step_num}: {step_name[:20]}"
            self._symbol_status[self.current_symbol] = short_status

            # Update overall status based on step outcome
            if status in ["BLOCKED", "FAILED", "ERROR"]:
                self.current_status = "blocked"
            elif status == "NO TRIGGER":
                self.current_status = "skipped"
            elif step_num >= 14 and status in ["SUCCESS", "PASS"]:  # Order placement/fill steps
                self.current_status = "buying"

    def end_evaluation(self, outcome: str, duration_ms: float, reason: str = ""):
        """
        End logging for a symbol

        Args:
            outcome: Final outcome - "BUY_COMPLETED", "BLOCKED", "SKIPPED", "ERROR"
            duration_ms: Evaluation duration in milliseconds
            reason: Reason for the outcome (for BLOCKED/SKIPPED/ERROR)
        """
        # Build outcome message
        if outcome == "BUY_COMPLETED":
            outcome_msg = "BUY COMPLETED"
            self.current_status = "completed"
        elif outcome == "BLOCKED":
            outcome_msg = f"BLOCKED: {reason}"
            self.current_status = "blocked"
        elif outcome == "SKIPPED":
            outcome_msg = f"SKIPPED: {reason}"
            self.current_status = "skipped"
        elif outcome == "ERROR":
            outcome_msg = f"ERROR: {reason}"
            self.current_status = "error"
        else:
            outcome_msg = outcome
            self.current_status = "unknown"

        self.logger.info(
            f"========== {self.current_symbol} - {outcome_msg} ({duration_ms:.0f}ms) =========="
        )
        self.logger.info("")  # Empty line for readability

        # Update symbol status
        if self.current_symbol:
            self._symbol_status[self.current_symbol] = f"✓ {outcome_msg}"

        # Reset state
        self.current_symbol = None
        self.start_time = None
        self.current_status = "idle"

    def get_symbol_status(self, symbol: str) -> str:
        """
        Get the current status of a symbol for terminal display

        Returns:
            Status string like "Step 5: Market Guards" or "✓ BUY COMPLETED"
        """
        return self._symbol_status.get(symbol, "—")

    def get_current_status(self) -> str:
        """Get the current overall status (idle, evaluating, buying, blocked, skipped, error)"""
        return self.current_status

    def close(self):
        """Close the logger and release file handles"""
        self.logger.info("=" * 80)
        self.logger.info("Buy Flow Logger shutdown")
        self.logger.info("=" * 80)

        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)


# Singleton instance
_buy_flow_logger: Optional[BuyFlowLogger] = None


def get_buy_flow_logger() -> BuyFlowLogger:
    """Get or create the global BuyFlowLogger instance"""
    global _buy_flow_logger
    if _buy_flow_logger is None:
        _buy_flow_logger = BuyFlowLogger()
    return _buy_flow_logger


def shutdown_buy_flow_logger():
    """Shutdown the global BuyFlowLogger instance"""
    global _buy_flow_logger
    if _buy_flow_logger is not None:
        _buy_flow_logger.close()
        _buy_flow_logger = None
