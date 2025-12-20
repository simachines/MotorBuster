import dearpygui.dearpygui as dpg
import sys
import os
import time
from collections import deque
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
    type: str = "Sine"
    start_time: float = 0.0
    duration: float = 2.0
    track_index: int = 0
    magnitude: int = 3276
    frequency: int = 10
    frequency_end: int = 10
    start_phase: int = 0
    sweep_enabled: bool = False
    direction_mode: str = "Polar"
    angle: int = 90
    radius: int = 1
    x: int = 1
    y: int = 0
    z: int = 0
    yaw: int = 0
    pitch: int = 0
    distance: int = 1
    start_mag: int = -10000
    end_mag: int = 10000
    attack_length: int = 0
    fade_length: int = 0
    name: str = "Clip"
    active_effect_id: int = -1

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "start_time": self.start_time,
            "duration": self.duration,
            "track_index": self.track_index,
            "magnitude": self.magnitude,
            "frequency": self.frequency,
            "frequency_end": self.frequency_end,
            "sweep_enabled": self.sweep_enabled,
            "start_phase": self.start_phase,
            "direction_mode": self.direction_mode,
            "angle": self.angle,
            "radius": self.radius,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "distance": self.distance,
            "start_mag": self.start_mag,
            "end_mag": self.end_mag,
            "attack_length": self.attack_length,
            "fade_length": self.fade_length,
            "name": self.name,
        }

    @staticmethod
    def from_dict(d):
        freq = d.get("frequency", 10)
        raw_angle = d.get("angle", 90)
        # Backward compatibility: older projects stored hundredths of a degree
        if raw_angle > 359:
            raw_angle = int(round(raw_angle / 100.0))
        raw_angle = max(0, min(359, raw_angle))

        return Clip(
            id=d.get("id", str(uuid.uuid4())),
            type=d.get("type", "Sine"),
            start_time=d.get("start_time", 0.0),
            duration=d.get("duration", 1.0),
            track_index=d.get("track_index", 0),
            magnitude=d.get("magnitude", 3276),
            frequency=freq,
            frequency_end=d.get("frequency_end", freq),
            start_phase=d.get("start_phase", 0),
            sweep_enabled=d.get("sweep_enabled", False),
            direction_mode=d.get("direction_mode", "Polar"),
            angle=raw_angle,
            radius=d.get("radius", 1),
            x=d.get("x", 1),
            y=d.get("y", 0),
            z=d.get("z", 0),
            yaw=d.get("yaw", 0),
            pitch=d.get("pitch", 0),
            distance=d.get("distance", 1),
            start_mag=d.get("start_mag", -10000),
            end_mag=d.get("end_mag", 10000),
            attack_length=d.get("attack_length", 0),
            fade_length=d.get("fade_length", 0),
            name=d.get("name", "Clip"),
        )
 
@dataclass
class Track:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Track"
    gain: int = 100
    clips: list[Clip] = field(default_factory=list)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "gain": self.gain,
            "clips": [c.to_dict() for c in self.clips],
        }

    @staticmethod
    def from_dict(d):
        track = Track(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Track"),
            gain=d.get("gain", 100),
        )
        track.clips = [Clip.from_dict(cd) for cd in d.get("clips", [])]
        return track


