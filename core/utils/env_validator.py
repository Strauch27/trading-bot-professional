#!/usr/bin/env python3
"""
Environment Variable Validator

Validates required environment variables at startup and provides clear error messages.
Implements fail-fast behavior for missing or invalid configuration.
"""

import os
import sys
import logging
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class EnvValidationError(Exception):
    """Raised when environment validation fails"""
    pass


class EnvValidator:
    """Validates environment variables with fail-fast behavior"""

    def __init__(self, env_file: str = ".env"):
        self.env_file = env_file
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self._load_env()

    def _load_env(self):
        """Load .env file if it exists"""
        env_path = Path(self.env_file)
        if env_path.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_path)
                logger.info(f"Loaded environment from {self.env_file}")
            except ImportError:
                logger.warning("python-dotenv not installed, skipping .env file loading")
            except Exception as e:
                logger.warning(f"Failed to load {self.env_file}: {e}")
        else:
            logger.info(f"No {self.env_file} file found, using system environment only")

    def require(self, var_name: str, description: str = "", validator: Optional[callable] = None) -> str:
        """
        Require an environment variable to be set.

        Args:
            var_name: Name of the environment variable
            description: Human-readable description
            validator: Optional validation function that takes value and returns (bool, error_msg)

        Returns:
            The environment variable value

        Raises:
            EnvValidationError: If variable is missing or invalid
        """
        value = os.environ.get(var_name)

        if not value:
            self.errors.append(
                f"❌ {var_name}: MISSING (Required)\n"
                f"   Description: {description or 'No description provided'}\n"
                f"   Set this in your .env file or environment"
            )
            return ""

        # Run validator if provided
        if validator:
            is_valid, error_msg = validator(value)
            if not is_valid:
                self.errors.append(
                    f"❌ {var_name}: INVALID\n"
                    f"   Value: {value[:50]}{'...' if len(value) > 50 else ''}\n"
                    f"   Error: {error_msg}"
                )
                return value

        logger.debug(f"✓ {var_name} validated")
        return value

    def optional(self, var_name: str, default: str = "", description: str = "") -> str:
        """
        Get an optional environment variable with a default value.

        Args:
            var_name: Name of the environment variable
            default: Default value if not set
            description: Human-readable description

        Returns:
            The environment variable value or default
        """
        value = os.environ.get(var_name, default)

        if value == default and default:
            logger.debug(f"ℹ️  {var_name} using default: {default}")
        elif not value:
            logger.debug(f"ℹ️  {var_name} not set (empty)")
        else:
            logger.debug(f"✓ {var_name} set")

        return value

    def validate_all(self, strict: bool = True) -> bool:
        """
        Validate all environment variables.

        Args:
            strict: If True, exit on validation errors. If False, just log.

        Returns:
            True if validation passed, False otherwise
        """
        if self.errors:
            error_msg = "\n\n" + "=" * 70 + "\n"
            error_msg += "🔴 ENVIRONMENT VALIDATION FAILED\n"
            error_msg += "=" * 70 + "\n\n"
            error_msg += "\n\n".join(self.errors)
            error_msg += "\n\n" + "=" * 70 + "\n"
            error_msg += "Fix the above issues and restart the bot.\n"
            error_msg += f"Example configuration: see {self.env_file}.example\n"
            error_msg += "=" * 70 + "\n"

            logger.error(error_msg)

            if strict:
                print(error_msg, file=sys.stderr)
                sys.exit(1)

            return False

        if self.warnings:
            warning_msg = "\n⚠️  Configuration Warnings:\n"
            warning_msg += "\n".join(f"  - {w}" for w in self.warnings)
            logger.warning(warning_msg)

        logger.info("✅ Environment validation passed")
        return True


def validate_api_key(value: str) -> Tuple[bool, str]:
    """Validate API key format"""
    if len(value) < 10:
        return False, "API key too short (minimum 10 characters)"
    if value.startswith("your_") or value == "your_api_key_here":
        return False, "Please replace placeholder with actual API key"
    return True, ""


