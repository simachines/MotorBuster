import threading
import time
import os
import sys
import ctypes

# Log to file to ensure we see output
def log(msg):
    with open("debug_thread.log", "a") as f:
        f.write(msg + "\n")
    print(msg)

log("Script starting...")

base_path = os.path.dirname(os.path.abspath(__file__))
dep_path = os.path.join(base_path, '.dependencies')
sys.path.append(dep_path)
os.environ["PATH"] += os.pathsep + dep_path # Help Windows find DLL

log(f"Added {dep_path} to sys.path and PATH")

try:
    from sdl3 import SDL_init as sdl_init
    from sdl3 import SDL_events as sdl_events
    from sdl3 import SDL_joystick as sdl_joy
    from sdl3 import SDL_timer as sdl_timer
    from sdl3 import SDL_error as sdl_error
    log("Imports successful")
except Exception as e:
    log(f"Import failed: {e}")
    sys.exit(1)

def _sdl_error():
    err = sdl_error.SDL_GetError()
    return err.decode("utf-8") if err else ""

def input_thread(joy):
    log("Background thread started")
    for i in range(10):
        # Try pumping events on this thread
        sdl_events.SDL_PumpEvents()
        
        axis = sdl_joy.SDL_GetJoystickAxis(joy, 0)
        log(f"Thread Axis 0: {axis}")
        
        time.sleep(0.1)
    log("Background thread finished")

def main():
    log("Initializing SDL...")
    if not sdl_init.SDL_Init(sdl_init.SDL_INIT_JOYSTICK | sdl_init.SDL_INIT_EVENTS):
        log(f"Init failed: {_sdl_error()}")
        return

    count = ctypes.c_int(0)
    joysticks = sdl_joy.SDL_GetJoysticks(ctypes.byref(count))
    log(f"Found {count.value} joysticks")
    
    if count.value == 0:
        log("No joysticks found - cannot test")
        return
        
    ids = ctypes.cast(joysticks, ctypes.POINTER(sdl_joy.SDL_JoystickID))
    joy_id = ids[0]
    joy = sdl_joy.SDL_OpenJoystick(joy_id)
    
    if not joy:
        log("Failed to open joystick")
        return

    log("Joystick opened. Starting thread...")

    t = threading.Thread(target=input_thread, args=(joy,))
    t.start()
    
    # Main thread work
    log("Main thread working...")
    for i in range(2):
        log("Main thread tick")
        time.sleep(1.0)
        
    t.join()
    
    sdl_joy.SDL_CloseJoystick(joy)
    sdl_init.SDL_Quit()
    log("Done")

if __name__ == "__main__":
    main()
