import dearpygui.dearpygui as dpg
import sys
import os
import time
from dataclasses import dataclass, field
import uuid
import json
import math
import ctypes

# Fix for finding modules in .dependencies when running locally or frozen
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    os.environ["PYSDL2_DLL_PATH"] = base_path
else:
    base_path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.join(base_path, '.dependencies'))

from server.ffb_engine import engine, DeviceInfo

# --- Data Model ---
@dataclass
class Clip:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "Sine" # Sine, Square, Ramp, etc.
    name: str = "" # User defined name
    start_time: float = 0.0 # Seconds
    duration: float = 2.0 # Seconds
    track_index: int = 0
    # Parameters
    magnitude: int = 10000
    frequency: int = 10
    frequency_end: int = 10 # For Sweep
    active_effect_id: int = -1 # Runtime ID
    
    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "name": self.name, "start_time": self.start_time,
            "duration": self.duration, "track_index": self.track_index,
            "magnitude": self.magnitude, "frequency": self.frequency,
            "frequency_end": self.frequency_end
        }
    
    @staticmethod
    def from_dict(d):
        freq = d.get("frequency", 10)
        c = Clip(
            id=d.get("id", str(uuid.uuid4())), type=d.get("type", "Sine"),
            name=d.get("name", ""),
            start_time=d.get("start_time", 0.0), duration=d.get("duration", 1.0),
            track_index=d.get("track_index", 0),
            magnitude=d.get("magnitude", 10000), frequency=freq,
            frequency_end=d.get("frequency_end", freq) # Default to start freq if missing
        )
        return c

@dataclass
class Track:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Track"
    gain: int = 100
    clips: list[Clip] = field(default_factory=list)
    
    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "gain": self.gain,
            "clips": [c.to_dict() for c in self.clips]
        }
    
    @staticmethod
    def from_dict(d):
        t = Track(id=d.get("id", str(uuid.uuid4())), name=d.get("name", "Track"), gain=d.get("gain", 100))
        t.clips = [Clip.from_dict(cd) for cd in d.get("clips", [])]
        return t

class FeditSequencer:
    def __init__(self):
        # Default tracks renamed to "1", "2"...
        self.tracks = [Track(name=f"{i+1}") for i in range(4)]
        self.is_playing = False
        self.current_time = 0.0
        self.last_tick = 0.0
        self.zoom_x = 50.0 # Pixels per second
        # Selection State
        self.selected_clip: Clip = None
        self.drag_clip: Clip = None
        self.drag_offset: float = 0.0
        # Resize State
        self.resize_clip: Clip = None
        self.resize_edge: str = None # "left" or "right"
        self.resize_initial_time: float = 0.0
        self.resize_initial_dur: float = 0.0
        
    def to_dict(self):
        return {"tracks": [t.to_dict() for t in self.tracks]}
        
    def load_from_dict(self, d):
        self.tracks = [Track.from_dict(td) for td in d.get("tracks", [])]
        self.selected_clip = None

    def add_clip(self, track_idx, type, start_time):
        clip = Clip(type=type, start_time=start_time, track_index=track_idx)
        self.tracks[track_idx].clips.append(clip)
        return clip

    def delete_clip(self, clip):
        for t in self.tracks:
            if clip in t.clips:
                t.clips.remove(clip)
                if self.selected_clip == clip:
                    self.selected_clip = None
                return

    def get_clip_at(self, track_idx, time_s):
        # Allow some tolerance for clicking
        for clip in self.tracks[track_idx].clips:
            if clip.start_time <= time_s <= clip.start_time + clip.duration:
                return clip
        return None

