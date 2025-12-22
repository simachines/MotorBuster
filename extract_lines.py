
def extract():
    try:
        with open("temp_old.py", "r", encoding="utf-16-le") as f:
            lines = f.readlines()
    except:
         with open("temp_old.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    # Helpers
    print("--- SEARCH START ---")
    targets = ["def make_drag_source", "def canvas_click", "def on_drop_receive", "def create_status_themes"]
    import re
    
    targets = ["def render_timeline"]
    
    # Read temp_old.py (v12 baseline)
    try:
        with open("temp_old.py", "r", encoding="utf-16-le") as f:
            lines = f.readlines()
    except UnicodeError:
        # Fallback if it was saved as utf-8 at some point
        with open("temp_old.py", "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    print("--- SEARCH START ---")
    with open("v6_render.txt", "w", encoding="utf-8") as out:
        for i, line in enumerate(lines):
            for t in targets:
                 if t in line:
                     out.write(f"Found {t} at {i}: {line.strip()}\n")
                     # render_timeline is usually long ~150 lines
                     for j in range(i, i+200):
                         if j < len(lines):
                             # Stop if we hit the next major method
                             if "def " in lines[j] and j > i:
                                 # minor heuristic to stop at next method
                                 pass 
                             out.write(lines[j])
                     out.write("\n--- END BLOCK ---\n")
    print("Done writing to v6_render.txt")

if __name__ == "__main__":
    extract()
