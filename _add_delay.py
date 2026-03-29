import sys

# Read file
with open(r'c:\Users\ernes\.gemini\antigravity\scratch\fedit_2\native_app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and modify line 2944-2945
for i in range(len(lines)):
    if i == 2943:  # Line 2944 (0-indexed)
        # Insert delay lines after SendMessageW
        lines[i] = lines[i]  # Keep SendMessageW line
        # Insert new lines after it
        lines.insert(i+1, "                              # Brief delay to allow mouse state to update\r\n")
        lines.insert(i+2, "                              import time\r\n")
        lines.insert(i+3, "                              time.sleep(0.05)  # 50ms delay\r\n")
        break

# Write back
with open(r'c:\Users\ernes\.gemini\antigravity\scratch\fedit_2\native_app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Successfully added delay after drag")
