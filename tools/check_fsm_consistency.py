#!/usr/bin/env python3
"""
FSM Consistency Check Script

Validates:
1. All FSMEvent references in code exist in the enum
2. No CANCELLED spelling (should be CANCELED)
3. All transition events are properly defined

Usage:
    python tools/check_fsm_consistency.py
"""

import re
import glob
import os
import sys

# Get project root (parent of tools/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def check_enum_consistency():
    """Check that all FSMEvent references exist in the enum."""
    print("=" * 80)
    print("1. Checking FSMEvent enum consistency...")
    print("=" * 80)

    enum_path = os.path.join(ROOT, "core/fsm/fsm_events.py")

    if not os.path.exists(enum_path):
        print(f"❌ ERROR: {enum_path} not found!")
        return False

    with open(enum_path, "r", encoding="utf-8", errors="ignore") as f:
        enum_text = f.read()

    # Extract all enum values
    enum_values = set(re.findall(r'(\w+)\s*=\s*auto\(\)', enum_text))

    if not enum_values:
        print(f"❌ ERROR: No enum values found in {enum_path}!")
        return False

    print(f"✅ Found {len(enum_values)} enum values in FSMEvent")
    print(f"   {', '.join(sorted(enum_values)[:5])}...")

    # Check all FSM-related files
    files = (
        glob.glob(os.path.join(ROOT, "core/fsm/*.py")) +
        glob.glob(os.path.join(ROOT, "engine/*fsm*.py"))
    )

    missing = []
    for path in files:
        if not os.path.exists(path):
            continue

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        # Find all FSMEvent.XXX references
        used = set(re.findall(r'FSMEvent\.(\w+)', text))

        # Check for undefined events
        undefined = [u for u in used if u not in enum_values]
        if undefined:
            rel_path = os.path.relpath(path, ROOT)
            missing.append((rel_path, sorted(set(undefined))))
            print(f"❌ UNDEFINED in {rel_path}:")
            for event in sorted(set(undefined)):
                print(f"   - FSMEvent.{event}")

    if missing:
        print(f"\n❌ FAIL: Found {len(missing)} files with undefined events")
        return False
    else:
        print(f"\n✅ PASS: All FSMEvent references are defined")
        return True


def check_spelling():
    """Check for British spelling CANCELLED (should be CANCELED)."""
    print("\n" + "=" * 80)
    print("2. Checking spelling (CANCELLED → CANCELED)...")
    print("=" * 80)

    files = (
        glob.glob(os.path.join(ROOT, "core/**/*.py"), recursive=True) +
        glob.glob(os.path.join(ROOT, "engine/**/*.py"), recursive=True) +
        glob.glob(os.path.join(ROOT, "services/**/*.py"), recursive=True)
    )

    bad_spelling = []
    for path in files:
        if not os.path.exists(path):
            continue

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

        if "CANCELLED" in text:
            rel_path = os.path.relpath(path, ROOT)
            bad_spelling.append(rel_path)
            # Find line numbers
            lines = text.split('\n')
            for i, line in enumerate(lines, 1):
                if "CANCELLED" in line:
                    print(f"❌ {rel_path}:{i}")
                    print(f"   {line.strip()}")

    if bad_spelling:
        print(f"\n❌ FAIL: Found {len(bad_spelling)} files with CANCELLED spelling")
        return False
    else:
        print(f"\n✅ PASS: No CANCELLED spelling found (all use CANCELED)")
        return True


def check_transition_coverage():
    """Check that critical transitions are defined."""
    print("\n" + "=" * 80)
    print("3. Checking transition table coverage...")
    print("=" * 80)

    transitions_path = os.path.join(ROOT, "core/fsm/transitions.py")

    if not os.path.exists(transitions_path):
        print(f"❌ ERROR: {transitions_path} not found!")
        return False

    with open(transitions_path, "r", encoding="utf-8", errors="ignore") as f:
        transitions_text = f.read()

    # Critical transitions that must exist
    required = [
        ("WAIT_FILL", "BUY_ORDER_FILLED", "POSITION"),
        ("WAIT_FILL", "ORDER_CANCELED", "IDLE"),
        ("WAIT_FILL", "BUY_ABORTED", "IDLE"),
        ("PLACE_BUY", "BUY_ABORTED", "IDLE"),
        ("ENTRY_EVAL", "RISK_LIMITS_BLOCKED", "IDLE"),
    ]

    missing_transitions = []
    for from_phase, event, to_phase in required:
        # Look for pattern: Phase.FROM, FSMEvent.EVENT, Phase.TO
        pattern = rf'Phase\.{from_phase}.*FSMEvent\.{event}.*Phase\.{to_phase}'
        if not re.search(pattern, transitions_text):
            missing_transitions.append((from_phase, event, to_phase))
            print(f"❌ MISSING: {from_phase} --[{event}]--> {to_phase}")

    if missing_transitions:
        print(f"\n❌ FAIL: {len(missing_transitions)} critical transitions missing")
        return False
    else:
        print(f"\n✅ PASS: All critical transitions defined")
        return True


def check_ui_events():
    """Check that UI events are emitted in fsm_engine.py."""
    print("\n" + "=" * 80)
    print("4. Checking UI event emissions...")
    print("=" * 80)

    engine_path = os.path.join(ROOT, "engine/fsm_engine.py")

    if not os.path.exists(engine_path):
        print(f"❌ ERROR: {engine_path} not found!")
        return False

    with open(engine_path, "r", encoding="utf-8", errors="ignore") as f:
        engine_text = f.read()

    # UI events that should be emitted
    required_ui_events = [
        "ORDER_SUBMITTED",
        "ORDER_FILLED",
        "ORDER_CANCELED",
        "POSITION_OPENED",
        "POSITION_CLOSED",
    ]

    missing_ui = []
    for event in required_ui_events:
        # Look for emit("EVENT_NAME"
        pattern = rf'emit\(["\']' + event + r'["\']'
        if not re.search(pattern, engine_text):
            missing_ui.append(event)
            print(f"❌ MISSING: emit('{event}', ...)")

    if missing_ui:
        print(f"\n⚠️  WARNING: {len(missing_ui)} UI events not explicitly emitted")
        print("   (May be handled elsewhere, but recommended to emit explicitly)")
        return True  # Warning, not failure
    else:
        print(f"\n✅ PASS: All UI events explicitly emitted")
        return True


def main():
    """Run all consistency checks."""
    print("\n" + "=" * 80)
    print("FSM CONSISTENCY CHECK")
    print("=" * 80)
    print(f"Root: {ROOT}\n")

    results = {
        "Enum Consistency": check_enum_consistency(),
        "Spelling": check_spelling(),
        "Transition Coverage": check_transition_coverage(),
        "UI Events": check_ui_events(),
    }

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    for check_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {check_name}")

    all_passed = all(results.values())

    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ALL CHECKS PASSED - FSM is consistent!")
        print("=" * 80)
        return 0
    else:
        print("❌ SOME CHECKS FAILED - Please fix issues above")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
