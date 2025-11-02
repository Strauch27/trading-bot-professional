#!/usr/bin/env python3
"""
Quick test to verify FSM fixes are in place.
"""
import sys
sys.path.insert(0, '/Users/stenrauch/Downloads/Trading Bot Professional Git/trading-bot-professional')

from core.fsm.transitions import get_transition_table
from core.fsm.fsm_events import FSMEvent
from core.fsm.phases import Phase

# Test 1: Check if ENTRY_EVAL + NO_SIGNAL transition exists
print("="*60)
print("Test 1: Checking ENTRY_EVAL + NO_SIGNAL transition")
print("="*60)

tt = get_transition_table()
transition = tt.get_transition(Phase.ENTRY_EVAL, FSMEvent.NO_SIGNAL)

if transition:
    next_phase, action = transition
    print(f"✓ Transition EXISTS")
    print(f"  From: {Phase.ENTRY_EVAL.name}")
    print(f"  Event: {FSMEvent.NO_SIGNAL.name}")
    print(f"  To: {next_phase.name}")
    print(f"  Action: {action.__name__ if action else 'None'}")
else:
    print(f"✗ Transition NOT FOUND!")
    print(f"  Phase: {Phase.ENTRY_EVAL.name}")
    print(f"  Event: {FSMEvent.NO_SIGNAL.name}")

print()
print("Total transitions in table:", tt.get_transition_count())
print()

# Test 2: Show valid events from ENTRY_EVAL
print("="*60)
print("Test 2: Valid events from ENTRY_EVAL phase")
print("="*60)
valid_events = tt.get_valid_events(Phase.ENTRY_EVAL)
for event in valid_events:
    trans = tt.get_transition(Phase.ENTRY_EVAL, event)
    if trans:
        next_phase, action = trans
        print(f"  {event.name:30} → {next_phase.name}")

print()
print("="*60)
print("DONE")
print("="*60)