def validate_api_secret(value: str) -> Tuple[bool, str]:
    """Validate API secret format"""
    if len(value) < 10:
        return False, "API secret too short (minimum 10 characters)"
    if value.startswith("your_") or value == "your_api_secret_here":
        return False, "Please replace placeholder with actual API secret"
    return True, ""


def validate_telegram_token(value: str) -> Tuple[bool, str]:
    """Validate Telegram bot token format"""
    if not value:
        return True, ""  # Optional field
    if value == "your_bot_token_here":
        return False, "Please replace placeholder with actual bot token"
    if ":" not in value:
        return False, "Invalid token format (should be like 123456:ABC-DEF...)"
    return True, ""


def validate_telegram_chat_id(value: str) -> Tuple[bool, str]:
    """Validate Telegram chat ID format"""
    if not value:
        return True, ""  # Optional field
    if value == "your_chat_id_here":
        return False, "Please replace placeholder with actual chat ID"
    if not value.lstrip("-").isdigit():
        return False, "Chat ID should be numeric (can start with -)"
    return True, ""


def validate_environment(strict: bool = True) -> Dict[str, str]:
    """
    Main environment validation function.

    Args:
        strict: If True, exit on validation errors

    Returns:
        Dictionary of validated environment variables

    Raises:
        SystemExit: If validation fails and strict=True
    """
    validator = EnvValidator()

    # Required: Exchange API Credentials
    api_key = validator.require(
        "API_KEY",
        "MEXC Exchange API Key for trading operations",
        validate_api_key
    )

    api_secret = validator.require(
        "API_SECRET",
        "MEXC Exchange API Secret for signing requests",
        validate_api_secret
    )

    # Optional: Logging
    log_level = validator.optional(
        "BOT_LOG_LEVEL",
        default="",
        description="Override log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)"
    )

    session_id = validator.optional(
        "BOT_SESSION_ID",
        default="",
        description="Session ID for tracking runs (auto-generated if not set)"
    )

    # Optional: Telegram
    telegram_enabled = validator.optional("TELEGRAM_ENABLED", "0")
    telegram_token = validator.optional("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = validator.optional("TELEGRAM_CHAT_ID", "")

    # Validate Telegram configuration if enabled
    if telegram_enabled in ("1", "true", "True", "yes"):
        if not telegram_token or telegram_token == "your_bot_token_here":
            validator.errors.append(
                "❌ TELEGRAM_BOT_TOKEN: Missing or placeholder\n"
                "   TELEGRAM_ENABLED=1 but bot token is not configured\n"
                "   Either set a valid token or disable Telegram (TELEGRAM_ENABLED=0)"
            )

        if not telegram_chat_id or telegram_chat_id == "your_chat_id_here":
            validator.errors.append(
                "❌ TELEGRAM_CHAT_ID: Missing or placeholder\n"
                "   TELEGRAM_ENABLED=1 but chat ID is not configured\n"
                "   Either set a valid chat ID or disable Telegram (TELEGRAM_ENABLED=0)"
            )

    # Validate all and exit if strict
    validator.validate_all(strict=strict)

    return {
        "API_KEY": api_key,
        "API_SECRET": api_secret,
        "BOT_LOG_LEVEL": log_level,
        "BOT_SESSION_ID": session_id,
        "TELEGRAM_ENABLED": telegram_enabled,
        "TELEGRAM_BOT_TOKEN": telegram_token,
        "TELEGRAM_CHAT_ID": telegram_chat_id,
    }


if __name__ == "__main__":
    """Run validation standalone"""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s"
    )

    try:
        env = validate_environment(strict=True)
        print("\n✅ Environment validation passed!")
        print("\nValidated variables:")
        for key, value in env.items():
            if value and key not in ("API_KEY", "API_SECRET", "TELEGRAM_BOT_TOKEN"):
                print(f"  {key}: {value}")
            elif value:
                print(f"  {key}: {'*' * 10} (hidden)")
            else:
                print(f"  {key}: (not set)")
    except SystemExit as e:
        sys.exit(e.code)
