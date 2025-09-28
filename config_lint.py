from __future__ import annotations
import os
import math
import config

class ConfigError(ValueError):
    pass

def _is_percent(x: float) -> bool:
    return 0.0 <= x <= 1.0

def _is_bps_list(xs) -> bool:
    try:
        return all(isinstance(v, (int, float)) and -5000 <= v <= 5000 for v in xs) and len(xs) > 0
    except Exception:
        return False

def validate_config() -> None:
    errs = []

    # --- Policy-Konflikte ---
    if getattr(config, "NEVER_MARKET_SELLS", False) and getattr(config, "ALLOW_MARKET_FALLBACK_TTL", False):
        errs.append("Policy conflict: NEVER_MARKET_SELLS=True und ALLOW_MARKET_FALLBACK_TTL=True. "
                    "Wähle entweder 'nie Market' ODER 'TTL darf Market-Fallback'.")

    # --- BUY Entry: Erzwinge genau einen Pfad (XOR) ---
    use_escalation = bool(getattr(config, "BUY_ESCALATION_STEPS", []))
    use_offset     = bool(getattr(config, "ENTRY_LIMIT_OFFSET_BPS", 0))
    if use_escalation and use_offset:
        errs.append("BUY-Konflikt: BUY_ESCALATION_STEPS und ENTRY_LIMIT_OFFSET_BPS gleichzeitig aktiv. "
                    "Bitte genau einen Entry-Mechanismus verwenden (oder ENTRY_LIMIT_OFFSET_BPS=0 setzen).")

    # --- Wertebereiche ---
    fee = float(getattr(config, "FEE_RATE", 0.001))
    if not _is_percent(fee):
        errs.append(f"FEE_RATE außerhalb [0,1]: {fee}")

    ttl = int(getattr(config, "TRADE_TTL_MIN", 0))
    if ttl < 0 or ttl > 24*60:
        errs.append(f"TRADE_TTL_MIN unplausibel (0..1440 erwartet): {ttl}")

    # Check if BUY_ESCALATION_STEPS is a list (as it should be)
    buy_steps = getattr(config, "BUY_ESCALATION_STEPS", [])
    if buy_steps and (not isinstance(buy_steps, list) or len(buy_steps) < 1 or len(buy_steps) > 10):
        errs.append(f"BUY_ESCALATION_STEPS muss Liste mit 1-10 Einträgen sein: {buy_steps}")

    pbps = int(getattr(config, "PREDICTIVE_BUY_ZONE_BPS", 0))
    if abs(pbps) > 5000:
        errs.append(f"PREDICTIVE_BUY_ZONE_BPS zu groß (|bps|<=5000): {pbps}")

    exit_bps = getattr(config, "EXIT_LADDER_BPS", None) or getattr(config, "EXIT_ESCALATION_BPS", None)
    if not _is_bps_list(exit_bps or [0]):
        errs.append(f"EXIT_LADDER_BPS/EXIT_ESCALATION_BPS müssen eine nicht-leere Liste in +-5000 bps sein: {exit_bps}")

    max_slip = int(getattr(config, "MAX_SLIPPAGE_BPS_EXIT", 200))
    if max_slip < 0 or max_slip > 10000:
        errs.append(f"MAX_SLIPPAGE_BPS_EXIT unplausibel (0..10000): {max_slip}")

    # --- Pfade/Dirs ---
    # Verzeichnis-Existenz wird erst zur Laufzeit sichergestellt (Engine _ensure_runtime_dirs)
    # => hier NICHT failen, nur warnen:
    import warnings

    state_dir = getattr(config, "STATE_DIR", None)
    if state_dir and not os.path.isdir(state_dir):
        warnings.warn(f"[config] Directory not found yet (will be created at runtime): {state_dir}")

    session_dir = getattr(config, "SESSION_DIR", None)
    if session_dir and not os.path.isdir(session_dir):
        warnings.warn(f"[config] Directory not found yet (will be created at runtime): {session_dir}")

    log_dir = getattr(config, "LOG_DIR", None)
    if log_dir and not os.path.isdir(log_dir):
        warnings.warn(f"[config] Directory not found yet (will be created at runtime): {log_dir}")

    reports_dir = getattr(config, "REPORTS_DIR", None)
    if reports_dir and not os.path.isdir(reports_dir):
        warnings.warn(f"[config] Directory not found yet (will be created at runtime): {reports_dir}")

    snapshots_dir = getattr(config, "SNAPSHOTS_DIR", None)
    if snapshots_dir and not os.path.isdir(snapshots_dir):
        warnings.warn(f"[config] Directory not found yet (will be created at runtime): {snapshots_dir}")

    # --- Ergebnis ---
    if errs:
        msg = "CONFIG VALIDATION FAILED:\n- " + "\n- ".join(errs)
        raise ConfigError(msg)