import sys
import os
import time

# Setup Path
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
    os.environ["PYSDL2_DLL_PATH"] = base_path
else:
    base_path = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(os.path.join(base_path, '.dependencies'))

from server.ffb_engine import engine
from sdl3 import SDL_haptic as sdl_haptic
import ctypes

def test_hardware_sine():
    print("=== Testing Hardware SINE Effect ===\n")
    
    print("1. Initializing SDL...")
    engine.init_sdl()
    
    print("2. Connecting to device...")
    devices = engine.list_devices()
    if not devices:
        print("ERROR: No devices found")
        return
    
    engine.connect_device(0)
    print(f"Connected to: {devices[0].name}\n")
    
    # Create a real hardware SINE effect (no software oscillator)
    print("3. Creating HARDWARE SINE effect (10 Hz, 2 seconds)...")
    effect = sdl_haptic.SDL_HapticEffect()
    ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
    
    effect.type = sdl_haptic.SDL_HAPTIC_SINE
    effect.periodic.type = sdl_haptic.SDL_HAPTIC_SINE
    
    # Try Cartesian first
    effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
    effect.periodic.direction.dir[0] = 1  # X-axis
    effect.periodic.direction.dir[1] = 0
    effect.periodic.direction.dir[2] = 0
    
    effect.periodic.period = 100  # 10 Hz (1000ms / 10 = 100ms period)
    effect.periodic.magnitude = 16000
    effect.periodic.length = 2000  # 2 seconds
    effect.periodic.attack_length = 0
    effect.periodic.fade_length = 0
    effect.periodic.phase = 0
    
    effect_id = sdl_haptic.SDL_CreateHapticEffect(engine.haptic, ctypes.byref(effect))
    
    if effect_id == -1:
        print("[FAIL] Cartesian SINE creation FAILED")
        print("   Trying POLAR direction instead...\n")
        
        # Try Polar
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_POLAR
        effect.periodic.direction.dir[0] = 9000  # 90 degrees
        effect.periodic.direction.dir[1] = 0
        effect.periodic.direction.dir[2] = 0
        effect.periodic.period = 100
        effect.periodic.magnitude = 16000
        effect.periodic.length = 2000
        effect.periodic.attack_length = 0
        effect.periodic.fade_length = 0
        effect.periodic.phase = 0
        
        effect_id = sdl_haptic.SDL_CreateHapticEffect(engine.haptic, ctypes.byref(effect))
        
        if effect_id == -1:
            print("[FAIL] POLAR SINE creation also FAILED")
            print("\nConclusion: Simagic does NOT support hardware SINE effects")
            print("Software oscillator is required.")
            engine.close_device()
            return
        else:
            print("[OK] POLAR SINE created successfully (ID: {})".format(effect_id))
    else:
        print("[OK] Cartesian SINE created successfully (ID: {})".format(effect_id))
    
    # Try to run it
    print("\n4. Running hardware SINE effect...")
    result = sdl_haptic.SDL_RunHapticEffect(engine.haptic, effect_id, 1)
    
    if result == 0:
        print("[OK] RunEffect SUCCESS!")
        print("   You should feel a 10 Hz sine wave for 2 seconds...")
        time.sleep(2.5)
        print("\n[OK] Hardware SINE works on your wheelbase!")
    else:
        print("[FAIL] RunEffect FAILED")
        print("   Hardware SINE doesn't work properly on Simagic")
        print("   Software oscillator is required.")
    
    # Cleanup
    sdl_haptic.SDL_StopHapticEffects(engine.haptic)
    sdl_haptic.SDL_DestroyHapticEffect(engine.haptic, effect_id)
    engine.close_device()
    print("\nTest complete.")

if __name__ == "__main__":
    try:
        test_hardware_sine()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
