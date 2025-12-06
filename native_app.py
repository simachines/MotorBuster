import dearpygui.dearpygui as dpg
import sys
import os
import time
from dataclasses import dataclass, field
import uuid

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
    active_effect_id: int = -1 # Runtime ID

@dataclass
class Track:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Track"
    gain: int = 100
    clips: list[Clip] = field(default_factory=list)

class FeditSequencer:
    def __init__(self):
        self.tracks = [Track(name="Master Force"), Track(name="Rumble A"), Track(name="Rumble B"), Track(name="Aux")]
        self.is_playing = False
        self.current_time = 0.0
        self.last_tick = 0.0
        self.zoom_x = 50.0 # Pixels per second
        self.selected_clip: Clip = None
        self.drag_clip: Clip = None
        self.drag_offset: float = 0.0

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
        self.log_items = []
        
        dpg.create_context()
        self.setup_ui()
        
        # Init Engine
        try:
            self.log("Initializing Haptic Subsystem...")
            engine.init_sdl()
        except Exception as e:
            self.log(f"SDL Init Error: {e}")

    def log(self, message):
        self.log_items.append(message)
        if len(self.log_items) > 50: self.log_items.pop(0)
        if dpg.does_item_exist("log_list"):
            dpg.configure_item("log_list", items=self.log_items)

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
         # Logic to parse name/index and connect
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

    def update_loop(self):
        # Handle Resize
        # dpg.set_item_width("timeline_group", dpg.get_viewport_width() - 250)
        
        if self.sequencer.is_playing:
            now = time.time()
            dt = now - self.sequencer.last_tick
            self.sequencer.last_tick = now
            self.sequencer.current_time += dt
            
            # Loop (e.g. at 30s) or Stop? Let's just run.
            dpg.set_value("time_display", f"{self.sequencer.current_time:.2f}s")
            
            # Trigger Effects
            self.process_sequencer_logic()
            
        self.render_timeline()

    def process_sequencer_logic(self):
        cur_t = self.sequencer.current_time
        
        for t in self.sequencer.tracks:
            for clip in t.clips:
                # Check if inside
                if clip.start_time <= cur_t < (clip.start_time + clip.duration):
                    # Should be playing
                    if clip.active_effect_id == -1:
                        # Start it
                        # Map type to engine call
                        if clip.type == "Sine":
                           # Create Sine
                           eid = engine.start_effect_sine(clip.frequency, clip.magnitude, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                        elif clip.type == "Constant":
                           eid = engine.start_effect_constant(clip.magnitude, int(clip.duration * 1000))
                           clip.active_effect_id = eid
                        # Add frequency sweep logic later?
                else:
                    # Should stop
                    if clip.active_effect_id != -1:
                        # engine.stop_effect(clip.active_effect_id) # Need targeted stop
                        clip.active_effect_id = -1
                        pass

    # --- Rendering ---
    def render_timeline(self):
        # Clear canvas
        dpg.delete_item("timeline_canvas", children_only=True, slot=2)
        
        # Draw Background
        # Tracks
        y_offset = 0
        track_height = 80
        
        for i, track in enumerate(self.sequencer.tracks):
             # Draw lane
             dpg.draw_rectangle([0, y_offset], [2000, y_offset + track_height], color=(30, 30, 35), fill=(30, 30, 35), parent="timeline_canvas")
             dpg.draw_line([0, y_offset + track_height], [2000, y_offset+track_height], color=(50, 50, 50), parent="timeline_canvas")
             
             # Draw Clips
             for clip in track.clips:
                 x_start = clip.start_time * self.sequencer.zoom_x
                 width = clip.duration * self.sequencer.zoom_x
                 
                 color = (100, 150, 255) if clip != self.sequencer.selected_clip else (255, 200, 100)
                 
                 if clip == self.sequencer.selected_clip:
                      dpg.draw_rectangle([x_start, y_offset + 5], [x_start + width, y_offset + track_height - 5], color=(255, 255, 255), thickness=2, parent="timeline_canvas")

                 dpg.draw_rectangle([x_start, y_offset + 5], [x_start + width, y_offset + track_height - 5], color=color, fill=(color[0], color[1], color[2], 100), parent="timeline_canvas")
                 dpg.draw_text([x_start + 5, y_offset + 10], clip.type, size=14, parent="timeline_canvas")

             y_offset += track_height
             
        # Playhead
        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y_offset], color=(255, 50, 50), thickness=2, parent="timeline_canvas")

    def canvas_click(self, sender, app_data):
        # Very basic hit testing for example
        mpos = dpg.get_mouse_pos(local=False) # Global coordinates? Need relative to canvas...
        # DPG mouse handling on canvas is tricky without an item handler. 
        # For this MVP, we rely on the Inspector to modify selected stuff or just 'Add' buttons.
        pass

    def on_drop_effect(self, sender, app_data, user_data):
        # user_data is track index
        effect_type = app_data
        # Add at 0 or current cursor?
        self.sequencer.add_clip(user_data, effect_type, start_time=self.sequencer.current_time)

    def update_selected_clip(self, sender, app_data, user_data):
        # Update param from inspector
        if not self.sequencer.selected_clip: return
        param = user_data
        if param == "freq": self.sequencer.selected_clip.frequency = app_data
        elif param == "mag": self.sequencer.selected_clip.magnitude = app_data
        elif param == "dur": self.sequencer.selected_clip.duration = app_data
        elif param == "start": self.sequencer.selected_clip.start_time = app_data

    # --- Palette Drag Source ---
    def make_drag_source(self, label, type):
        with dpg.group():
             dpg.add_button(label=label, width=100)
             with dpg.drag_payload(drag_data=type, payload_type="EFFECT"):
                 dpg.add_text(f"Effect: {label}")

    def setup_ui(self):
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (20, 20, 25))
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
        dpg.bind_theme(global_theme)

        with dpg.window(tag="Main"):
            # Top Bar
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=10)
                dpg.add_text("Fedit DAW", color=(100, 200, 255))
                dpg.add_spacer(width=20)
                dpg.add_combo(tag="device_combo", width=250)
                dpg.add_button(label="Scan", callback=self.scan_devices)
                dpg.add_button(label="Connect", callback=self.connect_callback)
                dpg.add_text("Status: Disconnected", tag="status_text", color=(255, 100, 100))
                
                dpg.add_spacer(width=50)
                dpg.add_button(tag="btn_play", label="Play", width=80, callback=self.toggle_play)
                dpg.add_text("0.00s", tag="time_display")

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
                    with dpg.group():
                         # Drop Target Container
                         with dpg.child_window(tag="timeline_scroll", horizontal_scrollbar=True):
                             with dpg.group(tag="timeline_group"):
                                 with dpg.drawlist(width=3000, height=1000, tag="timeline_canvas"):
                                     pass 
                             
                             # Drop Target
                             with dpg.drag_payload(parent="timeline_group", drag_data="ignore", payload_type="EFFECT"):
                                 pass 
                                 
                             with dpg.item_handler_registry(tag="timeline_click_handler"):
                                 dpg.add_item_clicked_handler(callback=self.canvas_click)
                             
                             dpg.bind_item_handler_registry("timeline_canvas", "timeline_click_handler")
                             
                             # Enable Drop on the Group
                             dpg.set_item_drop_callback("timeline_group", self.on_drop_receive)

                    # Col 3: Inspector
                    with dpg.child_window(height=-1):
                        dpg.add_text("Inspector")
                        dpg.add_separator()
                        dpg.add_input_float(label="Start (s)", tag="insp_start", callback=self.update_selected_clip, user_data="start")
                        dpg.add_input_float(label="Duration (s)", tag="insp_dur", callback=self.update_selected_clip, user_data="dur")
                        dpg.add_slider_int(label="Magnitude", tag="insp_mag", max_value=32000, callback=self.update_selected_clip, user_data="mag")
                        dpg.add_slider_int(label="Frequency", tag="insp_freq", max_value=100, callback=self.update_selected_clip, user_data="freq")
                        
                        dpg.add_separator()
                        dpg.add_button(label="DELETE CLIP", callback=self.delete_selected_clip, width=-1, show=False, tag="btn_delete")
                        dpg.add_separator()
                        dpg.add_text("Log")
                        dpg.add_listbox(tag="log_list", num_items=10, width=-1)

    def delete_selected_clip(self):
        if self.sequencer.selected_clip:
            self.sequencer.delete_clip(self.sequencer.selected_clip)
            self.sequencer.selected_clip = None
            dpg.hide_item("btn_delete")

    def on_drop_receive(self, sender, app_data):
        self.handle_drop(app_data)

    def handle_drop(self, effect_type):
        # Calculate where we dropped
        # dpg.get_mouse_pos is global. 
        # dpg.get_item_pos("timeline_canvas") is relative to window
        mpos = dpg.get_mouse_pos(local=False) # Global
        cpos = dpg.get_item_pos("timeline_canvas") # Global pos of canvas
        
        rel_x = mpos[0] - cpos[0] + dpg.get_x_scroll("timeline_scroll")
        rel_y = mpos[1] - cpos[1] + dpg.get_y_scroll("timeline_scroll")
        
        # Determine Track
        track_h = 80
        track_idx = int(rel_y // track_h)
        if 0 <= track_idx < len(self.sequencer.tracks):
            # Time
            time_s = max(0.0, rel_x / self.sequencer.zoom_x)
            self.sequencer.add_clip(track_idx, effect_type, time_s)
            self.log(f"Added {effect_type} to Track {track_idx} at {time_s:.2f}s")

    def canvas_click(self, sender, app_data):
        # app_data is (button, ...)
        # We need mouse pos again
        mpos = dpg.get_mouse_pos(local=False)
        cpos = dpg.get_item_pos("timeline_canvas") 
        rel_x = mpos[0] - cpos[0] + dpg.get_x_scroll("timeline_scroll")
        rel_y = mpos[1] - cpos[1] + dpg.get_y_scroll("timeline_scroll")
        
        # Hit Test Clips
        track_h = 80
        track_idx = int(rel_y // track_h)
        time_s = rel_x / self.sequencer.zoom_x
        
        found = False
        if 0 <= track_idx < len(self.sequencer.tracks):
             clip = self.sequencer.get_clip_at(track_idx, time_s)
             if clip:
                 self.sequencer.selected_clip = clip
                 # Update Inspector
                 dpg.set_value("insp_start", clip.start_time)
                 dpg.set_value("insp_dur", clip.duration)
                 dpg.set_value("insp_mag", clip.magnitude)
                 dpg.set_value("insp_freq", clip.frequency)
                 dpg.show_item("btn_delete")
                 found = True
        
        if not found:
            self.sequencer.selected_clip = None
            dpg.hide_item("btn_delete")
            # Seek if clicking emptiness?
            self.sequencer.current_time = max(0.0, time_s)
            self.log(f"Seek to {self.sequencer.current_time:.2f}s")
    
    # --- Rendering ---
    def render_timeline(self):
        dpg.delete_item("timeline_canvas", children_only=True, slot=2)
        
        # Grid/Background
        y_offset = 0
        track_height = 80
        total_w = 3000
        
        for i, track in enumerate(self.sequencer.tracks):
             # Alternating background
             bg_col = (40, 40, 45) if i % 2 == 0 else (35, 35, 40)
             dpg.draw_rectangle([0, y_offset], [total_w, y_offset + track_height], color=bg_col, fill=bg_col, parent="timeline_canvas")
             dpg.draw_line([0, y_offset + track_height], [total_w, y_offset+track_height], color=(60, 60, 60), parent="timeline_canvas")
             dpg.draw_text([10, y_offset + 5], track.name, size=15, color=(200, 200, 200), parent="timeline_canvas")
             
             for clip in track.clips:
                 x_start = clip.start_time * self.sequencer.zoom_x
                 width = clip.duration * self.sequencer.zoom_x
                 
                 # Colors
                 base_col = (100, 150, 255) if clip.type == "Sine" else (255, 100, 100)
                 if clip.type == "Constant": base_col = (100, 255, 100)
                 
                 border_col = (255, 255, 255) if clip == self.sequencer.selected_clip else base_col
                 
                 dpg.draw_rectangle([x_start, y_offset + 20], [x_start + width, y_offset + track_height - 5], color=border_col, thickness=2, fill=(base_col[0], base_col[1], base_col[2], 150), parent="timeline_canvas")
                 dpg.draw_text([x_start + 5, y_offset + 25], clip.type, size=13, parent="timeline_canvas")

             y_offset += track_height
        
        # Playhead
        px = self.sequencer.current_time * self.sequencer.zoom_x
        dpg.draw_line([px, 0], [px, y_offset], color=(255, 50, 50), thickness=2, parent="timeline_canvas")

    def run(self):
        dpg.create_viewport(title='Fedit DAW 2.0', width=1280, height=800)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("Main", True)
        
        # Setup Drop Callback on the WINDOW or GROUP
        # Trick: drag_payload goes on source. dnd_drop_target is strictly a widget wrapper.
        # But we can't wrap the canvas easily if it's a drawlist? 
        # Actually we can wrap the GROUP.
        
        self.scan_devices()

        while dpg.is_dearpygui_running():
            self.update_loop()
            dpg.render_dearpygui_frame()
            
        dpg.destroy_context()

if __name__ == "__main__":
    app = FeditNativeApp()
    app.run()