# --- Application ---
class FeditNativeApp:
    def __init__(self):
        self.sequencer = FeditSequencer()
        self.sequencer.is_scrubbing = False # New state for playhead dragging
        self.log_items = []
        self.renaming_track_idx = -1
        
        dpg.create_context()
        
        # Theme Init
        self.theme_colors = {}
        self.current_theme_mode = "Dark" 
        self.load_fonts()

        self.setup_ui()
        
        # Init Engine
        try:
            self.log("Initializing Haptic Subsystem...")
            engine.init_sdl()
        except Exception as e:
            self.log(f"SDL Init Error: {e}")

        # Statistics
        self.stats_peak = 0.0
        self.stats_min = 0.0
        self.stats_sum = 0.0
        self.stats_count = 0




    # --- Clip helpers ---
    def _snap_to_edges(self, track: Track, clip: Clip, candidate_time: float, snap_px: float = 8.0) -> float:
        """Snap a time position to the nearest edge of neighboring clips within a pixel threshold."""
        threshold_s = snap_px / max(1.0, self.sequencer.zoom_x)
        best_time = candidate_time
        best_delta = threshold_s

        for other in track.clips:
            if other is clip:
                continue
            for edge in (other.start_time, other.start_time + other.duration):
                delta = abs(edge - candidate_time)
                if delta < best_delta:
                    best_delta = delta
                    best_time = edge

        return best_time

    def _avoid_overlap_on_drag(self, track: Track, clip: Clip, candidate_start: float) -> float:
        """Shift the dragged clip so it no longer overlaps neighbors, preserving duration."""
        start = max(0.0, candidate_start)
        end = start + clip.duration

        # Iterate until stable to avoid overlaps
        for _ in range(10):
            adjusted = False
            for other in track.clips:
                if other is clip:
                    continue
                o_start = other.start_time
                o_end = other.start_time + other.duration
                overlaps = not (end <= o_start or start >= o_end)
                if overlaps:
                    # Decide direction based on where we came from
                    if start >= o_start:
                        start = o_end
                    else:
                        start = max(0.0, o_start - clip.duration)
                    end = start + clip.duration
                    adjusted = True
                    break
            if not adjusted:
                break

        return start

    def _limit_right_resize(self, track: Track, clip: Clip, desired_dur: float) -> float:
        """Clamp right-edge resize so it does not pass the next clip and snaps to near edges."""
        next_start = None
        for other in track.clips:
            if other is clip:
                continue
            if other.start_time >= clip.start_time:
                if next_start is None or other.start_time < next_start:
                    next_start = other.start_time

        desired_end = clip.start_time + desired_dur
        if next_start is not None:
            desired_end = min(desired_end, next_start)

        desired_end = self._snap_to_edges(track, clip, desired_end)
        if next_start is not None and desired_end > next_start:
            desired_end = next_start

        return max(0.1, desired_end - clip.start_time)

    def _limit_left_resize(self, track: Track, clip: Clip, desired_start: float, fixed_end: float) -> float:
        """Clamp left-edge resize so it does not pass the previous clip and snaps to near edges."""
        prev_end = None
        for other in track.clips:
            if other is clip:
                continue
            o_end = other.start_time + other.duration
            if o_end <= fixed_end:
                if prev_end is None or o_end > prev_end:
                    prev_end = o_end

        candidate = max(0.0, desired_start)
        candidate = self._snap_to_edges(track, clip, candidate)

        if prev_end is not None and candidate < prev_end:
            candidate = prev_end

        # Prevent inversion
        if candidate > fixed_end - 0.1:
            candidate = max(0.0, fixed_end - 0.1)

        return candidate

    # --- Waveform helpers ---
    def _wave_amplitude(self, clip: Clip, t: float) -> float:
        """Return signed amplitude at time t (seconds) for display purposes."""
        mag = clip.magnitude
        if clip.type == "Constant":
            return mag
        if clip.type == "Ramp":
            return mag * max(0.0, min(1.0, t / max(clip.duration, 1e-6)))
        if clip.type == "Sawtooth":
            period = 1.0 / max(1.0, clip.frequency)
            phase = (t / period) % 1.0
            return mag * (2 * phase - 1)  # -mag .. +mag
        # Default: Sine
        omega = 2 * math.pi * clip.frequency
        return mag * math.sin(omega * t)

    def _clip_wave_points(self, clip: Clip, x_start: float, width: float, y_top: float, y_bottom: float, samples: int = 80):
        """Compute polyline points representing the clip waveform within the clip bounds."""
        points = []
        y_mid = (y_top + y_bottom) / 2.0
        amp_span = (y_bottom - y_top) * 0.45  # leave padding
        mag_max = max(1.0, abs(clip.magnitude))

        # Increase sampling with frequency and width to avoid aliasing/flat lines
        samples = max(
            samples,
            int(width / 3),
            int(clip.frequency * clip.duration * 120)
        )
        samples = min(samples, 800)

        for i in range(samples + 1):
            frac = i / max(1, samples)
            t = frac * clip.duration
            amp = self._wave_amplitude(clip, t)
            norm = max(-1.0, min(1.0, amp / mag_max))
            x = x_start + frac * width
            y = y_mid - norm * amp_span
            points.append([x, y])
        return points

    def log(self, message):
        self.log_items.append(message)
        if len(self.log_items) > 50: self.log_items.pop(0)
        if dpg.does_item_exist("log_list"):
            dpg.configure_item("log_list", items=self.log_items)
            
    # --- Project Management ---
    def save_project_to_file(self, path):
        data = self.sequencer.to_dict()
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self.log(f"Project Saved: {os.path.basename(path)}")
        except Exception as e:
            self.log(f"Save Failed: {e}")

    def load_project_from_file(self, path):
        try:
            if not os.path.exists(path):
                self.log("File not found.")
                return
            with open(path, "r") as f:
                data = json.load(f)
            self.sequencer.load_from_dict(data)
            self.log(f"Loaded: {os.path.basename(path)}")
        except Exception as e:
            self.log(f"Load Failed: {e}")

    def action_save_file(self, sender, app_data):
        # app_data['file_path_name'] is the full path
        path = app_data.get('file_path_name')
        if path:
             # Ensure extension
             if not path.endswith(".fedit"): path += ".fedit"
             self.save_project_to_file(path)

    def action_load_file(self, sender, app_data):
        path = app_data.get('file_path_name')
        if path:
             self.load_project_from_file(path)

    def scan_devices(self):
        self.log("Scanning devices...")
        devs = engine.list_devices()
        items = [f"{d.name} (#{d.index})" for d in devs]
        dpg.configure_item("device_combo", items=items)
        if items: 
            dpg.set_value("device_combo", items[0])
            self.connect_device_by_name(items[0])
        else:
            dpg.set_value("device_combo", "No Devices Found")

    def _get_torque_for_device(self, name: str) -> float:
        """Returns known max torque (Nm) for common devices base name."""
        name_lower = name.lower()
        # Lookup Table
        db = {
            "simucube 2 ultimate": 32.0,
            "simucube 2 pro": 25.0,
            "simucube 2 sport": 17.0,
            "fanatec dd2": 25.0,
            "fanatec dd1": 20.0,
            "fanatec csl dd": 8.0,
            "fanatec gt dd pro": 8.0,
            "moza r21": 21.0,
            "moza r16": 16.0,
            "moza r12": 12.0,
            "moza r9": 9.0,
            "moza r5": 5.5,
            "logitech g pro": 11.0,
            "thrustmaster t818": 10.0,
            "vrs directforce pro": 20.0,
            "simagic alpha ultimate": 23.0,
            "simagic alpha": 15.0,
            "simagic alpha mini": 10.0,
            "asetek invicta": 27.0,
            "asetek forte": 18.0,
            "asetek la prima": 12.0,
            "conspit ares platinum": 20.0,
            "conspit ares apex": 8.0,
            "conspit ares": 20.0,
        }
        
        # Substring match - Find Longest Match
        best_val = 0.0
        max_len = 0
        
        for key, val in db.items():
            if key in name_lower:
                if len(key) > max_len:
                    max_len = len(key)
                    best_val = val
                    
        return best_val

    def connect_device_by_name(self, name):
         try:
             idx = int(name.split("#")[-1].replace(")", ""))
             if engine.connect_device(idx):
                 dpg.set_value("status_text", "Status: Connected")
                 # Theme Aware Color
                 col = (0, 255, 0) if self.current_theme_mode == "Dark" else (0, 150, 0)
                 dpg.configure_item("status_text", color=col)
                 
                 # Auto-Detect Torque
                 detected_torque = self._get_torque_for_device(name)
                 if detected_torque > 0.0:
                     if dpg.does_item_exist("input_max_torque"):
                         dpg.set_value("input_max_torque", detected_torque)
                         self.log(f"Auto-Detected Torque: {detected_torque} Nm")
                 
         except: pass

    def connect_callback(self):
        val = dpg.get_value("device_combo")
        if val and "No Devices" not in val:
            self.connect_device_by_name(val)
        else:
            self.scan_devices()

    # --- Torque Telemetry ---
    def calculate_current_force(self):
        """Calculates theoretical total force output at current time."""
        if not self.sequencer.is_playing: return 0.0, False
        
        total_force = 0.0
        cur_t = self.sequencer.current_time
        
        for t in self.sequencer.tracks:
            for clip in t.clips:
                 if clip.start_time <= cur_t < (clip.start_time + clip.duration):
                     # Calculate instantaneous amplitude
                     # Relative time in clip
                     rel_t = cur_t - clip.start_time
                     amp = self._wave_amplitude(clip, rel_t)
                     total_force += amp
        
        # Apply Global Gain (from UI if available, else 1.0)
        gain = 1.0
        if dpg.does_item_exist("slider_gain"):
             gain = dpg.get_value("slider_gain") / 100.0
        
        # Apply Safety Limit Logic
        limit_gain = 1.0
        if dpg.does_item_exist("input_max_torque") and dpg.does_item_exist("input_torque_limit"):
            dev_peak = dpg.get_value("input_max_torque")
            safe_limit = dpg.get_value("input_torque_limit")
            if dev_peak > 0 and safe_limit > 0:
                # If theoretical max (dev_peak) > safe_limit, we might need to clamp
                # total_force is in range +/- 32767 approx (if 1 clip full mag)
                # We essentially want to clamp the FINAL NORMALIZED PERCENTAGE?
                # Or clamp the precision value.
                pass
        
        normalized = (total_force * gain / 32767.0) * 100.0
        
        # Clamp Normalized based on Safety Limit vs Device Peak
        if dpg.does_item_exist("input_max_torque") and dpg.does_item_exist("input_torque_limit"):
             dev_peak = dpg.get_value("input_max_torque")
             safe_limit = dpg.get_value("input_torque_limit")
             if dev_peak > 0:
                 max_pct = (safe_limit / dev_peak) * 100.0
                 normalized = max(-max_pct, min(max_pct, normalized))

        # Check if we are "active" (i.e. inside any clip)
        is_active = False
        for t in self.sequencer.tracks:
            for clip in t.clips:
                 if clip.start_time <= cur_t < (clip.start_time + clip.duration):
                     is_active = True
                     break
            if is_active: break

        return max(-100.0, min(100.0, normalized)), is_active

    # --- Transport Logic ---
    def toggle_play(self):
        self.sequencer.is_playing = not self.sequencer.is_playing
        self.sequencer.last_tick = time.time()
        label = "Pause" if self.sequencer.is_playing else "Play"
        dpg.configure_item("btn_play", label=label)
        
        if self.sequencer.is_playing:
             # Reset Stats
             self.stats_peak = 0.0
             self.stats_min = 9999.0 # Start high
             self.stats_sum = 0.0
             self.stats_count = 0
             # Clear text
             if dpg.does_item_exist("txt_peak"): dpg.set_value("txt_peak", "Peak: --")
             if dpg.does_item_exist("txt_avg"): dpg.set_value("txt_avg", "Avg: --")
             if dpg.does_item_exist("txt_min"): dpg.set_value("txt_min", "Min: --")

        
        if not self.sequencer.is_playing:
            # Stop all
            engine.stop_effect()
            # Reset active IDs
            for t in self.sequencer.tracks:
                for c in t.clips: c.active_effect_id = -1

    def action_restart(self):
        self.sequencer.current_time = 0.0
        self.sequencer.last_tick = time.time()
        # Reset effect states
        engine.stop_effect()
        for t in self.sequencer.tracks:
            for c in t.clips: c.active_effect_id = -1
        self.log("Restarted")

    def get_canvas_relative_pos(self, global_mouse_pos):
        # Helper to get coords relative to timeline content
        try:
             # Rely on node_pos vs scroll.
             c_min = dpg.get_item_rect_min("timeline_canvas")
             return global_mouse_pos[0] - c_min[0], global_mouse_pos[1] - c_min[1]
        except:
             return 0,0

    def _get_resize_hover(self, track_idx, rel_x):
        """Check if mouse is hovering over a clip edge for resizing. Returns (clip, edge_type) or (None, None)."""
        if 0 <= track_idx < len(self.sequencer.tracks):
             track = self.sequencer.tracks[track_idx]
             threshold = 20.0 # Pixel threshold
             
             for clip in track.clips:
                 clip_px_start = clip.start_time * self.sequencer.zoom_x
                 clip_px_end = (clip.start_time + clip.duration) * self.sequencer.zoom_x
                 
                 if abs(rel_x - clip_px_end) < threshold:
                     return clip, "right"
                 if abs(rel_x - clip_px_start) < threshold:
                     return clip, "left"
        return None, None

    def update_loop(self):
        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        
        # Cursor Logic
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        track_h = 80
        if rel_y < 25: 
            track_idx = -1 # Ruler area
        else:
            track_idx = int((rel_y - 25) // track_h)
        
        resize_clip_hover, _ = self._get_resize_hover(track_idx, rel_x)
        
        if dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
             
             # 0. CURSOR ON RULER -> Scrub (Top Priority if in ruler)
             if 0 <= rel_y < 25 and dpg.is_item_hovered("timeline_canvas") and not self.sequencer.drag_clip and not self.sequencer.resize_clip:
                 self.sequencer.is_scrubbing = True

             # 1. RESIZING logic
             if self.sequencer.resize_clip:
                 clip = self.sequencer.resize_clip
                 track = self.sequencer.tracks[clip.track_index]
                 cur_mouse_t = rel_x / self.sequencer.zoom_x
                 
                 if self.sequencer.resize_edge == "right":
                     desired_dur = max(0.1, cur_mouse_t - clip.start_time)
                     clip.duration = self._limit_right_resize(track, clip, desired_dur)
                 elif self.sequencer.resize_edge == "left":
                     # Limit: cannot pull start past end
                     old_end = self.sequencer.resize_initial_time + self.sequencer.resize_initial_dur
                     desired_start = min(old_end - 0.1, max(0.0, cur_mouse_t))
                     new_start = self._limit_left_resize(track, clip, desired_start, old_end)
                     clip.start_time = new_start
                     clip.duration = old_end - new_start

             # 2. DRAGGING logic
             elif self.sequencer.drag_clip:
                  # Calculate new time/track
                  new_px = rel_x - self.sequencer.drag_offset
                  new_t = max(0.0, new_px / self.sequencer.zoom_x)
                  
                  new_t = max(0.0, new_px / self.sequencer.zoom_x)
                  
                  new_track_idx = int((rel_y - 25) // track_h)
                  
                  # Update clip
                  clip = self.sequencer.drag_clip
                  clip.start_time = new_t
                  
                  # Move track if changed
                  if 0 <= new_track_idx < len(self.sequencer.tracks):
                      if clip.track_index != new_track_idx:
                          # Remove from old
                          old_track = self.sequencer.tracks[clip.track_index]
                          if clip in old_track.clips:
                              old_track.clips.remove(clip)
                          # Add to new
                          clip.track_index = new_track_idx
                          self.sequencer.tracks[new_track_idx].clips.append(clip)
                          
                      # Snap to nearby clip edges and prevent overlaps on the active track
                      active_track = self.sequencer.tracks[clip.track_index]
                      snapped_start = self._snap_to_edges(active_track, clip, clip.start_time)
                      clip.start_time = self._avoid_overlap_on_drag(active_track, clip, snapped_start)
                          
             # 3. SCRUBBING logic (Sticky)
             elif self.sequencer.is_scrubbing or (dpg.is_item_hovered("timeline_canvas") and not self.sequencer.drag_clip and not self.sequencer.resize_clip):
                 # Check if we are starting a new scrub outside of bounds
                 total_h = 25 + len(self.sequencer.tracks) * 80
                 if not self.sequencer.is_scrubbing and rel_y > total_h:
                     pass # Do nothing
                 else:
                     # Scrubbing
                     new_t = max(0.0, rel_x / self.sequencer.zoom_x)
                     
                     # Snap to Zero
                     if new_t < 0.05: new_t = 0.0
                     
                     self.sequencer.current_time = new_t

        else:
            # Mouse Up - Release Drag/Resize/Scrub
            if self.sequencer.drag_clip:
                self.sequencer.drag_clip = None
            if self.sequencer.resize_clip:
                self.sequencer.resize_clip = None
            if hasattr(self.sequencer, 'is_scrubbing'):
                self.sequencer.is_scrubbing = False
        
        if self.sequencer.is_playing:
            now = time.time()
            dt = now - self.sequencer.last_tick
            self.sequencer.last_tick = now
            self.sequencer.current_time += dt
            
            # Auto-Stop / Loop Logic
            max_end = 0.0
            has_clips = False
            for t in self.sequencer.tracks:
                for c in t.clips:
                    max_end = max(max_end, c.start_time + c.duration)
                    has_clips = True
            
            if has_clips:
                if dpg.get_value("chk_loop"):
                    # Loop Mode: Restart after buffer
                    if self.sequencer.current_time > max_end + 0.5: # 0.5s buffer
                        self.action_restart()
                else:
                    # Normal Mode: Stop exactly at end
                    if self.sequencer.current_time >= max_end:
                        self.sequencer.current_time = max_end
                        self.toggle_play() # Force Stop

            if self.sequencer.is_playing: self.process_sequencer_logic()
            
            # Update Telemetry
            force, is_active = self.calculate_current_force()
            dpg.set_value("force_gauge", force)

            # Update Torque Monitor
            # Default to 8.0 Nm if not set (could make this a variable later accessible via UI)
            max_torque = dpg.get_value("input_max_torque") if dpg.does_item_exist("input_max_torque") else 8.0
            
            # force is -100 to 100 (normalized signal)
            current_nm = (force / 100.0) * max_torque
            
            dpg.set_value("txt_torque_val", f"{current_nm:.2f} Nm ({force:.0f}%)")
            dpg.set_value("bar_torque", (abs(current_nm)/max_torque) if max_torque > 0 else 0)

            # Statistics Update
            # Only update stats if we are actively playing a clip (not just silence)
            if self.sequencer.is_playing and is_active:
                abs_val = abs(current_nm)
                self.stats_peak = max(self.stats_peak, abs_val)
                self.stats_min = min(self.stats_min, abs_val)
                self.stats_sum += abs_val
                self.stats_count += 1
                
                avg = self.stats_sum / max(1, self.stats_count)
                
                dpg.set_value("txt_peak", f"Peak: {self.stats_peak:.2f} Nm")
                dpg.set_value("txt_avg", f"Avg: {avg:.2f} Nm")
                dpg.set_value("txt_min", f"Min: {self.stats_min:.2f} Nm")


            
        dpg.set_value("time_display", f"{self.sequencer.current_time:.2f}s")
        self.render_timeline()
        
        # Draw Cursor Overlay AFTER timeline render (z-order)
        should_hide_system = False
        if resize_clip_hover or self.sequencer.resize_clip:
             should_hide_system = True
             self._draw_resize_cursor(rel_x, rel_y)
        
        self._set_system_cursor_visible(not should_hide_system)

    def process_sequencer_logic(self):
        cur_t = self.sequencer.current_time
        
        # Apply Global Gain
        gain = 1.0
        if dpg.does_item_exist("slider_gain"):
             gain = dpg.get_value("slider_gain") / 100.0
             
        for t in self.sequencer.tracks:
            for clip in t.clips:
                # Check if inside
                if clip.start_time <= cur_t < (clip.start_time + clip.duration):
                    # Should be playing
                    if clip.active_effect_id == -1:
                        
                        eff_mag = int(clip.magnitude * gain)
                    
                        # Apply Safety Limit
                        if dpg.does_item_exist("input_max_torque") and dpg.does_item_exist("input_torque_limit"):
                            dev_peak = dpg.get_value("input_max_torque")
                            safe_limit = dpg.get_value("input_torque_limit")
                            if dev_peak > 0 and safe_limit > 0:
                                 # Ratio of limit to peak
                                 ratio = safe_limit / dev_peak
                                 limit_mag = int(ratio * 32767.0)
                                 if eff_mag > limit_mag: eff_mag = limit_mag
                                 if eff_mag < -limit_mag: eff_mag = -limit_mag # Magnitude is usually positive in Clip, but safe to check
                    
                    # Start it
                        if clip.type == "Sine":
                           if clip.frequency != clip.frequency_end:
                               # Sweep (Chirp)
                               eid = engine.start_effect_sweep(clip.frequency, clip.frequency_end, int(clip.duration * 1000), eff_mag)
                           else:
                               # Standard Sine
                               eid = engine.start_effect_sine(clip.frequency, eff_mag, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                        elif clip.type == "Constant":
                           eid = engine.start_effect_constant(eff_mag, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                        elif clip.type == "Ramp":
                           # Ramp up from 0 to Magnitude
                           eid = engine.start_effect_ramp(0, eff_mag, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                        elif clip.type == "Sawtooth":
                           # Period based on frequency
                           period = int(1000 / max(1, clip.frequency))
                           eid = engine.start_effect_sawtooth(eff_mag, period, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                else:
                    # Should stop
                    if clip.active_effect_id != -1:
                        clip.active_effect_id = -1
                        pass

    # --- Rendering ---
    def render_timeline(self):
        dpg.delete_item("timeline_canvas", children_only=True, slot=2)
        
        ruler_height = 25
        y_offset = ruler_height
        track_height = 80
        total_w = 3000
        total_h = ruler_height + len(self.sequencer.tracks) * track_height
        
        # 1. Draw Grid Lines (Vertical)
        # Every 1 second
        grid_step_s = 1.0
        max_time = total_w / self.sequencer.zoom_x
        
        cols = self.theme_colors

        current_grid_t = 0.0
        while current_grid_t < max_time:
            gx = current_grid_t * self.sequencer.zoom_x
            dpg.draw_line([gx, 0], [gx, total_h], color=cols["grid_line"], thickness=1, parent="timeline_canvas")
            current_grid_t += grid_step_s

        # 2. Draw Tracks
        for i, track in enumerate(self.sequencer.tracks):
             bg_col = cols["track_bg_even"] if i % 2 == 0 else cols["track_bg_odd"]
             dpg.draw_rectangle([0, y_offset], [total_w, y_offset + track_height], color=bg_col, fill=bg_col, parent="timeline_canvas")
             dpg.draw_line([0, y_offset + track_height], [total_w, y_offset+track_height], color=cols["track_border"], parent="timeline_canvas")
             dpg.draw_text([10, y_offset + 5], track.name, size=15, color=cols["text_track"], parent="timeline_canvas")
             
             for clip in track.clips:
                 x_start = clip.start_time * self.sequencer.zoom_x
                 width = clip.duration * self.sequencer.zoom_x
                 
                 base_col = cols["clip_sine"]
                 if clip.type == "Constant": base_col = cols["clip_const"]
                 if clip.type == "Ramp": base_col = cols["clip_ramp"]
                 if clip.type == "Sawtooth": base_col = cols["clip_saw"]

                 border_col = (255, 255, 255) if clip == self.sequencer.selected_clip else base_col
                 if self.current_theme_mode == "Light" and clip != self.sequencer.selected_clip:
                      # Darker borders for light mode visibility if needed, or keep colored
                      pass

                 dpg.draw_rectangle([x_start, y_offset + 20], [x_start + width, y_offset + track_height - 5], color=border_col, thickness=2, fill=(base_col[0], base_col[1], base_col[2], 150), parent="timeline_canvas")
                 
                 # Waveform preview
                 wave_points = self._clip_wave_points(
                     clip,
                     x_start + 4,
                     max(4.0, width - 8),
                     y_offset + 28,
                     y_offset + track_height - 12,
                     samples= max(20, int(width / 6))
                 )
                 # Wave color contrast
                 wave_col = (240, 240, 240, 220) if self.current_theme_mode == "Dark" else (20, 20, 20, 200)
                 dpg.draw_polyline(wave_points, color=wave_col, thickness=2, parent="timeline_canvas")

                 # Display Name if set, else Type (Draw ABOVE waveform/rect in empty space)
                 display_txt = clip.name if clip.name else clip.type
                 dpg.draw_text([x_start, y_offset], display_txt, size=20, color=(255, 255, 255, 255), parent="timeline_canvas")

             y_offset += track_height
        
        # 3. Draw Ruler (Top)
        dpg.draw_rectangle([0, 0], [total_w, ruler_height], fill=cols["ruler_bg"], color=cols["ruler_border"], parent="timeline_canvas")
        dpg.draw_line([0, ruler_height], [total_w, ruler_height], color=cols["ruler_line"], parent="timeline_canvas")
        
        # Ticks
        curr_t = 0.0
        while curr_t < max_time:
            rx = curr_t * self.sequencer.zoom_x
            # Major tick every 1s
            dpg.draw_line([rx, ruler_height-10], [rx, ruler_height], color=cols["ruler_tick"], thickness=1, parent="timeline_canvas")
            dpg.draw_text([rx + 2, 2], f"{curr_t:.1f}", size=12, color=cols["ruler_text"], parent="timeline_canvas")
            
            # Minor ticks (0.1s)
            for m in range(1, 10):
                mx = (curr_t + m*0.1) * self.sequencer.zoom_x
                h = 5 if m == 5 else 3
                dpg.draw_line([mx, ruler_height-h], [mx, ruler_height], color=cols["ruler_tick"], thickness=1, parent="timeline_canvas")

            curr_t += 1.0

        # 4. Playhead
        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y_offset], color=cols["playhead"], thickness=2, parent="timeline_canvas")
        # Playhead Triangle
        dpg.draw_triangle([px-6, 0], [px+6, 0], [px, 12], fill=cols["playhead_fill"], color=cols["playhead"], parent="timeline_canvas")

    def _draw_resize_cursor(self, x, y):
        """Draws a custom double-headed arrow cursor at (x,y)."""
        # Draw on top of everything
        # Size - 30% smaller than 10 -> 7
        sz = 7 
        # Color
        col = (255, 255, 255, 255)
        outline_col = (0, 0, 0, 200) # Shadow/Outline
        
        # Thinner shaft for smaller size
        shaft_w = 3
        shaft_len = 4

        # Helper to draw arrow with outline
        def draw_arrow(cx, cy, direction):
            # direction: -1 left, 1 right
            offset = shaft_len * direction
            
            # Arrow Head
            # Tip
            p1 = [cx + (sz * direction) + offset, cy]
            # Back Top
            p2 = [cx + offset, cy - sz/1.5]
            # Back Bottom
            p3 = [cx + offset, cy + sz/1.5]
            
            # Shadow/Outline
            dpg.draw_triangle(p1, p2, p3, fill=outline_col, color=outline_col, parent="timeline_canvas", thickness=2)
            # Main White Triangle
            dpg.draw_triangle(p1, p2, p3, fill=col, color=col, parent="timeline_canvas")

        draw_arrow(x, y, -1) # Left
        draw_arrow(x, y, 1)  # Right
        
        # Center Line (Shaft)
        # Shadow
        dpg.draw_line([x - shaft_len, y], [x + shaft_len, y], color=outline_col, thickness=shaft_w+2, parent="timeline_canvas")
        # Main
        dpg.draw_line([x - shaft_len, y], [x + shaft_len, y], color=col, thickness=shaft_w, parent="timeline_canvas")

    def delete_selected_clip(self):
        if self.sequencer.selected_clip:
            self.sequencer.delete_clip(self.sequencer.selected_clip)
            self.sequencer.selected_clip = None
            dpg.hide_item("btn_delete")

    def on_drop_receive(self, sender, app_data):
        print(f"DEBUG DROP: Sender={sender}, Data={app_data}")
        self.handle_drop(app_data, sender) # Pass sender to debug

    def handle_drop(self, effect_type, sender="Unknown"):
        if not isinstance(effect_type, str): return
        
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        
        track_h = 80
        track_idx = int((rel_y - 25) // track_h)
        
        self.log(f"Drop [{sender}]: {effect_type} at {int(rel_x)},{int(rel_y)} -> Tk {track_idx+1}")
        
        if 0 <= track_idx < len(self.sequencer.tracks):
             time_s = max(0.0, rel_x / self.sequencer.zoom_x)
             clip = self.sequencer.add_clip(track_idx, effect_type, time_s)
             self.sequencer.selected_clip = clip
             self.log(f"Created Clip: {time_s:.2f}s")
        else:
             self.log(f"Drop Skipped: Invalid Track {track_idx}")

    def canvas_click(self, sender, app_data):
        # Clear existing menus on any click (Left or Right)
        if dpg.does_item_exist("win_clip_opts"): dpg.delete_item("win_clip_opts")
        if dpg.does_item_exist("win_track_opts"): dpg.delete_item("win_track_opts")

        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        
        track_h = 80
        track_idx = int((rel_y - 25) // track_h)
        time_s = rel_x / self.sequencer.zoom_x
        
        mouse_btn = app_data[0]
        
        if mouse_btn == 1:
            if 0 <= track_idx < len(self.sequencer.tracks):
                click_clip = self.sequencer.get_clip_at(track_idx, time_s)
                
                if click_clip:
                    # CLIP CONTEXT MENU (Native-style)
                    self.sequencer.selected_clip = click_clip 
                    
                    with dpg.window(tag="win_clip_opts", no_title_bar=True, no_resize=True, autosize=True, pos=mpos, no_move=True):
                         dpg.add_text(f"{click_clip.type} Options")
                         dpg.add_separator()
                         
                         dpg.add_input_text(tag="input_clip_name", default_value=click_clip.name, hint="Clip Name", width=150)
                         
                         def do_update_name(s, a, u):
                             key = dpg.get_item_label(s)
                             # Auto-save name on change or just let it stay for Duplicate?
                             # Actually we can just read it when Duplicate is clicked or when window closes?
                             # Let's just update immediately on change?
                             # dpg callback for input_text on_enter=True?
                             pass
                         
                         def do_save_name():
                             click_clip.name = dpg.get_value("input_clip_name")
                             self.render_timeline()
                             
                         # Bind enter to save? or just save on menu actions. 
                         # Let's save on any action.

                         def do_dup():
                             click_clip.name = dpg.get_value("input_clip_name") # Save name first
                             import copy
                             new_clip = Clip.from_dict(click_clip.to_dict())
                             new_clip.id = str(uuid.uuid4())
                             new_clip.start_time = click_clip.start_time + click_clip.duration
                             self.sequencer.tracks[track_idx].clips.append(new_clip)
                             self.sequencer.selected_clip = new_clip
                             dpg.delete_item("win_clip_opts")
                             self.log("Clip Duplicated")
                             self.render_timeline()
                             
                         def do_del_clip():
                             self.sequencer.delete_clip(click_clip)
                             dpg.delete_item("win_clip_opts")
                             self.render_timeline()
                             
                         # Add callback to save name when typing
                         dpg.set_item_callback("input_clip_name", lambda: setattr(click_clip, 'name', dpg.get_value("input_clip_name")) or self.render_timeline())

                         dpg.add_selectable(label="Duplicate Clip", callback=do_dup, width=150)
                         dpg.add_selectable(label="Delete Clip", callback=do_del_clip, width=150)
                         dpg.add_separator()
                         dpg.add_selectable(label="Close", callback=lambda: dpg.delete_item("win_clip_opts"), width=150)
                else:
                    # TRACK CONTEXT MENU (Native-style)
                    self.renaming_track_idx = track_idx
                    
                    with dpg.window(tag="win_track_opts", no_title_bar=True, no_resize=True, autosize=True, pos=mpos, no_move=True):
                        dpg.add_text(f"Track {track_idx+1}")
                        dpg.add_separator()
                        dpg.add_input_text(tag="input_rename", default_value=self.sequencer.tracks[track_idx].name, width=150)
                        
                        def do_rename():
                            name = dpg.get_value("input_rename")
                            if 0 <= self.renaming_track_idx < len(self.sequencer.tracks):
                                self.sequencer.tracks[self.renaming_track_idx].name = name
                            dpg.delete_item("win_track_opts")
                            
                        def do_delete_trk():
                            if 0 <= self.renaming_track_idx < len(self.sequencer.tracks):
                                del self.sequencer.tracks[self.renaming_track_idx]
                                self.sequencer.selected_clip = None 
                            dpg.delete_item("win_track_opts")

                        if dpg.add_button(label="Rename", callback=do_rename, width=150): pass
                        if dpg.add_button(label="Delete Track", callback=do_delete_trk, width=150): pass
            return

        # 1. Check for Resize (Priority: High)
        r_clip, r_edge = self._get_resize_hover(track_idx, rel_x)
        if r_clip:
             self.sequencer.resize_clip = r_clip
             self.sequencer.resize_edge = r_edge
             self.sequencer.resize_initial_dur = r_clip.duration
             self.sequencer.resize_initial_time = r_clip.start_time
             self.sequencer.selected_clip = r_clip # Select on resize interaction
             dpg.set_value("insp_start", r_clip.start_time)
             dpg.set_value("insp_dur", r_clip.duration)
             dpg.set_value("insp_mag", r_clip.magnitude)
             dpg.set_value("insp_freq", r_clip.frequency)
             dpg.set_value("insp_freq_end", r_clip.frequency_end)
             return # Handled

        # 2. Check for Selection / Drag (Priority: Medium)
        found = False
        if 0 <= track_idx < len(self.sequencer.tracks):
             clip = self.sequencer.get_clip_at(track_idx, time_s)
             if clip:
                 self.sequencer.selected_clip = clip
                 dpg.set_value("insp_start", clip.start_time)
                 dpg.set_value("insp_dur", clip.duration)
                 dpg.set_value("insp_mag", clip.magnitude)
                 dpg.set_value("insp_freq", clip.frequency)
                 dpg.set_value("insp_freq_end", clip.frequency_end)
                 
                 # Show/Hide End Freq based on type
                 if clip.type == "Sine": dpg.show_item("insp_freq_end")
                 else: dpg.hide_item("insp_freq_end")
                 
                 dpg.show_item("btn_delete")
                 found = True
                 
                 # Normal Drag
                 clip_px_start = clip.start_time * self.sequencer.zoom_x
                 self.sequencer.drag_clip = clip
                 self.sequencer.drag_offset = rel_x - clip_px_start
        
        # 3. Seek (Priority: Low)
        if not found:
             # Check if click is valid (within tracks)
             if track_idx >= len(self.sequencer.tracks):
                 return

             self.sequencer.selected_clip = None
             dpg.hide_item("btn_delete")
             self.sequencer.current_time = max(0.0, time_s)
             self.sequencer.is_scrubbing = True # Start scrubbing
             self.log(f"Seek to {self.sequencer.current_time:.2f}s")
             
    def update_selected_clip(self, sender, app_data, user_data):
        if not self.sequencer.selected_clip: return
        param = user_data
        if param == "freq": self.sequencer.selected_clip.frequency = app_data
        elif param == "freq_end": self.sequencer.selected_clip.frequency_end = app_data
        elif param == "mag": self.sequencer.selected_clip.magnitude = app_data
        elif param == "dur": self.sequencer.selected_clip.duration = app_data
        elif param == "start": self.sequencer.selected_clip.start_time = app_data

    # --- Palette Drag Source ---
    def make_drag_source(self, label, type):
        with dpg.group():
             dpg.add_button(label=label, width=100)
             with dpg.drag_payload(drag_data=type): # Removed payload_type for compatibility
                 dpg.add_text(f"Effect: {label}")

    def _set_system_cursor_visible(self, visible: bool):
        if not hasattr(self, 'cursor_visible'): self.cursor_visible = True
        
        # Only toggle if state changes to avoid flickering/counter issues
        if self.cursor_visible == visible: return
        
        self.cursor_visible = visible
        try:
            # Windows API ShowCursor
            # True = Increment display count, False = Decrement
            # We want to force it.
            
            if not visible:
                # Hide: Decrement until < 0
                while ctypes.windll.user32.ShowCursor(False) >= 0: pass
            else:
                # Show: Increment until >= 0
                while ctypes.windll.user32.ShowCursor(True) < 0: pass
                
        except Exception as e:
            print(f"Cursor Error: {e}")

            print(f"Cursor Error: {e}")

    def load_fonts(self):
        with dpg.font_registry():
            # Attempt to load Segoe UI
            font_path = "C:\\Windows\\Fonts\\segoeui.ttf"
            if os.path.exists(font_path):
                self.main_font = dpg.add_font(font_path, 16)
                dpg.bind_font(self.main_font)
                self.log("Loaded font: Segoe UI")
            else:
                self.log("Segoe UI not found. Using default font.")

    def apply_theme(self, mode="Dark"):
        self.current_theme_mode = mode
        
        if mode == "Dark":
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (25, 25, 30))
                    dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (30, 30, 35))
                    dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (30, 30, 35))
                    dpg.add_theme_color(dpg.mvThemeCol_Border, (60, 60, 70))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (45, 45, 50))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (60, 60, 65))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (80, 80, 90))
                    dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (40, 40, 45))
                    dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (50, 50, 60))
                    dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (35, 35, 40))
                    
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (50, 60, 70))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (70, 80, 100))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (100, 150, 255))
                    
                    dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (100, 150, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (100, 150, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (140, 180, 255))

                    dpg.add_theme_color(dpg.mvThemeCol_Text, (220, 220, 220))
                    dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg, (100, 150, 255, 150))
                    
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
            
            dpg.bind_theme(theme)
            
            # Canvas Colors (Dark)
            self.theme_colors = {
                "grid_line": (60, 60, 65, 100),
                "track_bg_even": (40, 40, 45),
                "track_bg_odd": (35, 35, 40),
                "track_border": (60, 60, 60),
                "text_track": (200, 200, 200),
                "ruler_bg": (30, 30, 35),
                "ruler_border": (50, 50, 60),
                "ruler_line": (100, 100, 100),
                "ruler_tick": (150, 150, 150),
                "ruler_text": (150, 150, 150),
                "playhead": (255, 50, 50),
                "playhead": (255, 50, 50),
                "playhead_fill": (255, 50, 50),
                "clip_sine": (100, 150, 255),
                "clip_const": (100, 255, 100),
                "clip_ramp": (255, 255, 100),
                "clip_saw": (255, 150, 50)
            }
            
            # Dynamic Colors for UI overrides 
            if dpg.does_item_exist("txt_torque_val"): dpg.configure_item("txt_torque_val", color=(150, 255, 150))
            if dpg.does_item_exist("txt_min"): dpg.configure_item("txt_min", color=(200, 200, 200))
            if dpg.does_item_exist("time_display"): dpg.configure_item("time_display", color=(255, 255, 255))
            if dpg.does_item_exist("txt_peak"): dpg.configure_item("txt_peak", color=(255, 100, 100))
            if dpg.does_item_exist("txt_avg"): dpg.configure_item("txt_avg", color=(100, 200, 255))
            if dpg.does_item_exist("txt_torque_title"): dpg.configure_item("txt_torque_title", color=(150, 255, 150))
            if dpg.does_item_exist("txt_logo"): dpg.configure_item("txt_logo", color=(100, 180, 255))
            # Status: If connected, Green. Disconnected, Red.
            if dpg.does_item_exist("status_text"):
                 curr_txt = dpg.get_value("status_text")
                 if "Connected" in curr_txt and "Disconnected" not in curr_txt:
                     dpg.configure_item("status_text", color=(0, 255, 0))
                 else:
                     dpg.configure_item("status_text", color=(255, 100, 100))
            
        else: # LIGHT MODE
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (240, 240, 240))
                    dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (245, 245, 250))
                    dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (250, 250, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_Border, (200, 200, 210))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (220, 220, 230))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (200, 200, 220))
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (180, 200, 240))
                    dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (230, 230, 235))
                    dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (220, 220, 230))
                    dpg.add_theme_color(dpg.mvThemeCol_MenuBarBg, (235, 235, 240))
                    
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (220, 225, 230))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (210, 220, 240))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (150, 180, 255))
                    
                    dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (50, 100, 200))
                    dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (50, 100, 200))
                    dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (80, 130, 255))

                    dpg.add_theme_color(dpg.mvThemeCol_Text, (30, 30, 30))
                    dpg.add_theme_color(dpg.mvThemeCol_TextSelectedBg, (100, 150, 255, 100))
                    
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
            
            dpg.bind_theme(theme)

            # Canvas Colors (Light)
            self.theme_colors = {
                "grid_line": (160, 160, 170, 255),
                "track_bg_even": (255, 255, 255),
                "track_bg_odd": (245, 245, 250),
                "track_border": (180, 180, 200),
                "text_track": (20, 20, 20),
                "ruler_bg": (235, 235, 240),
                "ruler_border": (180, 180, 200),
                "ruler_line": (150, 150, 160),
                "ruler_tick": (100, 100, 110),
                "ruler_text": (50, 50, 60),
                "playhead": (255, 50, 50),
                "playhead": (255, 50, 50),
                "playhead_fill": (255, 50, 50),
                # High Contrast for Light Mode
                "clip_sine": (0, 100, 200),     # Darker Blue
                "clip_const": (0, 180, 0),      # Darker Green
                "clip_ramp": (220, 180, 0),     # Darker Yellow/Gold
                "clip_saw": (220, 100, 0)       # Darker Orange
            }

            # Dynamic Colors for UI overrides (Light)
            if dpg.does_item_exist("txt_torque_val"): dpg.configure_item("txt_torque_val", color=(0, 160, 0)) # Darker Green
            if dpg.does_item_exist("txt_min"): dpg.configure_item("txt_min", color=(80, 80, 80)) # Dark Grey
            if dpg.does_item_exist("time_display"): dpg.configure_item("time_display", color=(20, 20, 20))
            if dpg.does_item_exist("txt_peak"): dpg.configure_item("txt_peak", color=(200, 0, 0)) # Dark Red
            if dpg.does_item_exist("txt_avg"): dpg.configure_item("txt_avg", color=(0, 80, 200)) # Dark Blue
            if dpg.does_item_exist("txt_torque_title"): dpg.configure_item("txt_torque_title", color=(0, 120, 0)) # Dark Green
            if dpg.does_item_exist("txt_logo"): dpg.configure_item("txt_logo", color=(0, 80, 180)) # Dark Blue
            
            # Status
            if dpg.does_item_exist("status_text"):
                 curr_txt = dpg.get_value("status_text")
                 if "Connected" in curr_txt and "Disconnected" not in curr_txt:
                     dpg.configure_item("status_text", color=(0, 150, 0)) # Dark Green
                 else:
                     dpg.configure_item("status_text", color=(200, 0, 0)) # Dark Red
    def create_status_themes(self):
        with dpg.theme(tag="theme_btn_red"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 100, 100)) # Red Text
        
        with dpg.theme(tag="theme_btn_green"):
             with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Text, (100, 255, 100)) # Green Text

    def action_toggle_torque(self):
        # Initialize state if needed
        if not hasattr(self, 'is_torque_open'): self.is_torque_open = False
        
        self.is_torque_open = not self.is_torque_open
        
        # Color Update
        if self.is_torque_open:
            dpg.bind_item_theme("btn_toggle_torque", "theme_btn_green")
        else:
             dpg.bind_item_theme("btn_toggle_torque", "theme_btn_red")
             
        # Visibility Update
        pinned = getattr(self, 'torque_pinned', False)
        
        if pinned:
            # If pinned, we show/hide the content group within Inspector
            if self.is_torque_open:
                dpg.show_item("grp_torque_content")
            else:
                dpg.hide_item("grp_torque_content")
        else:
            # If floating, we show/hide the Window
            if self.is_torque_open:
                dpg.show_item("win_torque_monitor")
            else:
                dpg.hide_item("win_torque_monitor")

    def on_torque_win_close(self, sender, app_data):
        """Callback for when the 'X' is clicked on the floating window."""
        self.is_torque_open = False
        dpg.bind_item_theme("btn_toggle_torque", "theme_btn_red")

    def toggle_torque_pin(self):
        # Toggle State
        if not hasattr(self, 'torque_pinned'): self.torque_pinned = False
        self.torque_pinned = not self.torque_pinned
        
        if self.torque_pinned:
             # Pin to Inspector
             dpg.move_item("grp_torque_content", parent="inspector_win", before="sep_log")
             dpg.configure_item("btn_pin_torque", label="Undock") # Pop out
             dpg.hide_item("win_torque_monitor")
        else:
             # Unpin (Float)
             dpg.move_item("grp_torque_content", parent="win_torque_monitor")
             dpg.configure_item("btn_pin_torque", label="Dock") # Pin/Dock
             dpg.show_item("win_torque_monitor")

    def on_mouse_wheel(self, sender, app_data):
        # app_data is usually float (positive for up/forward, negative for down/back)
        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            # Zoom Logic
            zoom_speed = 1.1
            if app_data > 0:
                self.sequencer.zoom_x *= zoom_speed
            else:
                self.sequencer.zoom_x /= zoom_speed
                
            # Clamp Zoom
            self.sequencer.zoom_x = max(10.0, min(2000.0, self.sequencer.zoom_x))
            
            # Redraw
            self.render_timeline()

    def setup_ui(self):
        self.apply_theme("Dark") # Default
        self.create_status_themes() # Status colors
        
        # File Dialogs
        dpg.add_file_dialog(
            directory_selector=False, show=False, callback=self.action_save_file, tag="dlg_save",
            width=700, height=400, default_filename="project.fedit", label="Save Project As..."
        )
        dpg.add_file_extension(".fedit", color=(255, 255, 255, 255), parent="dlg_save")
        
        dpg.add_file_dialog(
            directory_selector=False, show=False, callback=self.action_load_file, tag="dlg_load",
            width=700, height=400, label="Open Project File..."
        )
        dpg.add_file_extension(".fedit", color=(255, 255, 255, 255), parent="dlg_load")
        dpg.add_file_extension(".*", parent="dlg_load")

        # Shortcuts
        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_S, callback=lambda: dpg.show_item("dlg_save") if (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)) else None)
            dpg.add_key_press_handler(dpg.mvKey_O, callback=lambda: dpg.show_item("dlg_load") if (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)) else None)
            dpg.add_mouse_wheel_handler(callback=self.on_mouse_wheel)

        with dpg.window(tag="Main"):
            # Menu Bar
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Save Project", shortcut="(Ctrl+S)", callback=lambda: dpg.show_item("dlg_save"))
                    dpg.add_menu_item(label="Open Project", shortcut="(Ctrl+O)", callback=lambda: dpg.show_item("dlg_load"))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())

                with dpg.menu(label="View"):
                     dpg.add_menu_item(label="Torque Monitor", callback=lambda: self.toggle_torque_pin() if getattr(self, 'torque_pinned', False) else dpg.show_item("win_torque_monitor"))
                     with dpg.menu(label="Theme"):
                         dpg.add_menu_item(label="Dark Mode", callback=lambda: self.apply_theme("Dark"))
                         dpg.add_menu_item(label="Light Mode", callback=lambda: self.apply_theme("Light"))

            # Torque Monitor Window (Initially Hidden or Shown)
            with dpg.window(tag="win_torque_monitor", label="Torque Monitor", width=300, height=270, pos=[400, 100], show=False, no_resize=True, no_scrollbar=True, on_close=self.on_torque_win_close):
                 # Group content for moving
                 with dpg.group(tag="grp_torque_content"):
                     # Title Header with Pin Button
                     with dpg.group(horizontal=True):
                         dpg.add_text("Real-time Torque", tag="txt_torque_title", color=(150, 255, 150))
                         dpg.add_spacer(width=90) # Adjusted for text width
                         dpg.add_button(label="Dock", tag="btn_pin_torque", width=50, height=20, callback=self.toggle_torque_pin)

                     dpg.add_text("0.00 Nm", tag="txt_torque_val") 
                     dpg.add_progress_bar(tag="bar_torque", width=-1, height=20)
                     
                     dpg.add_spacer(height=5)
                     with dpg.group(horizontal=True):
                         dpg.add_text("Peak: --", tag="txt_peak", color=(255, 100, 100))
                         dpg.add_spacer(width=10)
                         dpg.add_text("Avg: --", tag="txt_avg", color=(100, 200, 255))
                         dpg.add_spacer(width=10)
                         dpg.add_text("Min: --", tag="txt_min", color=(200, 200, 200))

                     dpg.add_separator()
                     # Settings Group
                     dpg.add_text("Settings:")
                     with dpg.group(horizontal=True):
                         dpg.add_text("Device Peak Torque:")
                         dpg.add_input_float(tag="input_max_torque", default_value=8.0, width=120, step=0.5)
                         with dpg.tooltip("input_max_torque"):
                             dpg.add_text("The physical maximum torque (Nm) of your hardware.\nSet this to match your wheelbase to ensure accurate monitoring.")

                     with dpg.group(horizontal=True):
                         dpg.add_text("Safety Limit (Nm):   ")
                         dpg.add_input_float(tag="input_torque_limit", default_value=20.0, width=120, step=0.5)
                         with dpg.tooltip("input_torque_limit"):
                             dpg.add_text("Clamps the output force to this Nm value.\nUse this to reduce strength without affecting the reference calibration.")
                         
                     with dpg.group(horizontal=True):
                         dpg.add_text("Master Gain (%):  ")
                         dpg.add_slider_int(tag="slider_gain", default_value=100, min_value=0, max_value=100, width=100)
                         with dpg.tooltip("slider_gain"):
                             dpg.add_text("Scales the overall output strength. 100% = No reduction.\n\nNOTE: Ensure your wheelbase software is set to 100% strength for accurate Nm readings.")

            
            # Set Main as a fallback drop target without payload type check
            try: dpg.set_item_drop_callback("Main", self.on_drop_receive)
            except: pass

            # Top Bar
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=8)
                
                # LOGO
                with dpg.group():
                    dpg.add_text("Fedit DAW", tag="txt_logo", color=(100, 180, 255))
                    
                dpg.add_spacer(width=20)
                # Separator Replaced by Spacer
                dpg.add_spacer(width=20)
                
                # DEVICE SECTION
                with dpg.group(horizontal=True):
                    dpg.add_text("Device:")
                    dpg.add_combo(tag="device_combo", width=200)
                    dpg.add_button(label="Scan", callback=self.scan_devices)
                    dpg.add_button(label="Connect", callback=self.connect_callback)
                    dpg.add_text("Status: Disconnected", tag="status_text", color=(255, 100, 100))
                
                dpg.add_spacer(width=20)
                # Separator Replaced by Spacer
                dpg.add_spacer(width=20)
                
                # MONITOR SECTION
                with dpg.group(horizontal=True):
                    dpg.add_text("Force:")
                    dpg.add_slider_float(tag="force_gauge", width=120, min_value=-100, max_value=100, format="%.0f%%")

                dpg.add_spacer(width=20)
                # Separator Replaced by Spacer
                dpg.add_spacer(width=20)

                # TRANSPORT SECTION
                with dpg.group(horizontal=True):
                    dpg.add_button(tag="btn_toggle_torque", label="Torque Monitor", callback=self.action_toggle_torque)
                    dpg.bind_item_theme("btn_toggle_torque", "theme_btn_red") # Default Off
                    
                    dpg.add_button(tag="btn_play", label="Play", width=60, callback=self.toggle_play)
                    dpg.add_button(label="Restart", callback=self.action_restart) # Using restart as Stop/Return for now
                    dpg.add_checkbox(label="Loop", tag="chk_loop")
                    dpg.add_spacer(width=10)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Time:")
                        dpg.add_text("0.00s", tag="time_display")

            dpg.add_separator()

            with dpg.table(header_row=False, resizable=True, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=150) # Palette
                dpg.add_table_column() # Timeline
                dpg.add_table_column(width_fixed=True, init_width_or_weight=300) # Inspector

                with dpg.table_row():
                    
                    # Col 1: Palette
                    with dpg.child_window(height=-1):
                        dpg.add_text("Effects")
                        dpg.add_separator()
                        self.make_drag_source("Sine Wave", "Sine")
                        self.make_drag_source("Constant", "Constant")
                        self.make_drag_source("Ramp", "Ramp")
                        self.make_drag_source("Sawtooth", "Sawtooth")
                        
                        dpg.add_spacer(height=20)
                        dpg.add_text("Tools")
                        dpg.add_button(label="+ Add Track", width=100, callback=lambda: self.sequencer.tracks.append(Track(name=str(len(self.sequencer.tracks) + 1))))

                    # Col 2: Timeline
                    with dpg.group(tag="timeline_group"):
                         # Scroll Window
                         with dpg.child_window(tag="timeline_scroll", horizontal_scrollbar=True):
                                 with dpg.drawlist(width=3000, height=1000, tag="timeline_canvas"):
                                    pass

                         # EXPLICIT TARGET to handle drop visualization and acceptance
                         # Removed invalid widget call. Relying on Main Window callback.
                         
                         with dpg.item_handler_registry(tag="timeline_click_handler"):
                                 dpg.add_item_clicked_handler(callback=self.canvas_click)
                             
                         dpg.bind_item_handler_registry("timeline_canvas", "timeline_click_handler")
                             
                         # Enable Drop
                         try:
                             # We set the callback on the Main window at the end of setup_ui
                             # But we can also try the child window one last time as a backup
                             dpg.set_item_drop_callback("timeline_scroll", self.on_drop_receive)
                         except Exception as e: print(f"Init Warning: {e}")

                    # Col 3: Inspector
                    with dpg.child_window(tag="inspector_win", height=-1):
                        dpg.add_text("Inspector")
                        dpg.add_separator()
                        dpg.add_input_float(label="Start (s)", tag="insp_start", callback=self.update_selected_clip, user_data="start")
                        dpg.add_input_float(label="Duration (s)", tag="insp_dur", callback=self.update_selected_clip, user_data="dur")
                        dpg.add_slider_int(label="Magnitude", tag="insp_mag", max_value=32000, callback=self.update_selected_clip, user_data="mag")
                        dpg.add_slider_int(label="Frequency", tag="insp_freq", max_value=100, callback=self.update_selected_clip, user_data="freq")
                        dpg.add_slider_int(label="End Freq (Sine)", tag="insp_freq_end", max_value=100, callback=self.update_selected_clip, user_data="freq_end", show=False)
                        
                        dpg.add_separator()
                        dpg.add_button(label="DELETE CLIP", callback=self.delete_selected_clip, width=-1, show=False, tag="btn_delete")
                        dpg.add_separator(tag="sep_log")
                        dpg.add_text("Log")
                        dpg.add_listbox(tag="log_list", num_items=10, width=-1)

        # FINAL BINDING
        try:
            dpg.set_item_drop_callback("Main", self.on_drop_receive)
        except Exception as e: print(f"Main Drop Bind Error: {e}")

    def run(self):
        # Enable High DPI Awareness (Fix blurriness)
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass # Fails on non-Windows or older Windows

        dpg.create_viewport(title='Fedit DAW 2.0', width=1280, height=800)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("Main", True)
        
        self.scan_devices()

        while dpg.is_dearpygui_running():
            self.update_loop()
            dpg.render_dearpygui_frame()
            
        dpg.destroy_context()

if __name__ == "__main__":
    app = FeditNativeApp()
    app.run()
