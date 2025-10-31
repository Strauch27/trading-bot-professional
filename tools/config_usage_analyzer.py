#!/usr/bin/env python3
"""
Config Usage Analyzer
Analyzes config.py to find unused, inconsistent, or missing implementations
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple


def extract_config_variables(config_path: str) -> Dict[str, str]:
    """Extract all config variables from config.py"""
    with open(config_path, 'r') as f:
        content = f.read()

    # Find all uppercase variable assignments
    pattern = r'^([A-Z_]+)\s*=\s*(.+?)(?:\s*#.*)?$'
    matches = re.findall(pattern, content, re.MULTILINE)

    variables = {}
    for var_name, value in matches:
        # Clean up value
        value = value.strip()
        variables[var_name] = value

    return variables


def find_variable_usage(var_name: str, search_dirs: List[str]) -> int:
    """Count usages of a variable in Python files"""
    total_count = 0

    for search_dir in search_dirs:
        try:
            # Use grep to search for variable
            cmd = [
                'grep', '-r',
                f'\\b{var_name}\\b',
                '--include=*.py',
                '--exclude-dir=venv',
                '--exclude-dir=sessions',
                '--exclude-dir=.git',
                '--exclude-dir=__pycache__',
                search_dir
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            lines = result.stdout.strip().split('\n') if result.stdout.strip() else []

            # Filter out config.py itself
            non_config_lines = [
                line for line in lines
                if line and not line.startswith('config.py:')
            ]

            total_count += len(non_config_lines)

        except Exception as e:
            print(f"Warning: Error searching for {var_name}: {e}")

    return total_count


def categorize_config_variables(variables: Dict[str, str]) -> Dict[str, List[str]]:
    """Categorize config variables by domain"""
    categories = {
        'runtime': [],
        'trading': [],
        'exit': [],
        'position': [],
        'risk': [],
        'guards': [],
        'market_data': [],
        'order': [],
        'logging': [],
        'performance': [],
        'features': [],
        'paths': [],
        'deprecated': [],
        'other': []
    }

    for var_name in variables.keys():
        if any(x in var_name for x in ['SESSION', 'LOG', 'DIR', 'FILE', 'PATH']):
            categories['paths'].append(var_name)
        elif any(x in var_name for x in ['RUN_ID', 'TIMESTAMP', 'CONFIG_VERSION']):
            categories['runtime'].append(var_name)
        elif any(x in var_name for x in ['TAKE_PROFIT', 'STOP_LOSS', 'EXIT', 'SL', 'TP']):
            categories['exit'].append(var_name)
        elif any(x in var_name for x in ['POSITION', 'MAX_TRADES', 'TTL', 'COOLDOWN']):
            categories['position'].append(var_name)
        elif any(x in var_name for x in ['MAX_', 'MIN_', 'LIMIT', 'BUDGET', 'RISK']):
            categories['risk'].append(var_name)
        elif any(x in var_name for x in ['GUARD', 'FILTER', 'USE_SMA', 'USE_VOLUME', 'USE_BTC']):
            categories['guards'].append(var_name)
        elif any(x in var_name for x in ['MD_', 'MARKET', 'POLL', 'TICKER', 'CACHE']):
            categories['market_data'].append(var_name)
        elif any(x in var_name for x in ['ORDER', 'ROUTER', 'IOC', 'GTC', 'TIF']):
            categories['order'].append(var_name)
        elif any(x in var_name for x in ['LOG', 'DEBUG', 'TRACE', 'CONSOLE']):
            categories['logging'].append(var_name)
        elif any(x in var_name for x in ['FEATURE_', 'ENABLE_', 'USE_']):
            categories['features'].append(var_name)
        elif 'DEPRECATED' in variables[var_name] or var_name == 'MODE' or var_name == 'POLL_MS':
            categories['deprecated'].append(var_name)
        else:
            categories['other'].append(var_name)

    return categories


def main():
    """Main analysis function"""
    print("=" * 80)
    print("CONFIG USAGE ANALYZER")
    print("=" * 80)
    print()

    # Get project root
    script_dir = Path(__file__).parent.parent
    os.chdir(script_dir)

    config_path = 'config.py'
    if not os.path.exists(config_path):
        print(f"ERROR: {config_path} not found")
        return

    print(f"Analyzing {config_path}...")
    print()

    # Extract variables
    variables = extract_config_variables(config_path)
    print(f"Total config variables: {len(variables)}")
    print()

    # Categorize
    categories = categorize_config_variables(variables)

    print("Variables by Category:")
    for category, vars_list in categories.items():
        if vars_list:
            print(f"  {category}: {len(vars_list)}")
    print()

    # Check usage for critical variables
    print("Checking usage for critical variables...")
    print()

    critical_vars = [
        # Trading
        'GLOBAL_TRADING',
        'TAKE_PROFIT_THRESHOLD',
        'STOP_LOSS_THRESHOLD',
        'SWITCH_TO_SL_THRESHOLD',
        'SWITCH_TO_TP_THRESHOLD',
        'SWITCH_COOLDOWN_S',

        # Entry
        'DROP_TRIGGER_VALUE',
        'DROP_TRIGGER_MODE',
        'USE_DROP_ANCHOR',

        # ATR
        'USE_ATR_BASED_EXITS',
        'ATR_PERIOD',
        'ATR_SL_MULTIPLIER',
        'ATR_TP_MULTIPLIER',

        # Position
        'MAX_CONCURRENT_POSITIONS',
        'POSITION_SIZE_USDT',
        'TRADE_TTL_MIN',

        # Market Data
        'MD_ENABLE_PRIORITY_UPDATES',
        'MD_PORTFOLIO_TTL_MS',
        'MD_BATCH_POLLING',
        'MD_AUTO_RESTART_ON_CRASH',

        # Order Router
        'ROUTER_MAX_RETRIES',
        'ENABLE_COID_MANAGER',

        # Features
        'ORDER_FLOW_ENABLED',
        'USE_RECONCILER',
        'EXIT_TRAILING_ENABLE',

        # Deprecated
        'MODE',
        'POLL_MS',
        'MAX_TRADES',
    ]

    search_dirs = ['.']
    unused = []
    deprecated_still_used = []

    for var in critical_vars:
        if var in variables:
            count = find_variable_usage(var, search_dirs)

            status = "✅" if count > 0 else "❌"
            print(f"{status} {var}: {count} usages")

            if count == 0:
                unused.append(var)

            if var in categories['deprecated'] and count > 0:
                deprecated_still_used.append((var, count))

    print()
    print("=" * 80)
    print("FINDINGS")
    print("=" * 80)
    print()

    if unused:
        print(f"⚠️  UNUSED VARIABLES ({len(unused)}):")
        for var in unused:
            print(f"  - {var}")
        print()

    if deprecated_still_used:
        print(f"⚠️  DEPRECATED BUT STILL USED ({len(deprecated_still_used)}):")
        for var, count in deprecated_still_used:
            print(f"  - {var}: {count} usages")
        print()

    print("✅ Analysis complete")


if __name__ == "__main__":
    main()
