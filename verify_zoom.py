
import dearpygui.dearpygui as dpg

# Setup DPG context needed for app init
dpg.create_context()

# Mock dpg functions used in on_mouse_wheel
def mock_is_key_down(key):
    # Simulate Ctrl key being down
    return True 

def mock_get_item_height(item):
    return 1000

def mock_set_y_scroll(item, val):
    pass
    
def mock_is_item_hovered(item):
    return True

# Monkeypatch
dpg.is_key_down = mock_is_key_down
dpg.get_item_height = mock_get_item_height
dpg.set_y_scroll = mock_set_y_scroll
dpg.is_item_hovered = mock_is_item_hovered

# Import App
from native_app import FeditNativeApp

print("Initializing App...")
app = FeditNativeApp()
# We don't need to call setup_ui to test on_mouse_wheel logic, as it operates on app.sequencer.zoom_x
# But we need app.sequencer initialized (which is done in __init__)

initial_zoom = app.sequencer.zoom_x
print(f"Initial Zoom: {initial_zoom}")

# simulate Scroll Up (Zoom In)
print("Simulating Scroll UP (+10)")
app.on_mouse_wheel(None, 10.0)
print(f"New Zoom: {app.sequencer.zoom_x}")

if app.sequencer.zoom_x > initial_zoom:
    print("VERIFICATION PASS: Zoom increased.")
else:
    print("VERIFICATION FAIL: Zoom did not increase.")
    
# Check Clamp
print("Checking Clamp (Zoom In a lot)")
for i in range(100):
    app.on_mouse_wheel(None, 10.0)
    
print(f"Zoom after 100 scrolls: {app.sequencer.zoom_x}")
if app.sequencer.zoom_x <= 50000.0:
    print("VERIFICATION PASS: Zoom clamped to max.")
else:
    print(f"VERIFICATION FAIL: Zoom {app.sequencer.zoom_x} exceeded max.")

dpg.destroy_context()
