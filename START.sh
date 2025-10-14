#!/bin/bash
# Trading Bot Startup Script
# Aktiviert Virtual Environment und startet den Bot

cd "$(dirname "$0")"

# Aktiviere Virtual Environment
source venv/bin/activate

# Starte den Bot
python main.py
