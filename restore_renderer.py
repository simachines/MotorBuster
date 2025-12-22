
import sys

# New render_timeline logic
# Hardcoded colors to avoid theme crashes
# "v6" Style: Black text on clips
NEW_RENDER = """
    def render_timeline(self):
        # Safety Check
        if not dpg.does_item_exist("timeline_canvas"):
            return

        # Clear
        dpg.delete_item("timeline_canvas", children_only=True, slot=2)
        
        # Dimensions
        ruler_height = 25
        track_height = 80
        total_w = 3000
        total_h = ruler_height + len(self.sequencer.tracks) * track_height
        
        # Log once
        if not hasattr(self, '_debug_render_once'):
            print(f"RENDER: Tracks={len(self.sequencer.tracks)}, Zoom={self.sequencer.zoom_x}")
            self._debug_render_once = True

        # Colors (Hardcoded fallback)
        c_bg = (30, 30, 30)
        c_grid = (50, 50, 50)
        c_track_even = (40, 40, 40)
        c_track_odd = (35, 35, 35)
        c_text = (200, 200, 200)
        c_clip_text = (0, 0, 0) # V6 FEATURE: Black Text
        
        # 1. Grid
        max_time = total_w / max(1.0, self.sequencer.zoom_x)
        curr = 0.0
        while curr < max_time:
            gx = curr * self.sequencer.zoom_x
            dpg.draw_line([gx, 0], [gx, total_h], color=c_grid, thickness=1, parent="timeline_canvas")
            curr += 1.0

        # 2. Tracks
        y = ruler_height
        for i, track in enumerate(self.sequencer.tracks):
             bg = c_track_even if i % 2 == 0 else c_track_odd
             # Track BG
             dpg.draw_rectangle([0, y], [total_w, y + track_height], color=bg, fill=bg, parent="timeline_canvas")
             # Border
             dpg.draw_line([0, y + track_height], [total_w, y + track_height], color=(60,60,60), parent="timeline_canvas")
             # Label
             dpg.draw_text([10, y + 5], track.name, size=15, color=c_text, parent="timeline_canvas")
             
             # Clips
             for clip in track.clips:
                 x_start = clip.start_time * self.sequencer.zoom_x
                 w = clip.duration * self.sequencer.zoom_x
                 
                 # Clip Color
                 if clip.type == "Sine": col = (100, 150, 255)
                 elif clip.type == "Constant": col = (100, 255, 100)
                 elif clip.type == "Ramp": col = (255, 255, 100)
                 elif clip.type == "Sawtooth": col = (255, 150, 50)
                 else: col = (150, 150, 150)
                 
                 # Draw Clip Rect
                 dpg.draw_rectangle([x_start, y+20], [x_start + w, y + track_height - 5], color=col, fill=(col[0], col[1], col[2], 150), thickness=2, parent="timeline_canvas")
                 
                 # Clip Name (Black + Shadow?)
                 # Shadow
                 dpg.draw_text([x_start + 6, y + 26], clip.name, size=13, color=(0,0,0,100), parent="timeline_canvas")
                 # Main Black
                 dpg.draw_text([x_start + 5, y + 25], clip.name, size=13, color=c_clip_text, parent="timeline_canvas")
                 
             y += track_height

        # 3. Ruler
        dpg.draw_rectangle([0, 0], [total_w, ruler_height], fill=(20, 20, 20), color=(60, 60, 60), parent="timeline_canvas")
        
        # 4. Playhead
        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y], color=(255, 50, 50), thickness=2, parent="timeline_canvas")
"""

with open("native_app.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace the existing render_timeline method
import re
# Find start
start_marker = "    def render_timeline(self):"
end_marker = "def _draw_resize_cursor" # Assuming this follows, or we use regex to match indentation check

lines = content.splitlines()
start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if start_marker.strip() in line:
        start_idx = i
        break

if start_idx != -1:
    # Scan for next method
    for i in range(start_idx + 1, len(lines)):
        if lines[i].strip().startswith("def "):
            end_idx = i
            break
            
if start_idx != -1 and end_idx != -1:
    print(f"Replacing render_timeline from {start_idx} to {end_idx}")
    new_lines = lines[:start_idx] + NEW_RENDER.splitlines() + lines[end_idx:]
    
    with open("native_app.py", "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))
    print("Successfully replaced render_timeline")
else:
    print("Could not locate render_timeline block.")

