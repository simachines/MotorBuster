import sys
import os
import time
import ctypes

# Setup Path so we can find server.ffb_engine
# (Same logic as native_app.py to find dependencies)
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    os.environ["PYSDL2_DLL_PATH"] = base_path
else:
    base_path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.join(base_path, '.dependencies'))

from server.ffb_engine import engine, sdl_haptic

def run_test():
    print("--- FFB Diagnostic Test ---")
    
    print("1. Initializing SDL...")
    engine.init_sdl()
    
    print("2. Listing Devices...")
    devices = engine.list_devices()
    if not devices:
        print("ERROR: No Haptic/Gamepad Devices found.")
        return
        
    print(f"Found {len(devices)} devices.")
    for d in devices:
        print(f" - [{d.index}] {d.name}")
        
    target_idx = 0
    print(f"\n3. Connecting to Device {target_idx}...")
    if not engine.connect_device(target_idx):
        print("ERROR: Failed to connect.")
        return
        
    print("Device Connected.")
    
    # Force Gain/Autocenter again just to be sure (diagnostic)
    try:
        print("   Setting Gain to 100...")
        sdl_haptic.SDL_SetHapticGain(engine.haptic, 100)
    except Exception as e:
        print(f"   WARN: SetGain failed: {e}")
        
    try:
        print("   Disabling Autocenter...")
        sdl_haptic.SDL_SetHapticAutocenter(engine.haptic, 0)
    except Exception as e:
        print(f"   WARN: SetAutocenter failed: {e}")

    print("\n4. Playing CONSTANT Force (Left/Neg) for 1s...")
    # constant force -10000
    engine.play_constant(-16000, 1000)
    time.sleep(1.2)
    
    print("5. Playing CONSTANT Force (Right/Pos) for 1s...")
    # constant force +10000
    engine.play_constant(16000, 1000)
    time.sleep(1.2)
    

    
    print("6. Playing SINE Wave for 2s...")
    # Sine
    desc = {
        "type": "sine",
        "frequency_hz": 5.0,
        "magnitude": 16000,
        "length_ms": 2000,
        "direction_mode": "cartesian", # Test strict cartesian
        "direction": {"angle": 0}, # Should be ignored/mapped to X
    }
    
    eid = engine.play_descriptor(desc)
    if eid == -1:
        print("ERROR: Failed to play Sine descriptor.")
    else:
        print(f"   Sine Effect ID: {eid} - Playing...")
        print(f"   Sine Effect ID: {eid} - Playing...")
        time.sleep(2.2)

    print("\n7. Playing SINE Wave (Polar) for 2s...")
    desc_polar = {
        "type": "sine",
        "frequency_hz": 5.0,
        "magnitude": 16000,
        "length_ms": 2000,
        "direction_mode": "polar",
        "direction": {"angle": 9000, "radius": 1}, # 90 degrees
    }
    eid_p = engine.play_descriptor(desc_polar)
    if eid_p == -1:
        print("ERROR: Failed to play Polar Sine descriptor.")
    else:
        print(f"   Polar Sine Effect ID: {eid_p} - Playing...")
        time.sleep(2.2)
        
    print("\n8. Stopping All...")
    engine.stop_effect()
    engine.close_device()
    print("Test Complete.")

if __name__ == "__main__":
    try:
         run_test()
    except Exception as e:
         print(f"CRITICAL ERROR: {e}")
         import traceback
         traceback.print_exc()
