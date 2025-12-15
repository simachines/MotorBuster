import dearpygui.dearpygui as dpg
import sys
import os
import time
from dataclasses import dataclass, field
import uuid
import json
import math

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
    magnitude: int = 10000
    frequency: int = 10
    frequency_end: int = 10 # For Sweep
    active_effect_id: int = -1 # Runtime ID
    
    def to_dict(self):
        return {
            "id": self.id, "type": self.type, "start_time": self.start_time,
            "duration": self.duration, "track_index": self.track_index,
            "magnitude": self.magnitude, "frequency": self.frequency,
            "frequency_end": self.frequency_end
        }
    
    @staticmethod
    def from_dict(d):
        freq = d.get("frequency", 10)
        c = Clip(
            id=d.get("id", str(uuid.uuid4())), type=d.get("type", "Sine"),
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

    def get_clip_at_precise(self, track_idx, time_s):
        # Half-open interval [start, end) logic for playback to avoid overlap
        for clip in self.tracks[track_idx].clips:
            if clip.start_time <= time_s < clip.start_time + clip.duration:
                return clip
        return None

# --- Application ---
class FeditNativeApp:
    def __init__(self):
        self.sequencer = FeditSequencer()
        self.log_items = []
        self.renaming_track_idx = -1
        self.drag_target_track_idx = -1 # For visual highlight
        
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

    def _clip_wave_points(self, clip: Clip, x_start: float, width: float, y_top: float, y_bottom: float, samples: int = 0):
        # Precise Peak-Detection for Aliasing
        points = []
        y_mid = (y_top + y_bottom) / 2.0
        amp_span = (y_bottom - y_top) * 0.45 
        mag_max = max(1.0, abs(clip.magnitude))

        if width <= 0: return []
        
        pixels = int(width)
        step_t = clip.duration / max(1, pixels)
        
        # Hard limit
        if pixels > 5000: pixels = 5000; step_t = clip.duration / pixels

        freq = clip.frequency
        is_aliasing = False
        if freq > 0 and (1.0/freq) < step_t * 2.5:
             is_aliasing = True

        for i in range(pixels):
            t0 = i * step_t
            t1 = (i + 1) * step_t
            
            local_min = 0.0
            local_max = 0.0

            if is_aliasing:
                # Aliasing: Draw full height
                local_min = -1.0
                local_max = 1.0
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

    def connect_device_by_name(self, name):
         try:
             idx = int(name.split("#")[-1].replace(")", ""))
             if engine.connect_device(idx):
                 dpg.set_value("status_text", "Status: Connected")
                 dpg.configure_item("status_text", color=(0, 255, 0))
         except: pass

    def connect_callback(self):
        val = dpg.get_value("device_combo")
        if val and "No Devices" not in val:
            self.connect_device_by_name(val)
        else:
            self.scan_devices()

    # --- Transport Logic ---
    def toggle_play(self):
        self.sequencer.is_playing = not self.sequencer.is_playing
        self.sequencer.last_tick = time.time()
        label = "Stop" if self.sequencer.is_playing else "Play"
        dpg.configure_item("btn_play", label=label)
        
        if not self.sequencer.is_playing:
            # Stop all
            engine.stop_effect()
            # Reset active IDs
            for t in self.sequencer.tracks:
                for c in t.clips: c.active_effect_id = -1

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

        # DOUBLE CLICK CHECK (Open Inspector)
        if dpg.is_mouse_button_double_clicked(dpg.mvMouseButton_Left):
             m_pos = dpg.get_mouse_pos(local=False)
             rx, ry = self.get_canvas_relative_pos(m_pos)
             d_clip = self.get_clip_at_pos(rx, ry)
             if d_clip:
                 self.sequencer.selected_clip = d_clip
                 self.show_floating_inspector()

        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        # Mouse logic for Dragging Clips vs Scrubbing vs Resizing
        if dpg.is_mouse_button_down(dpg.mvMouseButton_Left):
             mpos = dpg.get_mouse_pos(local=False)
             rel_x, rel_y = self.get_canvas_relative_pos(mpos)
             
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
                  
                  track_h = 80
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
                          
             # 3. INITIALIZE INTERACTION (Select / Init Drag / Init Resize / Seek)
             elif dpg.is_item_hovered("timeline_canvas") or dpg.is_item_hovered("timeline_scroll"):
                 # Check if over a clip
                 hover_clip = self.get_clip_at_pos(rel_x, rel_y)
                 
                 if hover_clip:
                     # HIT CLIP -> Select & Init Drag/Resize
                     self.sequencer.selected_clip = hover_clip
                     self.update_inspector_ui()
                     # dpg.configure_item("chk_insp_lock", default_value=True) # REMOVED: Don't force lock
                     
                     
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
                     # HIT EMPTY -> Seek & Deselect
                     new_t = max(0.0, rel_x / self.sequencer.zoom_x)
                     self.sequencer.current_time = new_t
                     
                     # Deselect if not locked
                     is_locked = dpg.get_value("chk_insp_lock") if dpg.does_item_exist("chk_insp_lock") else False
                     if not is_locked:
                         self.sequencer.selected_clip = None
                         self.update_inspector_ui()

        else:
            # Mouse Up - Release Drag/Resize
            if self.sequencer.drag_clip:
                self.sequencer.drag_clip = None
            if self.sequencer.resize_clip:
                self.sequencer.resize_clip = None
        
        if self.sequencer.is_playing:
            now = time.time()
            dt = now - self.sequencer.last_tick
            self.sequencer.last_tick = now
            self.sequencer.current_time += dt
            if self.sequencer.is_playing: self.process_sequencer_logic()
            
        dpg.set_value("time_display", f"{self.sequencer.current_time:.2f}s")
        self.render_timeline()

    def process_sequencer_logic(self):
        cur_t = self.sequencer.current_time
        
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
            def start_new_effect():
                if not current_clip: return -1
                dur_ms = int(current_clip.duration * 1000)
                new_id = -1
                if current_clip.type == "Sine":
                    new_id = engine.start_effect_sine(current_clip.frequency, current_clip.magnitude, dur_ms)
                elif current_clip.type == "Constant":
                    new_id = engine.start_effect_constant(current_clip.magnitude, dur_ms)
                elif current_clip.type == "Ramp":
                    new_id = engine.start_effect_ramp(0, current_clip.magnitude, dur_ms)
                elif current_clip.type == "Sawtooth":
                    per = int(1000 / max(1, current_clip.frequency))
                    new_id = engine.start_effect_sawtooth(current_clip.magnitude, per, dur_ms)
                return new_id

            # State Machine
            if curr_cid == prev_cid:
                # CONTINUATION (Same clip still playing)
                if current_clip and eff_id != -1:
                    # Update parameters if needed (Software Sweep)
                    remaining_ms = int((current_clip.duration - (cur_t - current_clip.start_time)) * 1000)
                    if remaining_ms < 0: remaining_ms = 0 # Safety
                    
                    if current_clip.type == "Sine":
                        # Sweep Logic
                        if current_clip.frequency != current_clip.frequency_end:
                             progress = (cur_t - current_clip.start_time) / current_clip.duration
                             progress = max(0.0, min(1.0, progress))
                             current_freq = int(current_clip.frequency + (current_clip.frequency_end - current_clip.frequency) * progress)
                             current_freq = max(1, current_freq)
                             
                             # FIX: Use full duration to prevent timeout dropouts during recreation
                             effect_len_ms = int(current_clip.duration * 1000) 
                             new_eff_id = engine.update_effect_sine(eff_id, current_freq, current_clip.magnitude, effect_len_ms)
                             if new_eff_id != -1:
                                 eff_id = new_eff_id
                                 state['effect_id'] = eff_id
                             
                elif current_clip and eff_id == -1:
                    # Recovery: Should be playing but isn't
                    eff_id = start_new_effect()
                    state['effect_id'] = eff_id
                    
            else:
                # TRANSITION (Clip Changed or Ended/Started)
                
                # Try Transfer (Reuse Effect)
                transferred = False
                # FIX: Disable reuse to avoid issues with updating stopped effects. 
                # Always Stop/Start new ensures reliable triggering.
                if False and eff_id != -1 and current_clip and prev_ctype == current_clip.type:
                     # Reuse Effect ID by Updating
                     dur_ms = int(current_clip.duration * 1000)
                     transferred = True
                     
                     if current_clip.type == "Sine":
                         engine.update_effect_sine(eff_id, current_clip.frequency, current_clip.magnitude, dur_ms)
                     elif current_clip.type == "Constant":
                         engine.update_effect_constant(eff_id, current_clip.magnitude, dur_ms)
                     elif current_clip.type == "Ramp":
                         engine.update_effect_ramp(eff_id, 0, current_clip.magnitude, dur_ms)
                     elif current_clip.type == "Sawtooth":
                         per = int(1000 / max(1, current_clip.frequency))
                         engine.update_effect_sawtooth(eff_id, current_clip.magnitude, per, dur_ms)
                     else:
                         transferred = False
                
                if not transferred:
                    # Stop Old
                    if eff_id != -1:
                        engine.stop_effect(eff_id)
                        eff_id = -1
                    
                    # Start New
                    if current_clip:
                        eff_id = start_new_effect()
                
                # Save State
                state['effect_id'] = eff_id
                state['clip_id'] = curr_cid
                state['clip_type'] = current_clip.type if current_clip else None

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
                 dpg.draw_text([x_start + 5, y_offset + 25], clip.type, size=13, parent="timeline_canvas")

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

    def delete_selected_clip(self):
        if self.sequencer.selected_clip:
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
                
                with dpg.window(tag="win_clip_opts", label="Clip Options", width=200, height=100, modal=True, show=True, pos=mpos):
                    dpg.add_text(f"Clip: {clip.type}")
                    def do_del_clip(s, a, u):
                        self.delete_selected_clip()
                        dpg.delete_item("win_clip_opts")
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

        found = False
        # Remove old legacy selection loop if present
        return

    def on_key_press(self, sender, app_data):
        if app_data == dpg.mvKey_Delete or app_data == dpg.mvKey_Back:
            # Check if an input is active (don't delete clip while typing)
            if dpg.is_item_active(dpg.get_active_item()):
                return
            self.delete_selected_clip()

    def update_selected_clip(self, sender, app_data, user_data):
        if not self.sequencer.selected_clip: return
        param = user_data
        if param == "freq": self.sequencer.selected_clip.frequency = app_data
        elif param == "freq_end": self.sequencer.selected_clip.frequency_end = app_data
        elif param == "mag": self.sequencer.selected_clip.magnitude = app_data
        elif param == "dur": self.sequencer.selected_clip.duration = app_data
        elif param == "start": self.sequencer.selected_clip.start_time = app_data


    def update_mag_percent(self, sender, app_data, user_data):
        if not self.sequencer.selected_clip: return
        # app_data is 0-100
        val = max(0, min(100, app_data))
        mag = int((val / 100.0) * 32767)
        self.sequencer.selected_clip.magnitude = mag
        
        # Update software sweep if needed? 
        # (Clip data is updated, next update_loop will handle engine update)
        
    def show_floating_inspector(self):
        # Create or Show Window
        if not dpg.does_item_exist("win_inspector_floating"):
            with dpg.window(tag="win_inspector_floating", label="Inspector", width=250, height=300, pos=[400, 200]):
                 dpg.add_text("Clip Properties")
                 dpg.add_input_float(label="Start (s)", tag="insp_start", callback=self.update_selected_clip, user_data="start")
                 dpg.add_input_float(label="Duration (s)", tag="insp_dur", callback=self.update_selected_clip, user_data="dur")
                 dpg.add_slider_int(label="Magnitude %", tag="insp_mag_pct", max_value=100, callback=self.update_mag_percent)
                 dpg.add_input_int(label="HZ Start", tag="insp_freq", min_value=1, max_value=5000, step=1, step_fast=10, callback=self.update_selected_clip, user_data="freq")
                 dpg.add_input_int(label="HZ End", tag="insp_freq_end", min_value=1, max_value=5000, step=1, step_fast=10, callback=self.update_selected_clip, user_data="freq_end", show=False)
        
        dpg.show_item("win_inspector_floating")
        dpg.focus_item("win_inspector_floating")
        self.update_inspector_ui()

    def update_inspector_ui(self):
        # Helper to sync UI with selection
        clip = self.sequencer.selected_clip
        
        # Only update if the window actually exists and is visible
        if not dpg.does_item_exist("win_inspector_floating") or not dpg.is_item_visible("win_inspector_floating"):
            return

        if clip:
             # Populate
             dpg.set_value("insp_start", clip.start_time)
             dpg.set_value("insp_dur", clip.duration)
             dpg.set_value("insp_mag_pct", int((clip.magnitude / 32767.0) * 100))
             dpg.set_value("insp_freq", clip.frequency)
             # Show extended props
             if clip.type == "Sine": 
                 dpg.show_item("insp_freq_end")
                 dpg.set_value("insp_freq_end", clip.frequency_end)
             else: dpg.hide_item("insp_freq_end")
        else:
             # Deselected / Deleted -> Hide Window
             dpg.hide_item("win_inspector_floating")

    # --- Palette Drag Source ---

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

        with dpg.window(tag="Main"):
            # Set Main as a fallback drop target without payload type check
            try: dpg.set_item_drop_callback("Main", self.on_drop_receive)
            except: pass
            
            # Global Handlers
            with dpg.handler_registry():
                dpg.add_mouse_wheel_handler(callback=self.on_mouse_wheel)
                dpg.add_key_press_handler(dpg.mvKey_Delete, callback=self.on_key_press)
                dpg.add_key_press_handler(dpg.mvKey_Back, callback=self.on_key_press)

            # Top Bar
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=10)
                dpg.add_text("Fedit DAW", color=(100, 200, 255))
                dpg.add_spacer(width=20)
                dpg.add_button(label="SAVE", callback=lambda: dpg.show_item("dlg_save"))
                dpg.add_button(label="LOAD", callback=lambda: dpg.show_item("dlg_load"))
                dpg.add_spacer(width=20)
                dpg.add_combo(tag="device_combo", width=250)
                dpg.add_button(label="Scan", callback=self.scan_devices)
                dpg.add_button(label="Connect", callback=self.connect_callback)
                dpg.add_text("Status: Disconnected", tag="status_text", color=(255, 100, 100))
                
                dpg.add_spacer(width=50)
                dpg.add_button(tag="btn_play", label="Play", width=80, callback=self.toggle_play)
                dpg.add_text("0.00s", tag="time_display")
                
                dpg.add_spacer(width=20)
                dpg.add_text("TimeBase (s):")
                dpg.add_input_float(tag="input_timebase", width=60, default_value=0.1, step=0, callback=self.on_timebase_change)

            dpg.add_separator()

            with dpg.table(header_row=False, resizable=True, policy=dpg.mvTable_SizingStretchProp, 
                           borders_innerV=True):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=150) # Palette
                dpg.add_table_column() # Timeline
                dpg.add_table_column(width_fixed=True, init_width_or_weight=200) # Inspector

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
                        dpg.add_button(label="+ Add Track", width=100, callback=lambda: self.sequencer.tracks.append(Track(name="New Track")))

                    # Col 2: Timeline
                    with dpg.group(tag="timeline_group"):
                         # Scroll Window
                         # Disable mouse scroll to handle Zoom ourselves without fighting vertical scroll
                         with dpg.child_window(tag="timeline_scroll", horizontal_scrollbar=True, no_scroll_with_mouse=True):
                                 with dpg.drawlist(width=3000, height=1000, tag="timeline_canvas"):
                                    pass

                         # EXPLICIT TARGET to handle drop visualization and acceptance
                         # Removed invalid widget call. Relying on Main Window callback.
                         dpg.add_text("Drop Effects Here", parent="timeline_scroll", color=(100,100,100))
                         
                         with dpg.item_handler_registry(tag="timeline_click_handler"):
                                 dpg.add_item_clicked_handler(callback=self.canvas_click)
                             
                         dpg.bind_item_handler_registry("timeline_canvas", "timeline_click_handler")
                             
                         # Enable Drop
                         try:
                             # We set the callback on the Main window at the end of setup_ui
                             # But we can also try the child window one last time as a backup
                             dpg.set_item_drop_callback("timeline_scroll", self.on_drop_receive)
                         except Exception as e: print(f"Init Warning: {e}")

                    # Col 3: Logic / Log
                    with dpg.child_window(tag="log_panel", width=300):
                        dpg.add_text("System Log")
                        dpg.add_separator()
                        dpg.add_listbox(tag="log_list", num_items=30, width=-1)

        # FINAL BINDING
        try:
            dpg.set_item_drop_callback("Main", self.on_drop_receive)
        except Exception as e: print(f"Main Drop Bind Error: {e}")

    def run(self):
        dpg.create_viewport(title='Fedit DAW 2.0', width=1280, height=800)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("Main", True)
        
        
        # GLOBAL HANDLERS removed in favor of polling in update_loop for reliability
        # with dpg.handler_registry():
        #    dpg.add_key_press_handler(callback=self.on_key_press)
        
        self.scan_devices()

        while dpg.is_dearpygui_running():
            self.update_loop()
            dpg.render_dearpygui_frame()
            
        dpg.destroy_context()

if __name__ == "__main__":
    app = FeditNativeApp()
    app.run()
