
import os

def run():
    # Try reading as utf-16le (default for PowerShell redirect on Windows)
    content = ""
    try:
        with open("temp_old.py", "r", encoding="utf-16-le") as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read utf-16-le: {e}")
        try:
            with open("temp_source.py", "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e2:
            print(f"Failed to read utf-8: {e2}")
            return

    # Write debug length
    print(f"Read {len(content)} characters.")
    
    # Analyze
    lines = content.splitlines()
    targets = ["def update_loop"]
    
    with open("temp_old.py", "r", encoding="utf-16-le") as f:
        content = f.read()
    lines = content.splitlines()

    print("Searching update_loop in temp_old.py...")
    for i, line in enumerate(lines):
        for t in targets:
             if t in line:
                 print(f"Found {t} at {i}: {line.strip()}")
                 for j in range(i, i+50):
                     if j < len(lines):
                         print(lines[j])
                 print("\n--- END BLOCK ---\n")

if __name__ == "__main__":
    run()
