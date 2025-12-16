import dearpygui.dearpygui as dpg
import sys
import os
import time
from dataclasses import dataclass, field
import uuid
import json
import math
import warnings

# Filter benign PySDL2 warning
warnings.filterwarnings("ignore", message="pysdl2-dll is installed as source-only")
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
    start_time: float = 0.0 # Seconds
    duration: float = 2.0 # Seconds
    track_index: int = 0
    # Parameters
    magnitude: int = 3276 # Default 10%
    frequency: int = 10
    frequency_end: int = 10 # For Sweep
    start_phase: int = 0
    sweep_enabled: bool = False
    name: str = "Clip"
    active_effect_id: int = -1 # Runtime ID
    
    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "start_time": self.start_time,
            "duration": self.duration, "track_index": self.track_index,
            "magnitude": self.magnitude, "frequency": self.frequency,
            "frequency_end": self.frequency_end, "sweep_enabled": self.sweep_enabled,
            "start_phase": self.start_phase,
            "name": self.name
        }
    
    @staticmethod
    def from_dict(d):
        freq = d.get("frequency", 10)
        c = Clip(
            id=d.get("id", str(uuid.uuid4())), type=d.get("type", "Sine"),
            start_time=d.get("start_time", 0.0), duration=d.get("duration", 1.0),
            track_index=d.get("track_index", 0),
            magnitude=d.get("magnitude", 3276), frequency=freq, # Default 10%
            frequency_end=d.get("frequency_end", freq), # Default to start freq if missing
            start_phase=d.get("start_phase", 0),
            sweep_enabled=d.get("sweep_enabled", False),
            name=d.get("name", "Clip")
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
        self.clipboard: dict = None # For Copy/Paste

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

    def get_clip_at_precise(self, track_idx, time_s):
        # Half-open interval [start, end) logic for playback to avoid overlap
        for clip in self.tracks[track_idx].clips:
            if clip.start_time <= time_s < clip.start_time + clip.duration:
                return clip
        return None

    def get_clip_by_id(self, clip_id):
        if clip_id is None: return None
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id: return clip
        return None


class InspectorPanel:
    def __init__(self, app, parent, clip=None, pos=[400, 200]):
        self.app = app
        self.clip = clip # If None, acts as "Live" inspector for selection
        self.parent = parent
        self.id = str(uuid.uuid4())[:8]
        
        # Unique Tags
        self.tag_tab = f"tab_{self.id}"
        self.tag_start = f"insp_start_{self.id}"
        self.tag_dur = f"insp_dur_{self.id}"
        self.tag_mag = f"insp_mag_{self.id}"
        self.tag_freq = f"insp_freq_{self.id}"
        self.tag_freq_end = f"insp_freq_end_{self.id}"
        self.tag_phase = f"insp_phase_{self.id}"
        self.tag_sweep = f"insp_sweep_{self.id}"
        self.tag_title = f"insp_title_{self.id}"
        
        label = "Inspector (Live)" if not clip else (clip.name or f"Clip {clip.type}")
        
        self.is_window = (parent is None)
        
        # Container Context Manager matching the type (Window or Tab)
        if self.is_window:
             self.container = dpg.window(label=label, tag=self.tag_tab, autosize=True, pos=pos)
        else:
             self.container = dpg.tab(label=label, tag=self.tag_tab, parent=parent, closable=(clip is not None))
             
        with self.container:
             dpg.add_text("Properties", tag=self.tag_title)
             dpg.add_separator()
             
             # Fields
             dpg.add_input_float(label="Start (s)", tag=self.tag_start, callback=self.on_change, user_data="start")
             dpg.add_input_float(label="Duration (s)", tag=self.tag_dur, callback=self.on_change, user_data="dur")
             dpg.add_slider_int(label="Magnitude %", tag=self.tag_mag, max_value=100, callback=self.on_change, user_data="mag")
             
             dpg.add_separator()
             dpg.add_input_int(label="Frequency (Hz)", tag=self.tag_freq, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq")
             
             dpg.add_checkbox(label="Enable Sweep", tag=self.tag_sweep, callback=self.on_change, user_data="sweep")
             dpg.add_input_int(label="End Freq (Hz)", tag=self.tag_freq_end, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq_end")
             
             dpg.add_separator()
             # if not clip:
             #     dpg.add_button(label="Open in New Tab", callback=self.duplicate_to_tab)
    
    def get_target_clip(self):
        if self.clip: return self.clip
        return self.app.sequencer.selected_clip

    def update(self):
        clip = self.get_target_clip()
        if not clip:
            # Disable or clear?
            # dpg.disable_item(self.tag_tab) # Can't disable tab content easily?
            dpg.set_value(self.tag_title, "No Selection")
            return
        
        dpg.set_value(self.tag_title, f"Clip: {clip.type} ({clip.id[:4]})")
        
        # Update Values (Only if not active to allow typing?)
        # Simple check: dpg.is_item_active
        
        def safe_set(tag, val):
            if not dpg.is_item_active(tag):
                dpg.set_value(tag, val)
        
        # Name update removed from here (handled via Right Click)

        
        safe_set(self.tag_start, clip.start_time)
        safe_set(self.tag_dur, clip.duration)
        safe_set(self.tag_mag, int((clip.magnitude / 32767.0) * 100))
        safe_set(self.tag_freq, clip.frequency)
        safe_set(self.tag_sweep, clip.sweep_enabled)
        
        if clip.type == "Sine":
            dpg.show_item(self.tag_sweep)
            if clip.sweep_enabled:
                dpg.show_item(self.tag_freq_end)
                safe_set(self.tag_freq_end, clip.frequency_end)
            else:
                dpg.hide_item(self.tag_freq_end)
        else:
             dpg.hide_item(self.tag_sweep)
             dpg.hide_item(self.tag_freq_end)

    def on_change(self, sender, app_data, user_data):
        clip = self.get_target_clip()
        if not clip: return
        
        param = user_data
        param = user_data
        if param == "start": clip.start_time = app_data
        elif param == "dur": clip.duration = max(0.01, app_data)
        elif param == "mag": 
            val = max(0, min(100, app_data))
            clip.magnitude = int((val / 100.0) * 32767)
        elif param == "freq": 
            if app_data < 1: 
                dpg.set_value(sender, 1)
                clip.frequency = 1
            else:
                clip.frequency = app_data
        elif param == "phase": clip.start_phase = max(0, min(360, app_data))
        elif param == "freq_end": 
             if app_data < 1:
                 dpg.set_value(sender, 1)
                 clip.frequency_end = 1
             else:
                 clip.frequency_end = app_data
        elif param == "sweep": 
            clip.sweep_enabled = app_data
            self.update() # Refresh visibility immediately
            
    def duplicate_to_tab(self, sender, app_data):
        clip = self.get_target_clip()
        if clip:
            self.app.create_floating_inspector(clip)

# --- Application ---
class FeditNativeApp:
    def __init__(self):
        self.sequencer = FeditSequencer()
        self.sequencer.is_scrubbing = False # New state for playhead dragging
        self.log_items = []
        self.fps_frames = 0
        self.fps_last_time = time.time()
        self.renaming_track_idx = -1
        self.renaming_track_idx = -1
        self.drag_target_track_idx = -1 # For visual highlight
        self.inspectors = [] # List of InspectorPanel instances
        
        dpg.create_context()
        self.setup_ui()
        
        # Init Engine
        try:
            self.log("Initializing Haptic Subsystem...")
            engine.init_sdl()
        except Exception as e:
            self.log(f"SDL Init Error: {e}")
            
        # Playback State: { track_index: {'effect_id': -1, 'clip_id': None} }
        self.track_states = {}

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
        start_f = clip.frequency
        end_f = start_f
        
        # Only use frequency_end if sweep is actually enabled
        if getattr(clip, 'sweep_enabled', False):
             end_f = getattr(clip, 'frequency_end', start_f)
        
        if start_f == end_f:
             omega = 2 * math.pi * start_f
             return mag * math.sin(omega * t)
        else:
             # Chirp: phase = 2*pi * (f0*t + 0.5*k*t^2)
             k = (end_f - start_f) / max(1e-6, clip.duration)
             phase = 2 * math.pi * (start_f * t + 0.5 * k * t * t)
             return mag * math.sin(phase)

    def _clip_wave_points(self, clip: Clip, x_start: float, width: float, y_top: float, y_bottom: float, samples: int = 0):
        # Precise Peak-Detection for Aliasing
        points = []
        y_mid = (y_top + y_bottom) / 2.0
        amp_span = (y_bottom - y_top) * 0.45 
        mag_max = 32767.0 # Normalize against full scale, so lower magnitude = smaller wave
        
        # Calculate visualization scale based on clip magnitude
        clip_scale = clip.magnitude / mag_max

        if width <= 0: return []
        
        pixels = int(width)
        step_t = clip.duration / max(1, pixels)
        
        # Hard limit
        if pixels > 5000: pixels = 5000; step_t = clip.duration / pixels

        freq = max(clip.frequency, getattr(clip, 'frequency_end', clip.frequency))
        is_aliasing = False
        if freq > 0 and (1.0/freq) < step_t * 2.5:
             is_aliasing = True

        for i in range(pixels):
            t0 = i * step_t
            t1 = (i + 1) * step_t
            
            local_min = 0.0
            local_max = 0.0

            if is_aliasing:
                # Aliasing: Draw full height relative to magnitude
                local_min = -1.0 * clip_scale
                local_max = 1.0 * clip_scale
            else:
                # Sub-pixel sampling: Check 10 points to catch peaks accurately
                # This prevents "pre-aliasing" noise where we miss the sine peak indtermittently
                local_min = 1.0 # Init inverted
                local_max = -1.0
                
                # Check 8 internal points + edges
                for k in range(8):
                    ft = t0 + (t1 - t0) * (k / 7.0)
                    val = self._wave_amplitude(clip, ft) / mag_max
                    if val < local_min: local_min = val
                    if val > local_max: local_max = val

            y_l = y_mid - max(-1.0, min(1.0, local_min)) * amp_span
            y_h = y_mid - max(-1.0, min(1.0, local_max)) * amp_span
            
            x = x_start + i
            points.append([x, y_l])
            points.append([x, y_h])
            
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
                 dpg.configure_item("status_text", color=(0, 255, 0))
                 
                 # Reset previous effect states as we have a new device
                 for k in self.track_states:
                     self.track_states[k] = {'effect_id': -1, 'clip_id': None, 'clip_type': None}
                 
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
        
        normalized = (total_force * gain / 32767.0) * 100.0
        
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
        if self.sequencer.is_playing:
            dpg.configure_item("btn_play", label="Stop")
            self.sequencer.last_tick = time.time()
            
            # Reset Stats (from Main)
            self.stats_peak = 0.0
            self.stats_min = 9999.0
            self.stats_sum = 0.0
            self.stats_count = 0
            if dpg.does_item_exist("txt_peak"): dpg.set_value("txt_peak", "Peak: --")
            if dpg.does_item_exist("txt_avg"): dpg.set_value("txt_avg", "Avg: --")
            if dpg.does_item_exist("txt_min"): dpg.set_value("txt_min", "Min: --")
            
            # Auto-rewind if at end
            # Auto-rewind if at end AND we have content
            max_dur = 0
            has_content = False
            for t in self.sequencer.tracks:
                for c in t.clips:
                    max_dur = max(max_dur, c.start_time + c.duration)
                    has_content = True
            
            if has_content and self.sequencer.current_time >= max_dur - 0.1:
                self.sequencer.current_time = 0.0
        else:
            dpg.configure_item("btn_play", label="Play")
            engine.stop_effect() # Stop all
            
            # Reset valid states (HEAD)
            for k in self.track_states:
                self.track_states[k] = {'effect_id': -1, 'clip_id': None, 'clip_type': None, 'phase_acc': 0.0}
            
            # Reset active IDs (Main)
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

    def get_clip_at_pos(self, rel_x, rel_y):
        track_h = 80
        track_idx = int(rel_y // track_h)
        click_time = max(0.0, rel_x / self.sequencer.zoom_x)
        
        if 0 <= track_idx < len(self.sequencer.tracks):
             for clip in self.sequencer.tracks[track_idx].clips:
                 if clip.start_time <= click_time < (clip.start_time + clip.duration):
                     return clip
        return None

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
        # Track Target for Drop/Drag
        mpos = dpg.get_mouse_pos(local=False)
        rx, ry = self.get_canvas_relative_pos(mpos)
        mpos = dpg.get_mouse_pos(local=False)
        rx, ry = self.get_canvas_relative_pos(mpos)
        self.drag_target_track_idx = int(ry // 80)
        
        # KEYBOARD SHORTCUTS
        if dpg.is_key_pressed(dpg.mvKey_Delete):
            # Check if we are focused on timeline or canvas to avoid deleting while typing
            # Using simple hover check for now as focus might be tricky
            if dpg.is_item_hovered("timeline_canvas") or dpg.is_item_focused("timeline_scroll"):
                 self.delete_selected_clip()

        # COPY / PASTE (Ctrl+C, Ctrl+V)
        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            if dpg.is_key_pressed(dpg.mvKey_C):
                if self.sequencer.selected_clip:
                    # Deep copy via dictionary serialization
                    self.sequencer.clipboard = self.sequencer.selected_clip.to_dict()
                    self.log(f"Copied {self.sequencer.selected_clip.name or 'Clip'}")
            
            if dpg.is_key_pressed(dpg.mvKey_V):
                if self.sequencer.clipboard:
                    # Create new clip logic
                    data = self.sequencer.clipboard
                    new_clip = Clip.from_dict(data)
                    # New ID
                    new_clip.id = str(uuid.uuid4())
                    # Position at playhead
                    new_clip.start_time = self.sequencer.current_time
                    
                    # Target Track (Same as original if possible, or try to respect selected track if implementing)
                    # For now: Same track
                    target_t_idx = new_clip.track_index
                    if 0 <= target_t_idx < len(self.sequencer.tracks):
                        t = self.sequencer.tracks[target_t_idx]
                        t.clips.append(new_clip)
                        # Fix overlaps
                        # snapped_start = self._snap_to_edges(t, new_clip, new_clip.start_time) # Optional snap
                        # Avoiding overlap on paste might be abrupt; standard DAW lets you paste on top sometimes.
                        # But our engine is monophonic per track-slot logically in the UI visuals often.
                        # Let's use the overlap avoidance to keep it clean.
                        actual_start = self._avoid_overlap_on_drag(t, new_clip, new_clip.start_time)
                        new_clip.start_time = actual_start
                        
                        self.sequencer.selected_clip = new_clip
                        self.update_inspector_ui()
                        self.log("Pasted Clip")

        # DOUBLE CLICK CHECK (Open Inspector)
        if dpg.is_mouse_button_double_clicked(dpg.mvMouseButton_Left):
             m_pos = dpg.get_mouse_pos(local=False)
             rx, ry = self.get_canvas_relative_pos(m_pos)
             d_clip = self.get_clip_at_pos(rx, ry)
             if d_clip:
                 self.sequencer.selected_clip = d_clip
                 # Open a dedicated Locked Inspector Tab on Double Click
                 self.create_floating_inspector(d_clip)

        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        
        # Cursor Logic
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        track_h = 80
        track_idx = int(rel_y // track_h)
        
        resize_clip_hover, _ = self._get_resize_hover(track_idx, rel_x)
        
        if dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
             
             # 1. CONTINUE RESIZING
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
                     
                 # Update UI while resizing
                 self.update_inspector_ui()

             # 2. CONTINUE DRAGGING
             elif self.sequencer.drag_clip:
                  # Calculate new time/track
                  new_px = rel_x - self.sequencer.drag_offset
                  new_t = max(0.0, new_px / self.sequencer.zoom_x)
                  
                  new_track_idx = int(rel_y // track_h)
                  
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
                  
                  self.update_inspector_ui()
                          
             # 3. INITIALIZE INTERACTION (Select / Init Drag / Init Resize / Seek / Scrub)
             elif dpg.is_item_hovered("timeline_canvas") or dpg.is_item_hovered("timeline_scroll") or self.sequencer.is_scrubbing:
                 
                 # Check if over a clip (Only if not already scrubbing)
                 hover_clip = None
                 if not self.sequencer.is_scrubbing:
                     hover_clip = self.get_clip_at_pos(rel_x, rel_y)
                 
                 if hover_clip and not self.sequencer.is_scrubbing:
                     # HIT CLIP -> Select & Init Drag/Resize
                     self.sequencer.selected_clip = hover_clip
                     self.update_inspector_ui()
                     
                     # Check Edges for Resize
                     clip_px_start = hover_clip.start_time * self.sequencer.zoom_x
                     clip_px_end = (hover_clip.start_time + hover_clip.duration) * self.sequencer.zoom_x
                     edge_threshold = 8.0
                     
                     if abs(rel_x - clip_px_start) < edge_threshold:
                         self.sequencer.resize_clip = hover_clip
                         self.sequencer.resize_edge = "left"
                         self.sequencer.resize_initial_dur = hover_clip.duration
                         self.sequencer.resize_initial_time = hover_clip.start_time
                     elif abs(rel_x - clip_px_end) < edge_threshold:
                         self.sequencer.resize_clip = hover_clip
                         self.sequencer.resize_edge = "right"
                         self.sequencer.resize_initial_dur = hover_clip.duration
                         self.sequencer.resize_initial_time = hover_clip.start_time
                     else:
                         # Init Drag
                         self.sequencer.drag_clip = hover_clip
                         self.sequencer.drag_offset = rel_x - clip_px_start
                         
                 else:
                     # HIT EMPTY or SCRUBBING -> Seek & Deselect
                     self.sequencer.is_scrubbing = True # Enable sticky scrubbing
                     
                     new_t = max(0.0, rel_x / self.sequencer.zoom_x)
                     if new_t < 0.05: new_t = 0.0 # Snap to zero (Main feature)
                     
                     self.sequencer.current_time = new_t
                     
                     if not self.sequencer.is_scrubbing: # Only clear on initial click
                        self.sequencer.selected_clip = None
                        self.update_inspector_ui()

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
            
            # force is -100 to 100
            current_nm = (force / 100.0) * max_torque
            dpg.set_value("txt_torque_val", f"{current_nm:.2f} Nm")
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
        
        # Read Global Gain
        global_gain = 1.0
        if dpg.does_item_exist("slider_gain"):
             global_gain = dpg.get_value("slider_gain") / 100.0

        # Iterate Tracks (not clips) to manage monophonic channel state
        for t_idx, track in enumerate(self.sequencer.tracks):
            if t_idx not in self.track_states:
                self.track_states[t_idx] = {'effect_id': -1, 'clip_id': None, 'clip_type': None}
            
            state = self.track_states[t_idx]
            prev_cid = state['clip_id']
            eff_id = state['effect_id']
            prev_ctype = state['clip_type']
            
            # Find current logical clip
            current_clip = self.sequencer.get_clip_at_precise(t_idx, cur_t)
            curr_cid = current_clip.id if current_clip else None
            
            # Helper to update active effect with new clip
            def start_new_effect(start_phase=-1):
                if not current_clip: return -1
                dur_ms = int(current_clip.duration * 1000)
                eff_mag = int(current_clip.magnitude * global_gain)
                
                new_id = -1
                if current_clip.type == "Sine":
                    # If start_phase is not overridden (i.e. -1), use the clip's defined start_phase
                    if start_phase == -1:
                        # Convert 0-360 deg to 0-36000
                        start_phase = getattr(current_clip, "start_phase", 0) * 100
                    
                    phase_arg = start_phase if start_phase >= 0 else 0
                    new_id = engine.start_effect_sine(current_clip.frequency, eff_mag, dur_ms, phase=phase_arg)
                elif current_clip.type == "Constant":
                    new_id = engine.start_effect_constant(eff_mag, dur_ms)
                elif current_clip.type == "Ramp":
                    new_id = engine.start_effect_ramp(0, eff_mag, dur_ms)
                elif current_clip.type == "Sawtooth":
                    per = int(1000 / max(1, current_clip.frequency))
                    new_id = engine.start_effect_sawtooth(eff_mag, per, dur_ms)
                return new_id

            # State Machine
            if curr_cid == prev_cid:
                # CONTINUATION (Same clip still playing)
                if current_clip and eff_id != -1:
                    # Update parameters if needed (Software Sweep)
                    remaining_ms = int((current_clip.duration - (cur_t - current_clip.start_time)) * 1000)
                    if remaining_ms < 0: remaining_ms = 0 # Safety
                    
                    if current_clip.type == "Sine":
                        # Always debug active clips
                        if self.sequencer.is_playing:
                            print(f"ClipActive: t={cur_t:.2f} id={eff_id} freq={current_clip.frequency} end={current_clip.frequency_end}")
                        
                        dpg.set_value("monitor_freq", f"Freq: {current_clip.frequency} Hz")
                        
                        # --- REAL-TIME UPDATE LOGIC ---
                        # Verify change against stored state or just update if sweep
                        is_sweep = current_clip.frequency != current_clip.frequency_end and current_clip.sweep_enabled
                        
                        # Check "Dirty" State (User changed sliders)
                        last_mag = state.get('last_mag', -1)
                        last_freq = state.get('last_freq', -1)
                        
                        has_changed = (current_clip.magnitude != last_mag) or \
                                      (current_clip.frequency != last_freq and not is_sweep) 
                        
                        if is_sweep or has_changed:
                             # Calculate current parameters
                             progress = (cur_t - current_clip.start_time) / current_clip.duration
                             progress = max(0.0, min(1.0, progress))
                             
                             if is_sweep:
                                 current_freq = float(current_clip.frequency + (current_clip.frequency_end - current_clip.frequency) * progress)
                                 current_freq = max(0.1, current_freq)
                             else:
                                 current_freq = float(current_clip.frequency)

                             dpg.set_value("monitor_freq", f"Freq: {current_freq:.2f} Hz")
                             
                             effect_len_ms = int(current_clip.duration * 1000) 

                             # Calculate Phase
                             t_local = cur_t - current_clip.start_time
                             start_f = current_clip.frequency
                             end_f = current_clip.frequency_end if is_sweep else start_f
                             k = (end_f - start_f) / max(1e-6, current_clip.duration)
                             
                             # Base Phase Integral: f0*t + 0.5*k*t^2
                             phase_integral = (start_f * t_local + 0.5 * k * t_local * t_local)
                             
                             # Add Start Phase Offset
                             start_deg = getattr(current_clip, "start_phase", 0)
                             norm_phase = (phase_integral + (start_deg / 360.0)) % 1.0
                             sdl_phase = int(norm_phase * 36000)
                             
                             # Update Engine
                             eff_mag = int(current_clip.magnitude * global_gain)
                             new_eff_id = engine.update_effect_sine(eff_id, current_freq, eff_mag, effect_len_ms, phase=sdl_phase)
                             if new_eff_id != -1:
                                 eff_id = new_eff_id
                                 state['effect_id'] = eff_id
                        
                        # Update State
                        state['last_mag'] = current_clip.magnitude
                        state['last_freq'] = current_clip.frequency

                # Update Phase Tracking for next frame's potential transition
                if current_clip and current_clip.type == "Sine":
                     t_local = cur_t - current_clip.start_time
                     start_f = current_clip.frequency
                     end_f = current_clip.frequency_end if current_clip.sweep_enabled else start_f
                     k = (end_f - start_f) / max(1e-6, current_clip.duration)
                     norm_phase = (start_f * t_local + 0.5 * k * t_local * t_local) % 1.0
                     state['last_phase'] = int(norm_phase * 36000)
                     
                elif current_clip and eff_id == -1:
                    # Recovery: Should be playing but isn't
                    eff_id = start_new_effect()
                    state['effect_id'] = eff_id
                    
            else:
                # TRANSITION (Clip Changed or Ended/Started)
                
                # Determine Start Phase for Gapless Continuity
                start_phase_override = -1
                if prev_cid is not None and current_clip and current_clip.type == "Sine" and prev_ctype == "Sine":
                     # Robust calculation: Find previous clip object and calculate its end phase
                     prev_clip = self.sequencer.get_clip_by_id(prev_cid)
                     if prev_clip:
                         start_f_prev = prev_clip.frequency
                         end_f_prev = prev_clip.frequency_end if prev_clip.sweep_enabled else start_f_prev
                         k_prev = (end_f_prev - start_f_prev) / max(1e-6, prev_clip.duration)
                         t_end = prev_clip.duration
                         # Calc exact end phase
                         end_phase_val = (start_f_prev * t_end + 0.5 * k_prev * t_end * t_end) % 1.0
                         start_phase_override = int(end_phase_val * 36000)
                     elif 'last_phase' in state:
                         start_phase_override = state['last_phase']
                
                # Try Transfer (Reuse Effect)
                transferred = False
                if eff_id != -1 and current_clip and prev_ctype == current_clip.type == "Sine":
                    # Reuse the sine effect via update
                    dur_ms = int(current_clip.duration * 1000)
                    eff_mag = int(current_clip.magnitude * global_gain)
                    
                    # Calculate Phase
                    start_deg = getattr(current_clip, "start_phase", 0)
                    phase_to_use = start_deg * 100 # Default user setting
                    
                    if start_phase_override != -1:
                         # Gapless Override takes precedence if we want continuity
                         # But wait, user wanted CONTROL.
                         # If user set a specific Phase, maybe we should use it?
                         # "I need you ... to set phase so it starts ... at same phase ... currently playing"
                         # This implies AUTO-CONTINUITY for SWEEP, but what about CLIP-TO-CLIP?
                         # "One ends at 0... Next starts at 0... I can choose the starting point"
                         # Implies manual setup. 
                         # So if start_phase is 0 (default), maybe use override?
                         # If start_phase is non-zero, use it?
                         if start_deg == 0:
                              phase_to_use = start_phase_override
                         else:
                              phase_to_use = start_deg * 100
                    
                    new_id = engine.update_effect_sine(eff_id, current_clip.frequency, eff_mag, dur_ms, phase=phase_to_use)
                    if new_id != -1:
                        transferred = True
                        eff_id = new_id
                
                if not transferred:
                    # Stop Old
                    if eff_id != -1:
                        engine.stop_effect(eff_id)
                        eff_id = -1
                    
                    # Start New
                    if current_clip:
                        eff_id = start_new_effect(start_phase=start_phase_override)
                
                # Save State
                state['effect_id'] = eff_id
                state['clip_id'] = curr_cid
                state['clip_type'] = current_clip.type if current_clip else None
                if current_clip:
                     state['last_phase'] = start_phase_override if start_phase_override != -1 else 0


    # --- Rendering ---
    def render_grid(self, total_w, total_h):
        """Draws vertical grid lines and time labels based on current zoom."""
        # Calculate grid spacing (time)
        # We want approx 10 divisions per screen or readable intervals
        # Let's try 1s, 0.1s, 0.01s based on zoom
        
        # Determine strict power of 10 spacing
        # If pixels_per_sec (zoom_x) is 50:
        # 1s = 50px (Too small? No)
        # If zoom is 500: 1s = 500px, 0.1s = 50px
        
        # Base spacing in pixels we want is roughly 100px
        target_px = 100.0
        # Ideal time step
        ideal_dt = target_px / max(0.1, self.sequencer.zoom_x)
        
        # Snap to 1, 2, 5
        power = math.floor(math.log10(ideal_dt))
        base = 10 ** power
        
        # Candidates: 1*base, 2*base, 5*base, 10*base
        candidates = [base, 2*base, 5*base, 10*base]
        # Pick closest
        grid_time = base
        min_dist = 999999.0
        for c in candidates:
             dist = abs(c - ideal_dt)
             if dist < min_dist:
                 min_dist = dist
                 grid_time = c
        
        # Update Time Base Display input
        if dpg.does_item_exist("input_timebase"):
             # Avoid infinite loop if user is typing
             if not dpg.is_item_active("input_timebase"):
                 dpg.set_value("input_timebase", grid_time)
        
        # Draw Lines
        start_t = 0.0
        # For optimization, we could start from scroll position, but we don't track scroll X easily yet.
        # Just draw visible range? We blindly draw 3000px width for now.
        
        x = 0
        t = 0.0
        while x < total_w:
            x = t * self.sequencer.zoom_x
            if x > total_w: break
            
            color = (60, 60, 60, 100)
            thickness = 1
            
            # Major lines every 10 steps?
            # if i % 10 == 0: color = (80,80,80,150)
            
            dpg.draw_line([x, 0], [x, total_h], color=color, thickness=thickness, parent="timeline_canvas")
            # dpg.draw_text([x + 2, 0], f"{t:.2g}s", size=10, color=(150, 150, 150), parent="timeline_canvas") # User requested removal
            
            t += grid_time

    def render_timeline(self):
        dpg.delete_item("timeline_canvas", children_only=True, slot=2)
        
        y_offset = 0
        track_height = 80
        total_w = max(3000, int(self.sequencer.zoom_x * 60)) # Dynamic width
        # Ensure canvas is large enough
        total_h = len(self.sequencer.tracks) * track_height
        dpg.configure_item("timeline_canvas", width=total_w, height=total_h) 
        # Setting height ensures that if we zoom out, canvas shrinks, scrollbar disappears.
        
        # Render Grid Background
        self.render_grid(total_w, total_h)
        
        for i, track in enumerate(self.sequencer.tracks):
             # Highlight if target
             is_target = (i == self.drag_target_track_idx) 
             # Simplify: Just check if valid index and dragging/dropping might happen.
             # Actually, just always highlight current hover track slightly? 
             # User said "highlight only the target track (not whole window)" during drag and drop.
             
             bg_col = (40, 40, 45, 50) if i % 2 == 0 else (35, 35, 40, 50)
             if i == self.drag_target_track_idx:
                 bg_col = (60, 60, 80, 100) # Highlight

             # Draw track lane bg (transparent to show grid potentially, or grid on top?)
             # Grid is drawn first, so BG must be transparent.
             dpg.draw_rectangle([0, y_offset], [total_w, y_offset + track_height], color=bg_col, fill=bg_col, parent="timeline_canvas")
             dpg.draw_line([0, y_offset + track_height], [total_w, y_offset+track_height], color=(60, 60, 60), parent="timeline_canvas")
             dpg.draw_text([10, y_offset + 5], track.name, size=15, color=(200, 200, 200), parent="timeline_canvas")
             
             for clip in track.clips:
                 x_start = clip.start_time * self.sequencer.zoom_x
                 width = clip.duration * self.sequencer.zoom_x
                 
                 base_col = (100, 150, 255) if clip.type == "Sine" else (255, 100, 100)
                 if clip.type == "Constant": base_col = (100, 255, 100)
                 if clip.type == "Ramp": base_col = (255, 255, 100)
                 if clip.type == "Sawtooth": base_col = (255, 150, 50)

                 border_col = (255, 255, 255) if clip == self.sequencer.selected_clip else base_col
                 
                 dpg.draw_rectangle([x_start, y_offset + 20], [x_start + width, y_offset + track_height - 5], color=border_col, thickness=2, fill=(base_col[0], base_col[1], base_col[2], 150), parent="timeline_canvas")
                 dpg.draw_text([x_start + 5, y_offset + 25], clip.name, size=13, parent="timeline_canvas")

                 # Waveform preview
                 wave_points = self._clip_wave_points(
                     clip,
                     x_start + 4,
                     max(4.0, width - 8),
                     y_offset + 28,
                     y_offset + track_height - 12,
                     samples= max(20, int(width / 6))
                 )
                 dpg.draw_polyline(wave_points, color=(240, 240, 240, 220), thickness=2, parent="timeline_canvas")

             y_offset += track_height
        
        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y_offset], color=(255, 50, 50), thickness=2, parent="timeline_canvas")

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
            self.sequencer.delete_clip(self.sequencer.selected_clip)
            self.sequencer.selected_clip = None
            self.update_inspector_ui()

    def on_drop_receive(self, sender, app_data):
        print(f"DEBUG DROP: Sender={sender}, Data={app_data}")
        self.handle_drop(app_data, sender) # Pass sender to debug

    def handle_drop(self, effect_type, sender="Unknown"):
        if not isinstance(effect_type, str): return
        
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        
        track_h = 80
        track_idx = int(rel_y // track_h)
        
        self.log(f"Drop [{sender}]: {effect_type} at {int(rel_x)},{int(rel_y)} -> Tk {track_idx+1}")
        
        if 0 <= track_idx < len(self.sequencer.tracks):
             time_s = max(0.0, rel_x / self.sequencer.zoom_x)
             clip = self.sequencer.add_clip(track_idx, effect_type, time_s)
             self.sequencer.selected_clip = clip
             self.log(f"Created Clip: {time_s:.2f}s")
        else:
             self.log(f"Drop Skipped: Invalid Track {track_idx}")

    def canvas_click(self, sender, app_data):
        mouse_btn = app_data[0] # [0]=button, [1]=tag
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        
        track_h = 80
        track_idx = int(rel_y // track_h)
        time_s = rel_x / self.sequencer.zoom_x
        
        if mouse_btn == 1:
            # RIGHT CLICK: Context Menu
            # Check if clicked on a clip
            clip = self.get_clip_at_pos(rel_x, rel_y)
            
            if clip:
                # CLIP CONTEXT MENU
                if dpg.does_item_exist("win_clip_opts"): dpg.delete_item("win_clip_opts")
                self.sequencer.selected_clip = clip # Auto-select on right click
                self.renaming_clip = clip
                
                with dpg.window(tag="win_clip_opts", label="Clip Options", width=250, height=120, modal=True, show=True, pos=mpos):
                     dpg.add_text(f"Clip: {clip.type}")
                     dpg.add_input_text(tag="input_clip_name", default_value=clip.name, on_enter=True, callback=lambda: do_rename_clip(None, None, None))

                     def do_rename_clip(s, a, u):
                         name = dpg.get_value("input_clip_name")
                         if self.renaming_clip:
                             self.renaming_clip.name = name
                             self.update_inspector_ui()
                         dpg.delete_item("win_clip_opts")

                     def do_del_clip(s, a, u):
                         if self.renaming_clip:
                             self.sequencer.delete_clip(self.renaming_clip)
                             self.sequencer.selected_clip = None
                             self.update_inspector_ui()
                         dpg.delete_item("win_clip_opts")

                     with dpg.group(horizontal=True):
                         dpg.add_button(label="Rename", callback=do_rename_clip)
                         dpg.add_button(label="Delete", callback=do_del_clip)
                         dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("win_clip_opts"))
            
            elif 0 <= track_idx < len(self.sequencer.tracks):
                # TRACK CONTEXT MENU
                self.renaming_track_idx = track_idx
                if dpg.does_item_exist("win_track_opts"): dpg.delete_item("win_track_opts")
                
                with dpg.window(tag="win_track_opts", label=f"Track {track_idx+1} Options", width=300, height=120, modal=True, show=True, pos=mpos):
                    dpg.add_input_text(tag="input_rename", default_value=self.sequencer.tracks[track_idx].name)
                    def do_rename(s, a, u):
                        name = dpg.get_value("input_rename")
                        if 0 <= self.renaming_track_idx < len(self.sequencer.tracks):
                            self.sequencer.tracks[self.renaming_track_idx].name = name
                        dpg.delete_item("win_track_opts")
                    def do_delete(s, a, u):
                        if 0 <= self.renaming_track_idx < len(self.sequencer.tracks):
                            del self.sequencer.tracks[self.renaming_track_idx]
                            self.sequencer.selected_clip = None 
                        dpg.delete_item("win_track_opts")
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Rename", callback=do_rename)
                        dpg.add_button(label="Delete Track", callback=do_delete)
                        dpg.add_button(label="Cancel", callback=lambda: dpg.delete_item("win_track_opts"))
            return



    def update_inspector_ui(self):
        # Update all active inspector panels
        active = []
        for p in self.inspectors:
            if dpg.does_item_exist(p.tag_tab):
                p.update()
                active.append(p)
        self.inspectors = active

    def create_floating_inspector(self, clip=None, parent=None):
        # 1. Check if window already exists for this clip (only for floating windows)
        if clip and not parent:
            for p in self.inspectors:
                if p.clip == clip and p.is_window and dpg.does_item_exist(p.tag_tab):
                    dpg.configure_item(p.tag_tab, show=True)
                    dpg.focus_item(p.tag_tab)
                    # Bring to front? Focus usually does it.
                    return

        # 2. Cleanup closed inspectors
        self.inspectors = [p for p in self.inspectors if dpg.does_item_exist(p.tag_tab)]

        # 3. Create New
        pos = [400, 200]
        if not parent:
            # Cascade logic
            offset = len(self.inspectors) * 30
            pos = [400 + offset, 200 + offset]
            
        p = InspectorPanel(self, parent, clip, pos=pos)
        self.inspectors.append(p)
        if parent:
             dpg.set_value(parent, p.tag_tab)

    # --- Palette Drag Source ---
    def make_drag_source(self, label, type):
        with dpg.group():
             dpg.add_button(label=label, width=100)
             with dpg.drag_payload(drag_data=type): # Removed payload_type for compatibility
                 dpg.add_text(f"Effect: {label}")


    def on_mouse_wheel(self, sender, app_data):
        # app_data is value
        # Lock view to tracks: Only zoom if hovering timeline
        if dpg.is_item_hovered("timeline_canvas") or dpg.is_item_hovered("timeline_scroll"):
            # Get Mouse Time
            mouse_x_screen = dpg.get_mouse_pos(local=False)[0]
            # Offset from scroll window
            try:
                win_pos = dpg.get_item_pos("timeline_scroll") 
                # Note: get_item_pos returns [x,y]. Mouse is global.
                # Scoll offset x
                scroll_x = dpg.get_x_scroll("timeline_scroll")
                
                # Mouse X relative to content 0
                # mouse_in_content = (mouse_x - win_x) + scroll_x
                # Simplified: timeline_canvas starts at 0,0 of child window content.
                # Warning: win_pos might be unstable or title bar offset.
                # Let's use get_item_rect_min of the scroll window
                r_min = dpg.get_item_rect_min("timeline_scroll")
                mouse_rel_x = mouse_x_screen - r_min[0] + scroll_x
            except:
                mouse_rel_x = 0
            
            # Calculate Time
            t_mouse = max(0.0, mouse_rel_x / self.sequencer.zoom_x)
            
            # Zoom
            scale_factor = 1.15
            if app_data > 0:
                self.sequencer.zoom_x *= scale_factor
            elif app_data < 0:
                self.sequencer.zoom_x /= scale_factor
            
            # Clamp settings
            self.sequencer.zoom_x = max(10.0, min(50000.0, self.sequencer.zoom_x))
            
            # Restore Scroll to keep t_mouse at mouse_rel_x location on screen
            # new_mouse_rel_x (in pixels) = t_mouse * new_zoom
            # new_scroll_x = new_mouse_rel_x - (mouse_x_screen - r_min[0])
            try:
                new_pixel_x = t_mouse * self.sequencer.zoom_x
                screen_offset = mouse_x_screen - r_min[0]
                new_scroll_x = max(0.0, new_pixel_x - screen_offset)
                dpg.set_x_scroll("timeline_scroll", new_scroll_x)
            except: pass
            
            # Prevent Vertical Scroll Drift?
            # If content fits, ensure Y scroll is 0?
            total_h = len(self.sequencer.tracks) * 80
            win_h = dpg.get_item_height("timeline_scroll")
            if total_h <= win_h:
                dpg.set_y_scroll("timeline_scroll", 0)

    def on_timebase_change(self, sender, app_data):
        # User manually entered a timebase (e.g. 0.1)
        # We need to set zoom_x such that grid lines appear at this interval
        # Base target px ~ 100px?
        if app_data <= 0.00001: return
        target_px = 100.0
        self.sequencer.zoom_x = target_px / app_data
        

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


    def setup_ui(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (20, 20, 25))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_theme(global_theme)
        
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
            dpg.add_key_press_handler(dpg.mvKey_S, callback=lambda: dpg.show_item("dlg_save") if dpg.is_key_down(dpg.mvKey_Control) else None)
            dpg.add_key_press_handler(dpg.mvKey_O, callback=lambda: dpg.show_item("dlg_load") if dpg.is_key_down(dpg.mvKey_Control) else None)

        with dpg.window(tag="Main"):
            # Menu Bar
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Save Project", shortcut="(Ctrl+S)", callback=lambda: dpg.show_item("dlg_save"))
                    dpg.add_menu_item(label="Open Project", shortcut="(Ctrl+O)", callback=lambda: dpg.show_item("dlg_load"))
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=lambda: dpg.stop_dearpygui())
                with dpg.menu(label="View"):
                     dpg.add_menu_item(label="Torque Monitor", callback=lambda: dpg.show_item("win_torque_monitor"))
                
                # --- Device Controls in Menu Bar ---
                dpg.add_spacer(width=20)
                dpg.add_combo(tag="device_combo", width=200)
                dpg.add_button(label="Scan", callback=self.scan_devices)
                dpg.add_button(label="Connect", callback=self.connect_callback)
                dpg.add_text("Status: Disconnected", tag="status_text", color=(255, 100, 100))

            # Torque Monitor Window (Initially Hidden or Shown)
            with dpg.window(tag="win_torque_monitor", label="Torque Monitor", width=250, height=200, pos=[400, 100], show=False):
                 dpg.add_text("Real-time Torque", color=(150, 255, 150))
                 dpg.add_text("0.00 Nm", tag="txt_torque_val") # Standard size for now to avoid crash
                 # We'll stick to standard text for now, maybe add a progress bar.
                 dpg.add_progress_bar(tag="bar_torque", width=-1, height=20)
                 
                 dpg.add_spacer(height=5)
                 with dpg.group(horizontal=True):
                     dpg.add_text("Peak: --", tag="txt_peak", color=(255, 100, 100))
                     dpg.add_spacer(width=10)
                     dpg.add_text("Avg: --", tag="txt_avg", color=(100, 200, 255))
                     dpg.add_spacer(width=10)
                     dpg.add_text("Min: --", tag="txt_min", color=(200, 200, 200))

                 dpg.add_separator()
                 dpg.add_text("Settings:")

                 with dpg.group(horizontal=True):
                     dpg.add_text("Base Torque (Ref):")
                     dpg.add_input_float(tag="input_max_torque", default_value=8.0, width=100, step=0.5)
                     
                 with dpg.group(horizontal=True):
                     dpg.add_text("Master Gain (%):  ")
                     dpg.add_slider_int(tag="slider_gain", default_value=100, min_value=0, max_value=100, width=100)

            
            # Set Main as a fallback drop target without payload type check
            # try: dpg.set_item_drop_callback("Main", self.on_drop_receive)
            # except: pass
            
            # Global Handlers
            with dpg.handler_registry():
                dpg.add_mouse_wheel_handler(callback=self.on_mouse_wheel)
                # dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self.on_key_press)
                # dpg.add_key_press_handler(dpg.mvKey_Back, callback=self.on_key_press)

            # Top Bar
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=10)
                # Device controls moved to menu bar
                # dpg.add_text("Fedit DAW", color=(100, 200, 255)) # Removed per request
                # dpg.add_text("| Rate: -- Hz", tag="status_fps", color=(150, 255, 150)) # REMOVED per user request
                
                dpg.add_spacer(width=20)
                # Force Monitor
                # Using a theme for the slider to look like a gauge/bar
                dpg.add_text("Force:")
                dpg.add_slider_float(tag="force_gauge", width=150, min_value=-100, max_value=100, format="%.0f%%")

                dpg.add_spacer(width=50)
                dpg.add_button(tag="btn_play", label="Play", width=80, callback=self.toggle_play)
                dpg.add_button(label="|<", tag="btn_restart", width=30, callback=self.action_restart)
                dpg.add_checkbox(label="Loop", tag="chk_loop")
                
                dpg.add_text("0.00s", tag="time_display")
                
                dpg.add_spacer(width=20)
                dpg.add_text("Freq: --", tag="monitor_freq", color=(100, 255, 100))
                
                dpg.add_spacer(width=20)
                dpg.add_text("T:")
                dpg.add_input_float(tag="input_timebase", width=60, default_value=0.1, step=0, callback=self.on_timebase_change)

            dpg.add_separator()

        

        # --- SOLID TABLE LAYOUT ---
        with dpg.table(header_row=False, resizable=True, policy=dpg.mvTable_SizingStretchProp, 
                       borders_innerV=True, parent="Main"):
            dpg.add_table_column(width_fixed=True, init_width_or_weight=150) # Palette
            dpg.add_table_column() # Timeline
            dpg.add_table_column(width_fixed=True, init_width_or_weight=300) # Inspector & Log

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
                    dpg.add_button(label="+ Add Track", width=100, callback=lambda: self.sequencer.tracks.append(Track(name="New Track")))

                # Col 2: Timeline
                with dpg.group(tag="timeline_group"):
                     # Scroll Window
                     with dpg.child_window(tag="timeline_scroll", horizontal_scrollbar=True, no_scroll_with_mouse=True):
                             with dpg.drawlist(width=3000, height=1000, tag="timeline_canvas"):
                                pass

                     dpg.add_text("Drop Effects Here", parent="timeline_scroll", color=(100,100,100))
                     
                     with dpg.item_handler_registry(tag="timeline_click_handler"):
                             dpg.add_item_clicked_handler(callback=self.canvas_click)
                         
                     dpg.bind_item_handler_registry("timeline_canvas", "timeline_click_handler")
                         
                     try:
                         dpg.set_item_drop_callback("timeline_scroll", self.on_drop_receive)
                     except Exception as e: print(f"Init Warning: {e}")

                # Col 3: Inspector (Top) & Log (Bottom)
                with dpg.group():
                    # Inspector Section (Top Half)
                    with dpg.child_window(tag="panel_inspector", height=450):
                        # We use a Tab Bar here to host the single Live Inspector tab
                        # This keeps the look consistent and clean
                        with dpg.tab_bar(tag="inspector_tab_bar"):
                            pass
                    
                    # Log Section (Bottom Half)
                    with dpg.child_window(tag="panel_log", height=-1):
                        dpg.add_text("System Log")
                        dpg.add_listbox(tag="log_list", num_items=30, width=-1)

        # Initialize Live Inspector (Docked in inspector_tab_bar)
        self.create_floating_inspector(None, parent="inspector_tab_bar")
        
        # FINAL BINDING
        try:
            dpg.set_item_drop_callback("Main", self.on_drop_receive)
        except Exception: pass

    def run(self):
        # Disable VSync for Haptics
        dpg.create_viewport(title='Fedit DAW 2.0', width=1280, height=800, vsync=False)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("Main", True)
        
        self.scan_devices()

        while dpg.is_dearpygui_running():
            try:
                self.update_loop()
            except Exception as e:
                print(f"Update Loop Crash: {e}")
                self.log(f"CRITICAL: {e}")
                # Optional: pause playback on crash to prevent loop
                self.sequencer.is_playing = False
                
            dpg.render_dearpygui_frame()
            
        dpg.destroy_context()

if __name__ == "__main__":
    app = FeditNativeApp()
    app.run()
