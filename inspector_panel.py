import uuid
import dearpygui.dearpygui as dpg

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
             self.container = dpg.window(label=label, tag=self.tag_tab, width=300, height=400, pos=pos)
        else:
             self.container = dpg.tab(label=label, tag=self.tag_tab, parent=parent, closable=(clip is not None))
             
        with self.container:
             dpg.add_text("Properties", tag=self.tag_title)
             dpg.add_separator()
             
             # Fields
             dpg.add_input_text(label="Name", tag=f"insp_name_{self.id}", callback=self.on_change, user_data="name")
             dpg.add_input_float(label="Start (s)", tag=self.tag_start, callback=self.on_change, user_data="start")
             dpg.add_input_float(label="Duration (s)", tag=self.tag_dur, callback=self.on_change, user_data="dur")
             dpg.add_slider_int(label="Magnitude %", tag=self.tag_mag, max_value=100, callback=self.on_change, user_data="mag")
             
             dpg.add_separator()
             dpg.add_input_int(label="Frequency (Hz)", tag=self.tag_freq, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq")
             dpg.add_slider_int(label="Phase (deg)", tag=self.tag_phase, min_value=0, max_value=360, callback=self.on_change, user_data="phase")
             
             dpg.add_checkbox(label="Enable Sweep", tag=self.tag_sweep, callback=self.on_change, user_data="sweep")
             dpg.add_input_int(label="End Freq (Hz)", tag=self.tag_freq_end, min_value=1, max_value=5000, step=1, step_fast=10, callback=self.on_change, user_data="freq_end")
             
             dpg.add_separator()
    
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
        
        if not dpg.is_item_active(f"insp_name_{self.id}"):
            dpg.set_value(f"insp_name_{self.id}", clip.name)
        
        safe_set(self.tag_start, clip.start_time)
        safe_set(self.tag_dur, clip.duration)
        safe_set(self.tag_mag, int((clip.magnitude / 32767.0) * 100))
        # Removed duplicate tag_mag set
        safe_set(self.tag_freq, clip.frequency)
        safe_set(self.tag_phase, getattr(clip, 'start_phase', 0))
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
        if param == "name": 
            clip.name = app_data
            # Update Title
            label = clip.name
            dpg.configure_item(self.tag_tab, label=label)
            
        elif param == "start": clip.start_time = app_data
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
        if clip:
            self.app.create_floating_inspector(clip)