class FeditSequencer:
    def __init__(self):
        self.tracks = [Track(name=f"{i+1}") for i in range(4)]
        self.is_playing = False
        self.current_time = 0.0
        self.last_tick = 0.0
        self.zoom_x = 50.0
        self.selected_clip: Clip = None
        self.drag_clip: Clip = None
        self.drag_time_offset: float = 0.0
        self.resize_clip: Clip = None
        self.resize_edge: str = None
        self.resize_initial_time: float = 0.0
        self.resize_initial_dur: float = 0.0
        self.clipboard: dict = None

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
        for clip in self.tracks[track_idx].clips:
            if clip.start_time <= time_s <= clip.start_time + clip.duration:
                return clip
        return None

    def get_clip_at_precise(self, track_idx, time_s):
        for clip in self.tracks[track_idx].clips:
            if clip.start_time <= time_s < clip.start_time + clip.duration:
                return clip
        return None

    def get_clip_by_id(self, clip_id):
        if clip_id is None:
            return None
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
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
        self.tag_type = f"insp_type_{self.id}"
        self.tag_dir_mode = f"insp_dir_mode_{self.id}"
        self.tag_angle = f"insp_angle_{self.id}"
        self.tag_radius = f"insp_radius_{self.id}"
        self.tag_x = f"insp_x_{self.id}"
        self.tag_y = f"insp_y_{self.id}"
        self.tag_z = f"insp_z_{self.id}"
        self.tag_yaw = f"insp_yaw_{self.id}"
        self.tag_pitch = f"insp_pitch_{self.id}"
        self.tag_distance = f"insp_distance_{self.id}"
        self.tag_start_mag = f"insp_startmag_{self.id}"
        self.tag_end_mag = f"insp_endmag_{self.id}"
        self.tag_attack = f"insp_attack_{self.id}"
        self.tag_fade = f"insp_fade_{self.id}"
        
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
             dpg.add_combo(label="Effect Type", items=["Sine","Square","Triangle","SawtoothUp","SawtoothDown","Constant","Ramp","Spring","Damper","Inertia","Friction","LeftRight"], tag=self.tag_type, callback=self.on_change, user_data="type")
             dpg.add_input_float(label="Start (s)", tag=self.tag_start, callback=self.on_change, user_data="start")
             dpg.add_input_float(label="Duration (s)", tag=self.tag_dur, callback=self.on_change, user_data="dur")
             dpg.add_slider_int(label="Magnitude %", tag=self.tag_mag, max_value=100, callback=self.on_change, user_data="mag")
             
             dpg.add_separator()
             dpg.add_input_int(label="Frequency (Hz)", tag=self.tag_freq, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq")
             dpg.add_checkbox(label="Enable Sweep", tag=self.tag_sweep, callback=self.on_change, user_data="sweep")
             dpg.add_input_int(label="End Freq (Hz)", tag=self.tag_freq_end, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq_end")
             dpg.add_slider_int(label="Phase (deg)", tag=self.tag_phase, min_value=0, max_value=359, callback=self.on_change, user_data="phase")

             dpg.add_separator()
             dpg.add_combo(label="Direction Mode", items=["Polar","Cartesian","Spherical"], tag=self.tag_dir_mode, callback=self.on_change, user_data="dir_mode")
             with dpg.group(tag=f"grp_polar_{self.id}"):
                 dpg.add_slider_int(label="Direction (deg)", tag=self.tag_angle, min_value=0, max_value=359, callback=self.on_change, user_data="angle")
                 dpg.add_input_int(label="Radius", tag=self.tag_radius, callback=self.on_change, user_data="radius")
             with dpg.group(tag=f"grp_cart_{self.id}"):
                 dpg.add_input_int(label="X", tag=self.tag_x, callback=self.on_change, user_data="x")
                 dpg.add_input_int(label="Y", tag=self.tag_y, callback=self.on_change, user_data="y")
                 dpg.add_input_int(label="Z", tag=self.tag_z, callback=self.on_change, user_data="z")
             with dpg.group(tag=f"grp_spherical_{self.id}"):
                 dpg.add_input_int(label="Yaw", tag=self.tag_yaw, callback=self.on_change, user_data="yaw")
                 dpg.add_input_int(label="Pitch", tag=self.tag_pitch, callback=self.on_change, user_data="pitch")
                 dpg.add_input_int(label="Distance", tag=self.tag_distance, callback=self.on_change, user_data="distance")

             dpg.add_separator()
             with dpg.group(tag=f"grp_ramp_{self.id}"):
                 dpg.add_input_int(label="Start Magnitude", tag=self.tag_start_mag, callback=self.on_change, user_data="start_mag")
                 dpg.add_input_int(label="End Magnitude", tag=self.tag_end_mag, callback=self.on_change, user_data="end_mag")

             dpg.add_separator()
             dpg.add_input_int(label="Attack (ms)", tag=self.tag_attack, callback=self.on_change, user_data="attack")
             dpg.add_input_int(label="Fade (ms)", tag=self.tag_fade, callback=self.on_change, user_data="fade")
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
        safe_set(self.tag_phase, int(max(0, min(359, getattr(clip, 'start_phase', 0)))))
        safe_set(self.tag_type, clip.type)
        safe_set(self.tag_dir_mode, clip.direction_mode)
        safe_set(self.tag_angle, int(max(0, min(359, getattr(clip, 'angle', 90)))))
        safe_set(self.tag_radius, getattr(clip, 'radius', 1))
        safe_set(self.tag_x, getattr(clip, 'x', 1))
        safe_set(self.tag_y, getattr(clip, 'y', 0))
        safe_set(self.tag_z, getattr(clip, 'z', 0))
        safe_set(self.tag_yaw, getattr(clip, 'yaw', 0))
        safe_set(self.tag_pitch, getattr(clip, 'pitch', 0))
        safe_set(self.tag_distance, getattr(clip, 'distance', 1))
        safe_set(self.tag_start_mag, getattr(clip, 'start_mag', -10000))
        safe_set(self.tag_end_mag, getattr(clip, 'end_mag', 10000))
        safe_set(self.tag_attack, getattr(clip, 'attack_length', 0))
        safe_set(self.tag_fade, getattr(clip, 'fade_length', 0))
        
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

        # Direction group visibility
        mode = clip.direction_mode or "Polar"
        dpg.configure_item(f"grp_polar_{self.id}", show=(mode == "Polar"))
        dpg.configure_item(f"grp_cart_{self.id}", show=(mode == "Cartesian"))
        dpg.configure_item(f"grp_spherical_{self.id}", show=(mode == "Spherical"))

        # Ramp-only fields
        dpg.configure_item(f"grp_ramp_{self.id}", show=(clip.type == "Ramp"))

    def on_change(self, sender, app_data, user_data):
        clip = self.get_target_clip()
        if not clip: return
        
        param = user_data
        param = user_data
        if param == "start": clip.start_time = max(0.0, app_data)
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
        elif param == "phase": clip.start_phase = max(0, min(359, app_data))
        elif param == "freq_end": 
             if app_data < 1:
                 dpg.set_value(sender, 1)
                 clip.frequency_end = 1
             else:
                 clip.frequency_end = app_data
        elif param == "sweep": 
            clip.sweep_enabled = app_data
            self.update() # Refresh visibility immediately
        elif param == "type":
            clip.type = app_data
            self.update()
        elif param == "dir_mode":
            clip.direction_mode = app_data
            self.update()
        elif param == "angle": clip.angle = max(0, min(359, app_data))
        elif param == "radius": clip.radius = max(0, app_data)
        elif param == "x": clip.x = app_data
        elif param == "y": clip.y = app_data
        elif param == "z": clip.z = app_data
        elif param == "yaw": clip.yaw = max(0, min(360, app_data))
        elif param == "pitch": clip.pitch = max(0, min(360, app_data))
        elif param == "distance": clip.distance = max(0, app_data)
        elif param == "start_mag": clip.start_mag = app_data
        elif param == "end_mag": clip.end_mag = app_data
        elif param == "attack": clip.attack_length = max(0, app_data)
        elif param == "fade": clip.fade_length = max(0, app_data)
            
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
        self.api_log_items = []
        self.resize_threshold_px = 12.0
        self._console_opened = False
        self.sweep_markers = []  # [{"time": float, "phase": int}]
        self.wheel_graph_height = 150
        self.wheel_graph_gain = 1.0
        self.wheel_history = deque()
        self.wheel_history_limit = None
        self.wheel_axis_scale = 32767.0
        self.hide_playhead_until_next_sample = False
        self.manual_timebase_override = False
        self.manual_timebase_value = None
        self.mouse_left_button_down = False
        self._blocking_hover_tags = ("panel_inspector", "panel_log")
        self._hover_configs = {
            "panel_inspector": {
                "default": {"bg": (22, 22, 28), "border": (58, 62, 76)},
                "hover": {"bg": (51, 67, 92), "border": (120, 180, 255)},
            },
            "panel_log": {
                "default": {"bg": (22, 22, 28), "border": (58, 62, 76)},
                "hover": {"bg": (51, 67, 92), "border": (120, 180, 255)},
            },
            "timeline_scroll": {
                "default": {"bg": (16, 16, 20), "border": (40, 44, 56)},
                "hover": {"bg": (255, 249, 184), "border": (225, 190, 0)},
            },
            "Main": {
                "default": {"bg": (18, 18, 24), "border": (48, 56, 70)},
                "hover": {"bg": (44, 75, 135), "border": (120, 180, 255)},
            },
        }
        self._mouse_status_tag = "txt_mouse_status"
        self._mouse_status_visible = True
       
        dpg.create_context()
        self.setup_ui()
        
        # Init Engine
        try:
            self.log("Initializing Haptic Subsystem...")
            engine.init_sdl()
        except Exception as e:
            self.log(f"SDL Init Error: {e}")

        self._ensure_api_console()
            
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

        pixels = max(1, int(width))
        max_samples = 5000
        sample_count = min(pixels, max_samples)

        duration = max(clip.duration, 1e-6)
        step_t = duration / max(1, sample_count)
        step_x = width / max(1, sample_count)

        freq = max(clip.frequency, getattr(clip, 'frequency_end', clip.frequency))
        is_aliasing = False
        if freq > 0 and (1.0/freq) < step_t * 2.5:
             is_aliasing = True

        for i in range(sample_count):
            t0 = i * step_t
            t1 = (i + 1) * step_t
            
            local_min = 0.0
            local_max = 0.0

            if is_aliasing:
                local_min = -1.0 * clip_scale
                local_max = 1.0 * clip_scale
            else:
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
            
            x = x_start + i * step_x
            points.append([x, y_l])
            points.append([x, y_h])
            
        return points

    def log(self, message):
        self.log_items.append(message)
        if len(self.log_items) > 50: self.log_items.pop(0)
        if dpg.does_item_exist("log_list"):
            dpg.configure_item("log_list", items=self.log_items)

    def log_api(self, action, payload):
        """Record haptic API interactions; mirror only to API console/buffer (not main UI log)."""
        preview = payload
        try:
            preview = json.dumps(payload) if not isinstance(payload, str) else payload
        except Exception:
            preview = str(payload)
        entry = f"API {action}: {preview}"
        print(entry)

        # Keep dedicated API buffer (in case we re-add a panel later)
        self.api_log_items.append(entry[:500])
        if len(self.api_log_items) > 200:
            self.api_log_items.pop(0)

    def _ensure_api_console(self):
        """Open a dedicated console for API logs (Windows only)."""
        if self._console_opened:
            return
        try:
            ctypes.windll.kernel32.AllocConsole()
            sys.stdout = open("CONOUT$", "w")
            sys.stderr = sys.stdout
            self._console_opened = True
            print("API console initialized")
        except Exception as e:
            # Fallback: keep stdout as-is; API logs still appear in panel
            self.log(f"API console unavailable: {e}")
            
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
                     self.track_states[k] = {
                         'effect_id': -1,
                         'clip_id': None,
                         'clip_type': None,
                         'last_sweep_update_local': None,
                         'last_sent_freq': None,
                         'last_mag': None,
                         'last_freq': None,
                     }
                 
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
            self.sweep_markers = []
            
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
            self.log_api("stop_effect", {"scope": "all"})
            
            # Reset valid states (HEAD)
            for k in self.track_states:
                self.track_states[k] = {
                    'effect_id': -1,
                    'clip_id': None,
                    'clip_type': None,
                    'effect_start_time': None,
                    'phase_acc': 0.0,
                    'last_sweep_update_local': None,
                    'last_sent_freq': None,
                    'last_mag': None,
                    'last_freq': None,
                }
            
            # Reset active IDs (Main)
            for t in self.sequencer.tracks:
                for c in t.clips: c.active_effect_id = -1

    def action_restart(self):
        self.sequencer.current_time = 0.0
        self.sequencer.last_tick = time.time()
        # Reset effect states
        engine.stop_effect()
        self.log_api("stop_effect", {"scope": "all"})
        for k in self.track_states:
            self.track_states[k] = {
                'effect_id': -1,
                'clip_id': None,
                'clip_type': None,
                'effect_start_time': None,
                'phase_acc': 0.0,
                'last_sweep_update_local': None,
                'last_sent_freq': None,
                'last_mag': None,
                'last_freq': None,
            }
        for t in self.sequencer.tracks:
            for c in t.clips: c.active_effect_id = -1
        self.sweep_markers = []
        self.log("Restarted")

    def get_canvas_relative_pos(self, global_mouse_pos):
        # Helper to get coords relative to timeline content
        try:
             # Rely on node_pos vs scroll.
             c_min = dpg.get_item_rect_min("timeline_canvas")
             return global_mouse_pos[0] - c_min[0], global_mouse_pos[1] - c_min[1]
        except:
             return 0,0

    def _is_mouse_in_timeline_viewport(self, global_mouse_pos):
        timeline_hit = self._mouse_hits_timeline(global_mouse_pos)
        blocking_hover, hovered_tags = self._highlight_and_report()
        self._refresh_mouse_status(global_mouse_pos, timeline_hit, hovered_tags, blocking_hover)

        if not timeline_hit:
            return False

        if blocking_hover:
            return False

        return True

    def _safe_get_rect(self, tag):
        try:
            return dpg.get_item_rect_min(tag), dpg.get_item_rect_size(tag)
        except Exception:
            return None, None

    def _point_in_rect(self, pt, rect_min, rect_size):
        if not rect_min or not rect_size:
            return False
        max_x = rect_min[0] + rect_size[0]
        max_y = rect_min[1] + rect_size[1]
        return rect_min[0] <= pt[0] <= max_x and rect_min[1] <= pt[1] <= max_y

    def _mouse_hits_timeline(self, global_mouse_pos):
        if dpg.is_item_hovered("panel_inspector"):
            return False
        if self._is_any_inspector_hovered():
            return False

        # Explicitly block if the pointer is inside the inspector/log columns even when not hovering items
        insp_min, insp_size = self._safe_get_rect("panel_inspector")
        log_min, log_size = self._safe_get_rect("panel_log")
        if self._point_in_rect(global_mouse_pos, insp_min, insp_size):
            return False
        if self._point_in_rect(global_mouse_pos, log_min, log_size):
            return False

        if not dpg.does_item_exist("timeline_scroll"):
            return False

        # Primary check: rely on DearPyGui hover for scroll or canvas
        if dpg.is_item_hovered("timeline_scroll") or dpg.is_item_hovered("timeline_canvas"):
            return True

        # Fallback: geometry check on scroll rect
        scroll_min, scroll_size = self._safe_get_rect("timeline_scroll")
        return self._point_in_rect(global_mouse_pos, scroll_min, scroll_size)

    def _on_global_mouse_move(self, sender, app_data):
        global_pos = dpg.get_mouse_pos(local=False)
        timeline_hit = self._mouse_hits_timeline(global_pos)
        blocking_hover, hovered_tags = self._highlight_and_report()
        self._refresh_mouse_status(global_pos, timeline_hit, hovered_tags, blocking_hover)

    def _highlight_and_report(self):
        hovered_blocking = False
        hovered_tags = []
        for tag, cfg in self._hover_configs.items():
            if not dpg.does_item_exist(tag):
                continue
            is_hover = dpg.is_item_hovered(tag)
            self._apply_hover_highlight(tag, cfg, is_hover)
            if is_hover:
                hovered_tags.append(tag)
                if tag in self._blocking_hover_tags:
                    hovered_blocking = True
        inspector_hover_labels = self._get_hovered_inspector_labels()
        if inspector_hover_labels:
            hovered_blocking = True
            hovered_tags.extend(inspector_hover_labels)
        return hovered_blocking, hovered_tags

    def _get_hovered_inspector_labels(self):
        labels = []
        for inspector in self.inspectors:
            tag = inspector.tag_tab
            if not dpg.does_item_exist(tag):
                continue
            if dpg.is_item_hovered(tag):
                target_clip = inspector.clip or self.sequencer.selected_clip
                if target_clip:
                    label = target_clip.name or f"Clip {target_clip.type}"
                else:
                    label = "Live"
                labels.append(f"inspector:{label}")
        return labels

    def _is_any_inspector_hovered(self):
        return bool(self._get_hovered_inspector_labels())

    def _refresh_mouse_status(self, global_mouse_pos, timeline_hit, hovered_tags, blocking_hover):
        if not dpg.does_item_exist(self._mouse_status_tag):
            return
        tags = ",".join([t for t in hovered_tags if t])
        status = f"Mouse: {int(global_mouse_pos[0])},{int(global_mouse_pos[1])} | Timeline={timeline_hit} | Blocking={blocking_hover} | Hovering={tags}"
        try:
            dpg.set_value(self._mouse_status_tag, status)
        except Exception:
            pass

    def _set_mouse_status_visibility(self, visible: bool):
        self._mouse_status_visible = visible
        if dpg.does_item_exist(self._mouse_status_tag):
            dpg.configure_item(self._mouse_status_tag, show=visible)
        if dpg.does_item_exist("menu_mouse_status"):
            dpg.set_value("menu_mouse_status", visible)

    def _on_mouse_status_checkbox(self, sender, app_data):
        self._set_mouse_status_visibility(bool(app_data))

    def _apply_hover_highlight(self, tag, cfg, highlighted):
        if not dpg.does_item_exist(tag):
            return
        colors = cfg.get("hover") if highlighted else cfg.get("default")
        if not colors:
            colors = {}
        bg_color = colors.get("bg")
        border_color = colors.get("border")
        try:
            kwargs = {}
            if bg_color is not None:
                kwargs["bg_color"] = bg_color
            if border_color is not None:
                kwargs["border_color"] = border_color
            if kwargs:
                dpg.configure_item(tag, **kwargs)
        except Exception:
            pass

    def _configure_hover_defaults(self):
        for tag in self._hover_configs.keys():
            cfg = self._hover_configs.get(tag)
            if cfg and dpg.does_item_exist(tag):
                self._apply_hover_highlight(tag, cfg, False)

    def _track_index_from_rel_y(self, rel_y: float) -> int:
        """Map canvas-relative Y into track index; ignore the wheel graph band."""
        track_h = 80
        if rel_y < self.wheel_graph_height:
            return -1
        return int((rel_y - self.wheel_graph_height) // track_h)

    def get_clip_at_pos(self, rel_x, rel_y):
        track_idx = self._track_index_from_rel_y(rel_y)
        if track_idx < 0:
            return None
        click_time = max(0.0, rel_x / self.sequencer.zoom_x)
        
        if 0 <= track_idx < len(self.sequencer.tracks):
             for clip in self.sequencer.tracks[track_idx].clips:
                 if clip.start_time <= click_time < (clip.start_time + clip.duration):
                     return clip
        return None

    def _get_resize_hover(self, track_idx, rel_x):
        """Check for the closest clip edge within the resize threshold and prefer the selected clip."""
        if 0 <= track_idx < len(self.sequencer.tracks):
             track = self.sequencer.tracks[track_idx]
             threshold = self.resize_threshold_px
             best_clip = None
             best_edge = None
             best_score = (threshold, 1)
             selected_clip = self.sequencer.selected_clip
             zoom = self.sequencer.zoom_x
             for clip in track.clips:
                 start_px = clip.start_time * zoom
                 end_px = (clip.start_time + clip.duration) * zoom
                 for edge_pos, edge_name in ((start_px, "left"), (end_px, "right")):
                     delta = abs(rel_x - edge_pos)
                     if delta < threshold:
                         score = (delta, 0 if clip is selected_clip else 1)
                         if score < best_score:
                             best_score = score
                             best_clip = clip
                             best_edge = edge_name
             if best_clip:
                 return best_clip, best_edge
        return None, None

    def update_loop(self):
        # Track Target for Drop/Drag
        mpos = dpg.get_mouse_pos(local=False)
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        mouse_in_timeline = self._is_mouse_in_timeline_viewport(mpos)
        track_idx = self._track_index_from_rel_y(rel_y)
        self.drag_target_track_idx = track_idx if mouse_in_timeline else -1
        interactive_timeline = mouse_in_timeline or getattr(self.sequencer, 'is_scrubbing', False)

        # KEYBOARD SHORTCUTS
        if dpg.is_key_pressed(dpg.mvKey_Delete):
            if dpg.is_item_hovered("timeline_canvas") or dpg.is_item_focused("timeline_scroll"):
                self.delete_selected_clip()

        if dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl):
            if dpg.is_key_pressed(dpg.mvKey_C):
                if self.sequencer.selected_clip:
                    self.sequencer.clipboard = self.sequencer.selected_clip.to_dict()
                    self.log(f"Copied {self.sequencer.selected_clip.name or 'Clip'}")

            if dpg.is_key_pressed(dpg.mvKey_V):
                if self.sequencer.clipboard:
                    data = self.sequencer.clipboard
                    new_clip = Clip.from_dict(data)
                    new_clip.id = str(uuid.uuid4())
                    new_clip.start_time = self.sequencer.current_time

                    target_t_idx = new_clip.track_index
                    if 0 <= target_t_idx < len(self.sequencer.tracks):
                        t = self.sequencer.tracks[target_t_idx]
                        t.clips.append(new_clip)
                        actual_start = self._avoid_overlap_on_drag(t, new_clip, new_clip.start_time)
                        new_clip.start_time = actual_start

                        self.sequencer.selected_clip = new_clip
                        self.update_inspector_ui()
                        self.log("Pasted Clip")

        # DOUBLE CLICK CHECK (Open Inspector)
        if mouse_in_timeline and dpg.is_mouse_button_double_clicked(dpg.mvMouseButton_Left):
            m_pos = dpg.get_mouse_pos(local=False)
            rx, ry = self.get_canvas_relative_pos(m_pos)
            d_clip = self.get_clip_at_pos(rx, ry)
            if d_clip:
                self.sequencer.selected_clip = d_clip
                self.create_floating_inspector(d_clip)

        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        track_h = 80
        mouse_left_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        mouse_left_just_pressed = mouse_left_down and not self.mouse_left_button_down
        self.mouse_left_button_down = mouse_left_down

        resize_clip_hover, resize_edge = (None, None)
        if interactive_timeline and not self.sequencer.drag_clip and not self.sequencer.resize_clip and not getattr(self.sequencer, 'is_scrubbing', False):
            resize_clip_hover, resize_edge = self._get_resize_hover(track_idx, rel_x)

        if not interactive_timeline:
            # Cancel interactions when outside the timeline while keeping the rest of the loop running.
            if self.sequencer.drag_clip:
                self.sequencer.drag_clip = None
            if self.sequencer.resize_clip:
                self.sequencer.resize_clip = None
            if hasattr(self.sequencer, 'is_scrubbing'):
                self.sequencer.is_scrubbing = False
            self.sequencer.drag_time_offset = 0.0
            self._set_system_cursor_visible(True)
        elif mouse_left_down:
            if mouse_left_just_pressed and resize_clip_hover:
                clip, edge = resize_clip_hover, resize_edge
                self.sequencer.selected_clip = clip
                self.sequencer.resize_clip = clip
                self.sequencer.resize_edge = edge
                self.sequencer.resize_initial_dur = clip.duration
                self.sequencer.resize_initial_time = clip.start_time
                self.update_inspector_ui()
                return

            if self.sequencer.resize_clip:
                clip = self.sequencer.resize_clip
                track = self.sequencer.tracks[clip.track_index]
                cur_mouse_t = rel_x / self.sequencer.zoom_x

                if self.sequencer.resize_edge == "right":
                    desired_dur = max(0.1, cur_mouse_t - clip.start_time)
                    clip.duration = self._limit_right_resize(track, clip, desired_dur)
                elif self.sequencer.resize_edge == "left":
                    old_end = self.sequencer.resize_initial_time + self.sequencer.resize_initial_dur
                    desired_start = min(old_end - 0.1, max(0.0, cur_mouse_t))
                    new_start = self._limit_left_resize(track, clip, desired_start, old_end)
                    clip.start_time = new_start
                    clip.duration = old_end - new_start

                self.update_inspector_ui()

            elif self.sequencer.drag_clip:
                pointer_time = rel_x / max(1.0, self.sequencer.zoom_x)
                new_t = max(0.0, pointer_time + self.sequencer.drag_time_offset)

                new_track_idx = self._track_index_from_rel_y(rel_y)

                clip = self.sequencer.drag_clip
                clip.start_time = new_t

                if 0 <= new_track_idx < len(self.sequencer.tracks):
                    if clip.track_index != new_track_idx:
                        old_track = self.sequencer.tracks[clip.track_index]
                        if clip in old_track.clips:
                            old_track.clips.remove(clip)
                        clip.track_index = new_track_idx
                        self.sequencer.tracks[new_track_idx].clips.append(clip)

                    active_track = self.sequencer.tracks[clip.track_index]
                    snapped_start = self._snap_to_edges(active_track, clip, clip.start_time)
                    clip.start_time = self._avoid_overlap_on_drag(active_track, clip, snapped_start)

                self.update_inspector_ui()

            elif interactive_timeline:
                hover_clip = None
                if not self.sequencer.is_scrubbing:
                    hover_clip = self.get_clip_at_pos(rel_x, rel_y)

                if mouse_left_just_pressed:
                    if hover_clip and not self.sequencer.is_scrubbing:
                        self.sequencer.selected_clip = hover_clip
                        self.update_inspector_ui()

                        clip_px_start = hover_clip.start_time * self.sequencer.zoom_x
                        clip_px_end = (hover_clip.start_time + hover_clip.duration) * self.sequencer.zoom_x
                        edge_threshold = self.resize_threshold_px

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
                            self.sequencer.drag_clip = hover_clip
                            mouse_time = rel_x / max(1.0, self.sequencer.zoom_x)
                            self.sequencer.drag_time_offset = hover_clip.start_time - mouse_time
                    else:
                        was_scrubbing = self.sequencer.is_scrubbing
                        self.sequencer.is_scrubbing = True
                        new_t = max(0.0, rel_x / self.sequencer.zoom_x)
                        if new_t < 0.05:
                            new_t = 0.0
                        self.sequencer.current_time = new_t
                        if not was_scrubbing:
                            self.sequencer.selected_clip = None
                            self.update_inspector_ui()
                elif self.sequencer.is_scrubbing:
                    new_t = max(0.0, rel_x / self.sequencer.zoom_x)
                    if new_t < 0.05:
                        new_t = 0.0
                    self.sequencer.current_time = new_t
        else:
            # Mouse Up - Release Drag/Resize/Scrub
            if self.sequencer.drag_clip:
                self.sequencer.drag_clip = None
            if self.sequencer.resize_clip:
                self.sequencer.resize_clip = None
            if hasattr(self.sequencer, 'is_scrubbing'):
                self.sequencer.is_scrubbing = False
            self.sequencer.drag_time_offset = 0.0
        
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
                    # Loop Mode: restart immediately at end
                    if self.sequencer.current_time >= max_end:
                        self.sequencer.current_time = 0.0
                        self.sequencer.last_tick = now
                        engine.stop_effect()
                        self.log_api("stop_effect", {"scope": "all"})
                        for k in self.track_states:
                            self.track_states[k] = {
                                'effect_id': -1,
                                'clip_id': None,
                                'clip_type': None,
                                'effect_start_time': None,
                                'phase_acc': 0.0,
                                'last_sweep_update_local': None,
                                'last_sent_freq': None,
                                'last_mag': None,
                                'last_freq': None,
                            }
                        for t in self.sequencer.tracks:
                            for c in t.clips:
                                c.active_effect_id = -1
                        self.sweep_markers = []
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

        # Always sample wheel position regardless of playback state so the overlay stays populated
        self._record_wheel_sample(self.sequencer.current_time)
        self._throttle_when_idle()

    def _record_wheel_sample(self, current_time: float):
        if self.sequencer.drag_clip or self.sequencer.resize_clip or getattr(self.sequencer, 'is_scrubbing', False):
            return
        raw = engine.get_axis_value(-1)
        if raw is None:
            return
        axis_scale = self.wheel_axis_scale if self.wheel_axis_scale else 32767.0
        norm = raw / axis_scale
        norm = max(-1.0, min(1.0, norm))
        self.wheel_history.append({"time": current_time, "value": norm})
        if self.wheel_history_limit and len(self.wheel_history) > self.wheel_history_limit:
            while len(self.wheel_history) > self.wheel_history_limit:
                self.wheel_history.popleft()
        self.hide_playhead_until_next_sample = False

    def _clear_wheel_history(self):
        self.wheel_history.clear()
        self.hide_playhead_until_next_sample = False

    def _draw_wheel_graph(self, total_w: float):
        height = max(0, self.wheel_graph_height)
        if height <= 0:
            return

        gain = max(0.1, self.wheel_graph_gain)

        dpg.draw_rectangle([0, 0], [total_w, height], color=(40, 40, 50, 220), fill=(20, 20, 25, 190), parent="timeline_canvas")

        center_y = height / 2
        if total_w > 0:
            dpg.draw_line([0, center_y], [total_w, center_y], color=(100, 110, 140, 200), thickness=1, parent="timeline_canvas")
        dot_width = 6
        dot_gap = 10
        int_width = max(0, int(total_w))
        for x in range(0, int_width, dot_gap):
            end_x = min(total_w, x + dot_width)
            dpg.draw_line([x, center_y], [end_x, center_y], color=(170, 170, 190, 120), thickness=1, parent="timeline_canvas")

        segments = []
        current_segment = []
        prev_time = None
        gap_threshold = 0.25  # seconds between samples considered a jump

        for sample in self.wheel_history:
            sample_time = sample["time"]
            x = sample_time * self.sequencer.zoom_x
            if x < 0 or x > total_w:
                continue
            val = max(-1.0, min(1.0, sample["value"] * gain))
            y = (height / 2) - val * (height / 2 - 6)

            if prev_time is not None:
                dt = sample_time - prev_time
                if dt < 0 or dt > gap_threshold:
                    if len(current_segment) >= 2:
                        segments.append(current_segment)
                    current_segment = []

            current_segment.append([x, y])
            prev_time = sample_time

        if len(current_segment) >= 2:
            segments.append(current_segment)

        for segment in segments:
            dpg.draw_polyline(segment, color=(100, 220, 255, 220), thickness=2, parent="timeline_canvas")

        if self._should_show_wheel_playhead():
            playhead_x = self.sequencer.current_time * self.sequencer.zoom_x
            dpg.draw_line([playhead_x, 0], [playhead_x, height], color=(255, 60, 60), thickness=1, parent="timeline_canvas")

            latest = self.wheel_history[-1] if self.wheel_history else None
            if latest:
                val = max(-1.0, min(1.0, latest["value"] * gain))
                y = (height / 2) - val * (height / 2 - 6)
                dpg.draw_circle([playhead_x, y], 4, color=(255, 150, 60), fill=(255, 150, 60), parent="timeline_canvas")
                dpg.draw_text([4, 4], f"Wheel: {latest['value']*100:.1f}%", size=12, color=(220, 220, 255), parent="timeline_canvas")


    def _should_show_wheel_playhead(self) -> bool:
        return not self.hide_playhead_until_next_sample


    def _throttle_when_idle(self):
        if self.sequencer.is_playing:
            return
        if self.sequencer.drag_clip or self.sequencer.resize_clip or getattr(self.sequencer, 'is_scrubbing', False):
            return
        time.sleep(0.003)

    def process_sequencer_logic(self):
        cur_t = self.sequencer.current_time
        
        # Read Global Gain
        global_gain = 1.0
        if dpg.does_item_exist("slider_gain"):
             global_gain = dpg.get_value("slider_gain") / 100.0

        # Iterate Tracks (not clips) to manage monophonic channel state
        for t_idx, track in enumerate(self.sequencer.tracks):
            if t_idx not in self.track_states:
                self.track_states[t_idx] = {
                    'effect_id': -1,
                    'clip_id': None,
                    'clip_type': None,
                    'effect_start_time': None,
                    'last_sweep_update_local': None,
                    'last_sent_freq': None,
                    'last_mag': None,
                    'last_freq': None,
                }
            
            state = self.track_states[t_idx]
            prev_cid = state['clip_id']
            eff_id = state['effect_id']
            prev_ctype = state['clip_type']
            
            # Find current logical clip
            current_clip = self.sequencer.get_clip_at_precise(t_idx, cur_t)
            curr_cid = current_clip.id if current_clip else None
            
            # Helper to update active effect with new clip
            def start_new_effect(start_phase=-1):
                if not current_clip:
                    return -1
                dur_ms = int(current_clip.duration * 1000)
                eff_mag = int(current_clip.magnitude * global_gain)

                # Build descriptor for engine.play_descriptor
                type_map = {
                    "Sine": "sine",
                    "Square": "square",
                    "Triangle": "triangle",
                    "SawtoothUp": "sawtoothup",
                    "SawtoothDown": "sawtoothdown",
                    "Sawtooth": "sawtoothup",
                    "Constant": "constant",
                    "Ramp": "ramp",
                    "Spring": "spring",
                    "Damper": "damper",
                    "Inertia": "inertia",
                    "Friction": "friction",
                    "LeftRight": "leftright",
                }
                tkey = type_map.get(current_clip.type, "sine")

                direction_mode = (current_clip.direction_mode or "Polar").lower()
                direction = {}
                if direction_mode == "cartesian":
                    direction = {"x": current_clip.x, "y": current_clip.y, "z": current_clip.z}
                elif direction_mode == "spherical":
                    direction = {"yaw": max(0, min(36000, current_clip.yaw)), "pitch": max(0, min(36000, current_clip.pitch)), "distance": max(0, current_clip.distance)}
                else:
                    dir_deg = max(0, min(359, getattr(current_clip, "angle", 90)))
                    direction = {"angle": int(dir_deg * 100), "radius": max(0, current_clip.radius)}

                # Phase: slider stores 0-359 deg; API expects 0-35900 (hundredths). 36000 wraps to 0.
                if start_phase != -1:
                    phase_payload = max(0, min(35900, int(start_phase)))
                else:
                    phase_deg = max(0, min(359, int(getattr(current_clip, "start_phase", 0))))
                    phase_payload = phase_deg * 100

                desc = {
                    "type": tkey,
                    "frequency_hz": float(current_clip.frequency),
                    "magnitude": eff_mag,
                    "length_ms": dur_ms,
                    "phase": phase_payload,
                    "direction_mode": direction_mode,
                    "direction": direction,
                    "start_mag": current_clip.start_mag,
                    "end_mag": current_clip.end_mag,
                    "envelope": {
                        "attack_length": current_clip.attack_length,
                        "fade_length": current_clip.fade_length,
                        "attack_level": 0,
                        "fade_level": 0,
                    },
                }

                self.log_api("play_descriptor", desc)
                new_id = engine.play_descriptor(desc)
                state['effect_start_time'] = current_clip.start_time
                state['last_sweep_update_local'] = None
                # Initialize tracking so the first continuation tick doesn't immediately trigger an update
                state['last_mag'] = current_clip.magnitude
                state['last_freq'] = current_clip.frequency
                state['last_sent_freq'] = current_clip.frequency
                return new_id

            # State Machine
            if curr_cid == prev_cid:
                # CONTINUATION (Same clip still playing)
                if current_clip and eff_id != -1:
                    # Update parameters if needed (Software Sweep)
                    remaining_ms = int((current_clip.duration - (cur_t - current_clip.start_time)) * 1000)
                    if remaining_ms < 0: remaining_ms = 0 # Safety
                    
                    if current_clip.type == "Sine":
                        # --- REAL-TIME UPDATE LOGIC ---
                        # Verify change against stored state or just update if sweep
                        is_sweep = current_clip.frequency != current_clip.frequency_end and current_clip.sweep_enabled
                        
                        # Check "Dirty" State (User changed sliders)
                        last_mag = state.get('last_mag', -1)
                        last_freq = state.get('last_freq', -1)

                        has_changed = (current_clip.magnitude != last_mag) or \
                                      (current_clip.frequency != last_freq and not is_sweep)

                        # Target ~10 updates per Hz across the clip duration (time-based, not frame-based)
                        t_local = max(0.0, cur_t - (state.get('effect_start_time') if state.get('effect_start_time') is not None else current_clip.start_time))
                        sweep_ready = False
                        if is_sweep:
                            freq_span = abs(current_clip.frequency_end - current_clip.frequency)
                            steps = max(1, int(math.ceil(freq_span * 10.0)))
                            interval = current_clip.duration / steps if steps > 0 else current_clip.duration
                            last_local = state.get('last_sweep_update_local')
                            sweep_ready = ((last_local is None) and (t_local >= interval)) or (
                                last_local is not None and (t_local - last_local >= interval)
                            )

                        # Calculate current parameters for monitoring
                        progress = t_local / current_clip.duration
                        progress = max(0.0, min(1.0, progress))

                        if is_sweep:
                            current_freq = float(current_clip.frequency + (current_clip.frequency_end - current_clip.frequency) * progress)
                            current_freq = max(0.1, current_freq)
                        else:
                            current_freq = float(current_clip.frequency)

                        # Always refresh on-screen monitor frequency
                        dpg.set_value("monitor_freq", f"Freq: {current_freq:.2f} Hz")

                        should_send = (is_sweep and sweep_ready) or has_changed

                        if should_send:
                             effect_len_ms = int(max(0.0, current_clip.duration - t_local) * 1000)

                             # Calculate continuous phase from clip start to preserve direction on each update
                             t_elapsed = t_local
                             start_f = current_clip.frequency
                             end_f = current_clip.frequency_end if is_sweep else start_f
                             k = (end_f - start_f) / max(1e-6, current_clip.duration)
                             phase_integral = (start_f * t_elapsed + 0.5 * k * t_elapsed * t_elapsed)
                             start_deg = getattr(current_clip, "start_phase", 0)
                             norm_phase = (phase_integral + (start_deg / 360.0)) % 1.0
                             phase_payload = int(norm_phase * 36000)
                             
                             eff_mag = int(current_clip.magnitude * global_gain)
                             log_payload = {"effect_id": eff_id, "freq": current_freq, "mag": eff_mag, "length_ms": effect_len_ms, "phase": phase_payload}
                             self.log_api("update_effect_sine", log_payload)

                             new_eff_id = engine.update_effect_sine(eff_id, current_freq, eff_mag, effect_len_ms, phase=phase_payload)

                             if new_eff_id != -1:
                                 eff_id = new_eff_id
                                 state['effect_id'] = eff_id
                                 state['last_sent_freq'] = current_freq
                                 if is_sweep:
                                     state['last_sweep_update_local'] = t_local
                                     # record marker for visualization
                                     self.sweep_markers.append({"time": cur_t, "phase": phase_payload})
                                     if len(self.sweep_markers) > 500:
                                         self.sweep_markers.pop(0)

                        # Update State
                        state['last_mag'] = current_clip.magnitude
                        state['last_freq'] = current_clip.frequency

                # Update Phase Tracking for next frame's potential transition
                if current_clip and current_clip.type == "Sine":
                     t_local = max(0.0, cur_t - (state.get('effect_start_time') if state.get('effect_start_time') is not None else current_clip.start_time))
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

                    # Clamp to valid API range (0..35900); 36000 wraps to 0
                    phase_to_use = max(0, min(35900, int(phase_to_use)))
                    
                    self.log_api("update_effect_sine", {"effect_id": eff_id, "freq": current_clip.frequency, "mag": eff_mag, "length_ms": dur_ms, "phase": phase_to_use})
                    new_id = engine.update_effect_sine(eff_id, current_clip.frequency, eff_mag, dur_ms, phase=phase_to_use)
                    if new_id != -1:
                        transferred = True
                        eff_id = new_id
                        state['effect_start_time'] = current_clip.start_time
                
                if not transferred:
                    # Stop Old
                    if eff_id != -1:
                        self.log_api("stop_effect", {"effect_id": eff_id})
                        engine.stop_effect(eff_id)
                        eff_id = -1
                    
                    # Start New
                    if current_clip:
                        eff_id = start_new_effect(start_phase=start_phase_override)
                
                # Save State
                state['effect_id'] = eff_id
                state['clip_id'] = curr_cid
                state['clip_type'] = current_clip.type if current_clip else None
                state['effect_start_time'] = current_clip.start_time if current_clip else None
                state['last_sweep_update_local'] = None
                if current_clip:
                    # Seed tracking to prevent an immediate "changed" update on the next tick
                    state['last_sent_freq'] = current_clip.frequency
                    state['last_mag'] = current_clip.magnitude
                    state['last_freq'] = current_clip.frequency
                    state['last_phase'] = start_phase_override if start_phase_override != -1 else 0
                else:
                    state['last_sent_freq'] = None
                    state['last_mag'] = None
                    state['last_freq'] = None


    # --- Rendering ---
    def render_grid(self, total_w, total_h):
        """Draws vertical grid lines and time labels based on current zoom."""
        target_px = 100.0
        ideal_dt = target_px / max(0.1, self.sequencer.zoom_x)
        auto_grid_time = self._nice_grid_interval(ideal_dt)
        grid_time = self.manual_timebase_value if (self.manual_timebase_override and self.manual_timebase_value) else auto_grid_time

        if dpg.does_item_exist("input_timebase"):
             if not self.manual_timebase_override and not dpg.is_item_active("input_timebase"):
                 dpg.set_value("input_timebase", grid_time)
                 self.manual_timebase_value = grid_time
             elif not self.manual_timebase_override:
                 self.manual_timebase_value = grid_time

        scroll_x = 0
        scroll_w = total_w
        pad_px = 200
        if dpg.does_item_exist("timeline_scroll"):
            try:
                scroll_x = dpg.get_x_scroll("timeline_scroll")
            except:
                scroll_x = 0
            rect = dpg.get_item_rect_size("timeline_scroll")
            if rect:
                scroll_w = rect[0]

        start_px = max(0, scroll_x - pad_px)
        end_px = min(total_w, scroll_x + scroll_w + pad_px)
        t = math.floor((start_px / self.sequencer.zoom_x) / grid_time) * grid_time if grid_time > 0 else 0.0
        lines_drawn = 0
        max_lines = 600
        while True:
            x = t * self.sequencer.zoom_x
            if x > end_px or lines_drawn >= max_lines:
                break
            if x >= start_px:
                color = (60, 60, 60, 100)
                thickness = 1
                dpg.draw_line([x, 0], [x, total_h], color=color, thickness=thickness, parent="timeline_canvas")
                lines_drawn += 1
            t += grid_time

    def _nice_grid_interval(self, ideal_dt: float) -> float:
        if ideal_dt <= 0:
            return 0.01
        fine_threshold = 0.08
        fine_steps = (0.01, 0.02, 0.05)
        if ideal_dt <= fine_threshold:
            for step in reversed(fine_steps):
                if ideal_dt >= step:
                    return step
            return fine_steps[0]

        power = math.floor(math.log10(ideal_dt))
        base = 10 ** power
        frac = ideal_dt / base
        if frac < 1.5:
            nice = 1
        elif frac < 3:
            nice = 2
        elif frac < 7:
            nice = 5
        else:
            nice = 10
        return nice * base

    def render_timeline(self):
        if dpg.does_item_exist("timeline_canvas"):
            dpg.delete_item("timeline_canvas", children_only=True)

        y_offset = self.wheel_graph_height
        track_height = 80
        total_w = max(3000, int(self.sequencer.zoom_x * 60))
        total_h = int(self.wheel_graph_height + len(self.sequencer.tracks) * track_height)
        dpg.configure_item("timeline_canvas", width=total_w, height=total_h)
        self.render_grid(total_w, total_h)
        
        if self.wheel_graph_height > 0:
            self._draw_wheel_graph(total_w)

        for i, track in enumerate(self.sequencer.tracks):
            is_target = (i == self.drag_target_track_idx)
            
            bg_col = (40, 40, 45, 50) if i % 2 == 0 else (35, 35, 40, 50)
            if i == self.drag_target_track_idx:
                bg_col = (60, 60, 80, 100)

            dpg.draw_rectangle([0, y_offset], [total_w, y_offset + track_height], color=bg_col, fill=bg_col, parent="timeline_canvas")
            dpg.draw_line([0, y_offset + track_height], [total_w, y_offset + track_height], color=(60, 60, 60), parent="timeline_canvas")
            dpg.draw_text([10, y_offset + 5], track.name, size=15, color=(200, 200, 200), parent="timeline_canvas")
            
            for clip in track.clips:
                x_start = clip.start_time * self.sequencer.zoom_x
                width = clip.duration * self.sequencer.zoom_x
                
                base_col = (100, 150, 255) if clip.type == "Sine" else (255, 100, 100)
                if clip.type == "Constant":
                    base_col = (100, 255, 100)
                if clip.type == "Ramp":
                    base_col = (255, 255, 100)
                if clip.type == "Sawtooth":
                    base_col = (255, 150, 50)

                border_col = (255, 255, 255) if clip == self.sequencer.selected_clip else base_col
                
                dpg.draw_rectangle([x_start, y_offset + 20], [x_start + width, y_offset + track_height - 5], color=border_col, thickness=2, fill=(base_col[0], base_col[1], base_col[2], 150), parent="timeline_canvas")
                dpg.draw_text([x_start + 5, y_offset + 25], clip.name, size=13, parent="timeline_canvas")

                wave_points = self._clip_wave_points(
                    clip,
                    x_start + 4,
                    max(4.0, width - 8),
                    y_offset + 28,
                    y_offset + track_height - 12,
                    samples=max(20, int(width / 6))
                )
                dpg.draw_polyline(wave_points, color=(240, 240, 240, 220), thickness=2, parent="timeline_canvas")

            y_offset += track_height

        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y_offset], color=(255, 50, 50), thickness=2, parent="timeline_canvas")

        if self.sweep_markers:
            for m in self.sweep_markers:
                mx = m.get("time", 0.0) * self.sequencer.zoom_x
                phase_txt = f"{m.get('phase', 0)/100.0:.1f}°"
                dpg.draw_line([mx, 0], [mx, y_offset], color=(200, 50, 50, 120), thickness=1, parent="timeline_canvas")
                dpg.draw_text([mx + 4, 8], phase_txt, size=12, color=(255, 120, 120), parent="timeline_canvas")

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
        track_idx = self._track_index_from_rel_y(rel_y)
        
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
        if not self._is_mouse_in_timeline_viewport(mpos):
            return
        rel_x, rel_y = self.get_canvas_relative_pos(mpos)
        
        track_h = 80
        track_idx = self._track_index_from_rel_y(rel_y)
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
            self.manual_timebase_override = False
            self.manual_timebase_value = None
            # Zoom
            scale_factor = 1.15
            if app_data > 0:
                self.sequencer.zoom_x *= scale_factor
            elif app_data < 0:
                self.sequencer.zoom_x /= scale_factor

            # Clamp settings
            self.sequencer.zoom_x = max(10.0, min(50000.0, self.sequencer.zoom_x))
            
            # Prevent Vertical Scroll Drift?
            # If content fits, ensure Y scroll is 0?
            total_h = self.wheel_graph_height + len(self.sequencer.tracks) * 80
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
        self.manual_timebase_override = True
        self.manual_timebase_value = app_data
        

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
        def ctrl_pressed() -> bool:
            return dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)

        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_S, callback=lambda: dpg.show_item("dlg_save") if ctrl_pressed() else None)
            dpg.add_key_press_handler(dpg.mvKey_O, callback=lambda: dpg.show_item("dlg_load") if ctrl_pressed() else None)

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
                    dpg.add_menu_item(label="Mouse Status", tag="menu_mouse_status", check=True, default_value=True, callback=self._on_mouse_status_checkbox)

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
                dpg.add_mouse_move_handler(callback=self._on_global_mouse_move)
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
                dpg.add_button(label="Clear", width=70, callback=lambda: self._clear_wheel_history())
                
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
                    self.make_drag_source("Square", "Square")
                    self.make_drag_source("Triangle", "Triangle")
                    self.make_drag_source("Constant", "Constant")
                    self.make_drag_source("Ramp", "Ramp")
                    self.make_drag_source("Saw Up", "SawtoothUp")
                    self.make_drag_source("Saw Down", "SawtoothDown")
                    self.make_drag_source("Spring", "Spring")
                    self.make_drag_source("Damper", "Damper")
                    self.make_drag_source("Inertia", "Inertia")
                    self.make_drag_source("Friction", "Friction")
                    self.make_drag_source("Left/Right", "LeftRight")
                    dpg.add_spacer(height=20)
                    dpg.add_button(label="+ Add Track", width=100, callback=lambda: self.sequencer.tracks.append(Track(name="New Track")))

                # Col 2: Timeline
                with dpg.group(tag="timeline_group"):
                    # Scroll Window
                    with dpg.child_window(tag="timeline_scroll", horizontal_scrollbar=True, no_scroll_with_mouse=True):
                        with dpg.drawlist(width=3000, height=1000, tag="timeline_canvas"):
                            pass

                    dpg.add_text("Drop Effects Here", parent="timeline_scroll", color=(100,100,100))
                    dpg.add_text("", tag=self._mouse_status_tag, parent="timeline_scroll", color=(200, 210, 255))

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

                self._configure_hover_defaults()
                self._set_mouse_status_visibility(self._mouse_status_visible)

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

        try:
            while dpg.is_dearpygui_running():
                try:
                    self.update_loop()
                except Exception as e:
                    print(f"Update Loop Crash: {e}")
                    self.log(f"CRITICAL: {e}")
                    # Optional: pause playback on crash to prevent loop
                    self.sequencer.is_playing = False
                    
                dpg.render_dearpygui_frame()
        finally:
            try:
                engine.stop_effect()
                self.log_api("stop_effect", {"scope": "all"})
            except Exception as e:
                self.log(f"Stop on exit failed: {e}")
            dpg.destroy_context()

if __name__ == "__main__":
    app = FeditNativeApp()
    app.run()
