import sys
import os
import ctypes
import time

# Ensure we can find SDL3
os.environ["PATH"] += os.pathsep + r"C:\Users\nishi\AppData\Local\Programs\Python\Python313\Lib\site-packages\sdl3"

from sdl3 import SDL_init as sdl_init
from sdl3 import SDL_haptic as sdl_haptic
from sdl3 import SDL_joystick as sdl_joy
from sdl3 import SDL_error as sdl_error
from sdl3 import SDL_timer as sdl_timer
from sdl3 import SDL_hints

def log(msg):
    print(f"[DEBUG] {msg}")

def check_error():
    err = sdl_error.SDL_GetError()
    if err:
        return err.decode("utf-8")
    return "None"

def main():
    log("Initializing SDL...")
    # Force DirectInput
    SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
    SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI", b"0") # Disable HIDAPI
    SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_RAWINPUT", b"0")
    
    if not sdl_init.SDL_Init(sdl_init.SDL_INIT_HAPTIC | sdl_init.SDL_INIT_JOYSTICK | sdl_init.SDL_INIT_EVENTS):
        log(f"SDL_Init failed: {check_error()}")
        return

    count = ctypes.c_int(0)
    joysticks = sdl_joy.SDL_GetJoysticks(ctypes.byref(count))
    log(f"Found {count.value} joysticks.")
    
    device_id = -1
    haptic = None
    
    for i in range(count.value):
        jid = joysticks[i]
        name = sdl_joy.SDL_GetJoystickNameForID(jid).decode("utf-8")
        log(f"Device {i}: {name} (ID: {jid})")
        
        # Open Joystick temp to check Haptic
        joy = sdl_joy.SDL_OpenJoystick(jid)
        if joy:
            is_haptic = sdl_haptic.SDL_IsJoystickHaptic(joy)
            log(f"  - Haptic: {bool(is_haptic)}")
            sdl_joy.SDL_CloseJoystick(joy)
            
            if is_haptic:
                device_id = jid
                # Prioritize Simagic if found
                if "Simagic" in name or "Alpha" in name:
                    break
    
    if device_id == -1:
        log("No Haptic Device found.")
        return

    log(f"Opening Haptic Device ID: {device_id}")
    joy = sdl_joy.SDL_OpenJoystick(device_id)
    haptic = sdl_haptic.SDL_OpenHapticFromJoystick(joy)
    
    if not haptic:
        log(f"Failed to open haptic: {check_error()}")
        return
        
    log("Haptic Opened Successfully.")
    
    # Caps
    caps = sdl_haptic.SDL_GetHapticFeatures(haptic)
    log(f"Caps: {caps:x}")
    if caps & sdl_haptic.SDL_HAPTIC_CONSTANT:
        log(" - CONSTANT Supported")
    else:
        log(" - CONSTANT NOT Supported")

    # Set Gain
    sdl_error.SDL_ClearError()
    if sdl_haptic.SDL_SetHapticGain(haptic, 100) != 0:
        log(f"SetGain Failed (Ignored): {check_error()}")
    else:
        log("SetGain Success.")
        
    sdl_error.SDL_ClearError()
    # Unpause
    try:
        sdl_haptic.SDL_ResumeHaptic(haptic)
        log("Resumed Haptic.")
    except:
        log("ResumeHaptic not available/failed.")

    # Create Effect (Cartesian Constant X)
    log("Creating CONSTANT Effect (Cartesian X, Level 10000)...")
    effect = sdl_haptic.SDL_HapticEffect()
    ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
    effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
    effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
    effect.constant.direction.dir[0] = 1 # X
    effect.constant.length = sdl_haptic.SDL_HAPTIC_INFINITY
    effect.constant.level = 10000 
    effect.constant.attack_length = 0
    effect.constant.fade_length = 0

    sdl_error.SDL_ClearError()
    eff_id = sdl_haptic.SDL_CreateHapticEffect(haptic, ctypes.byref(effect))
    
    if eff_id == -1:
        log(f"CreateEffect Failed: {check_error()}")
        return
    
    log(f"Effect Created. ID: {eff_id}")
    
    # Run Effect
    log("Running Effect (Iterations=1)...")
    sdl_error.SDL_ClearError()
    res = sdl_haptic.SDL_RunHapticEffect(haptic, eff_id, 1)
    if res == 0:
        log("RunEffect Success! (Wait 2s)")
        time.sleep(2)
    else:
        log(f"RunEffect Failed: {check_error()}")
        log("Retrying with INFINITE iterations...")
        sdl_error.SDL_ClearError()
        res = sdl_haptic.SDL_RunHapticEffect(haptic, eff_id, 4294967295)
        if res == 0:
             log("RunEffect (Infinite) Success! (Wait 2s)")
             time.sleep(2)
        else:
             log(f"RunEffect (Infinite) Failed: {check_error()}")
             
             # Try Polar
             log("Retrying with POLAR Direction...")
             sdl_haptic.SDL_DestroyHapticEffect(haptic, eff_id)
             
             effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_POLAR
             effect.constant.direction.dir[0] = 9000 # 90 deg
             eff_id = sdl_haptic.SDL_CreateHapticEffect(haptic, ctypes.byref(effect))
             
             sdl_error.SDL_ClearError()
             res = sdl_haptic.SDL_RunHapticEffect(haptic, eff_id, 1)
             if res == 0:
                 log("RunEffect (Polar) Success! (Wait 2s)")
                 time.sleep(2)
             else:
                 log(f"RunEffect (Polar) Failed: {check_error()}")

    log("Stopping...")
    sdl_haptic.SDL_StopHapticEffects(haptic)
    sdl_haptic.SDL_CloseHaptic(haptic)
    sdl_joy.SDL_CloseJoystick(joy)
    log("Done.")

if __name__ == "__main__":
    main()
