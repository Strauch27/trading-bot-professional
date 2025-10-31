#!/usr/bin/env python3
"""
Remove ULTRA DEBUG code blocks from market_data.py
Removes debug file writes and bare except clauses
"""

import re

def remove_ultra_debug_blocks(content: str) -> tuple[str, int]:
    """Remove ULTRA DEBUG blocks"""
    removed_count = 0

    # Pattern 1: ULTRA DEBUG comment + try/except block writing to file
    pattern1 = r'\s*# ULTRA DEBUG[^\n]*\n\s*try:\s*\n\s*with open\([^)]+\)[^:]*:\s*\n(?:\s+[^\n]+\n)+\s*except:\s*\n\s*pass\s*\n'

    matches = list(re.finditer(pattern1, content, re.MULTILINE))
    removed_count += len(matches)

    for match in reversed(matches):  # Remove from end to preserve positions
        content = content[:match.start()] + content[match.end():]

    return content, removed_count


def main():
    filepath = 'services/market_data.py'

    print(f"Reading {filepath}...")
    with open(filepath, 'r') as f:
        original_content = f.read()

    original_lines = len(original_content.splitlines())

    print(f"Removing ULTRA DEBUG blocks...")
    cleaned_content, blocks_removed = remove_ultra_debug_blocks(original_content)

    cleaned_lines = len(cleaned_content.splitlines())
    lines_removed = original_lines - cleaned_lines

    print(f"✅ Removed {blocks_removed} ULTRA DEBUG blocks")
    print(f"✅ Removed {lines_removed} lines of code")

    # Write cleaned version
    backup_file = filepath + '.backup'
    with open(backup_file, 'w') as f:
        f.write(original_content)
    print(f"✅ Backup saved: {backup_file}")

    with open(filepath, 'w') as f:
        f.write(cleaned_content)
    print(f"✅ Cleaned file written: {filepath}")

    print(f"\n📊 Stats:")
    print(f"   Original: {original_lines:,} lines")
    print(f"   Cleaned:  {cleaned_lines:,} lines")
    print(f"   Removed:  {lines_removed:,} lines ({lines_removed/original_lines*100:.1f}%)")


if __name__ == "__main__":
    main()
