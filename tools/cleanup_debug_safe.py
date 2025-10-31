#!/usr/bin/env python3
"""
Safe ULTRA DEBUG cleanup
Only removes file write blocks, keeps useful logging
"""

def main():
    filepath = 'services/market_data.py'

    with open(filepath, 'r') as f:
        lines = f.readlines()

    print(f"Total lines: {len(lines)}")
    print("Analyzing ULTRA DEBUG blocks...")

    # Track blocks to remove
    blocks_to_remove = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Find ULTRA DEBUG comment
        if '# ULTRA DEBUG' in line:
            # Check if followed by try: with open(/tmp/...
            block_start = i
            j = i + 1

            # Look ahead for file write pattern
            is_file_write_block = False
            block_end = i

            while j < len(lines) and j < i + 20:  # Look ahead max 20 lines
                if 'with open("/tmp/' in lines[j] or "with open('/tmp/" in lines[j]:
                    is_file_write_block = True
                    # Find end of try-except block
                    indent_level = len(lines[j]) - len(lines[j].lstrip())

                    # Find matching except:
                    for k in range(j, min(j + 15, len(lines))):
                        if 'except:' in lines[k] and 'pass' in lines[k+1] if k+1 < len(lines) else False:
                            block_end = k + 1
                            break
                        elif lines[k].strip() == 'except:':
                            for m in range(k+1, min(k+3, len(lines))):
                                if 'pass' in lines[m]:
                                    block_end = m
                                    break
                            break
                    break
                j += 1

            if is_file_write_block and block_end > block_start:
                blocks_to_remove.append((block_start, block_end))
                print(f"Found file write block: lines {block_start+1}-{block_end+1}")
                i = block_end + 1
            else:
                i += 1
        else:
            i += 1

    print(f"\nFound {len(blocks_to_remove)} file write blocks to remove")

    if not blocks_to_remove:
        print("No blocks to remove!")
        return

    # Remove blocks (from end to preserve line numbers)
    new_lines = lines.copy()
    for start, end in reversed(blocks_to_remove):
        print(f"Removing lines {start+1}-{end+1}")
        del new_lines[start:end+1]

    # Write cleaned version
    backup = filepath + '.before_debug_cleanup'
    with open(backup, 'w') as f:
        f.writelines(lines)
    print(f"\n✅ Backup created: {backup}")

    with open(filepath, 'w') as f:
        f.writelines(new_lines)

    print(f"✅ Cleaned file written")
    print(f"\n📊 Stats:")
    print(f"   Original: {len(lines)} lines")
    print(f"   Cleaned: {len(new_lines)} lines")
    print(f"   Removed: {len(lines) - len(new_lines)} lines")

if __name__ == "__main__":
    main()
