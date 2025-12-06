import sdl2
import sdl2.ext
import ctypes
import asyncio
import logging
from dataclasses import dataclass

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FFB_Engine")

@dataclass
class DeviceInfo:
    index: int
    name: str

class HapticController:
    def __init__(self):
        self.haptic = None
        self.joystick = None
        self.effect_id = -1
        
    def init_sdl(self):
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_HAPTIC | sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            logger.error(f"SDL_Init Error: {sdl2.SDL_GetError()}")
            raise Exception("Failed to initialize SDL")
        
    def list_devices(self):
        devices = []
        num_joysticks = sdl2.SDL_NumJoysticks()
        logger.info(f"SDL2 found {num_joysticks} total joysticks.")
        
        for i in range(num_joysticks):
            name = sdl2.SDL_JoystickNameForIndex(i)
            name_str = name.decode('utf-8') if name else "Unknown"
            
            is_gc = sdl2.SDL_IsGameController(i)
            is_haptic = False
            
            # Open joystick temporarily to check for haptic capability
            tmp_joy = sdl2.SDL_JoystickOpen(i)
            if tmp_joy:
                # Fix for PySDL2 returning int instead of pointer
                joy_arg = tmp_joy
                if isinstance(tmp_joy, int):
                    joy_arg = ctypes.cast(tmp_joy, sdl2.LP_SDL_Joystick)
                    
                is_haptic = sdl2.SDL_JoystickIsHaptic(joy_arg)
                sdl2.SDL_JoystickClose(joy_arg)
            
            logger.info(f"Device {i}: '{name_str}' | Haptic: {is_haptic} | GameController: {is_gc}")

            # Accept if it is Haptic OR if it is a GameController (we can try Rumble API)
            if is_haptic or is_gc:
                devices.append(DeviceInfo(index=i, name=name_str))
                
        return devices

    def connect_device(self, index: int):
        self.close_device()
        
        # Open Joystick
        # Force return type to be generic pointer if not set, to handle PySDL2 quirks
        if not hasattr(sdl2.SDL_JoystickOpen, 'restype'):
             sdl2.SDL_JoystickOpen.restype = ctypes.c_void_p

        self.joystick = sdl2.SDL_JoystickOpen(index)
        if not self.joystick:
            logger.error(f"Joystick Open Failed: {sdl2.SDL_GetError()}")
            return False

        # Handle PySDL2 returning int address instead of pointer
        joystick_arg = self.joystick
        if isinstance(self.joystick, int):
            logger.info(f"Casting Joystick handle {self.joystick} (int) to LP_SDL_Joystick")
            # Cast to c_void_p first, then to the expected pointer type if needed, 
            # OR just pass c_void_p if checking for pointer-ness
            # SDL_HapticOpenFromJoystick expects a POINTER(SDL_Joystick)
            # which equates to LP_SDL_Joystick
            joystick_arg = ctypes.cast(self.joystick, sdl2.LP_SDL_Joystick)
        elif isinstance(self.joystick, ctypes.c_void_p):
             # If it returned c_void_p, cast it to strict type
             joystick_arg = ctypes.cast(self.joystick, sdl2.LP_SDL_Joystick)

        # Open Haptic
        self.haptic = sdl2.SDL_HapticOpenFromJoystick(joystick_arg)
        if not self.haptic:
            logger.error(f"Haptic Open Failed: {sdl2.SDL_GetError()}")
            return False

        if sdl2.SDL_HapticRumbleSupported(self.haptic):
             sdl2.SDL_HapticRumbleInit(self.haptic)
        
        # Log Capabilities
        caps = sdl2.SDL_HapticQuery(self.haptic)
        logger.info(f"Device Capabilities: {caps:08x}")
        if caps & sdl2.SDL_HAPTIC_CONSTANT: logger.info("- CONSTANT Supported")
        if caps & sdl2.SDL_HAPTIC_SINE: logger.info("- SINE Supported")
        if caps & sdl2.SDL_HAPTIC_CUSTOM: logger.info("- CUSTOM Supported")

        logger.info(f"Connected to device {index}")
        return True

    def close_device(self):
        if self.effect_id != -1:
            sdl2.SDL_HapticDestroyEffect(self.haptic, self.effect_id)
            self.effect_id = -1
            
        if self.haptic:
            sdl2.SDL_HapticClose(self.haptic)
            self.haptic = None
            
        if self.joystick:
            sdl2.SDL_JoystickClose(self.joystick)
            self.joystick = None

    def play_constant(self, level: int, length: int = 5000):
        if not self.haptic: return
        
        # Ensure Constant supported, or fallback? 
        # Most devices support constant.
        effect = sdl2.SDL_HapticEffect()
        effect.type = sdl2.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl2.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 0
        effect.constant.length = length
        effect.constant.level = level
        
        if self.effect_id == -1:
            self.effect_id = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
        else:
            sdl2.SDL_HapticUpdateEffect(self.haptic, self.effect_id, ctypes.byref(effect))

        sdl2.SDL_HapticRunEffect(self.haptic, self.effect_id, 1)

    def play_sweep(self, start_freq: float, end_freq: float, duration_ms: int, magnitude: int = 10000):
        """Generates and plays a linear frequency sweep using Custom Force. Fallback to Sine if unsupported."""
        if not self.haptic: return

        # Check Capability
        caps = sdl2.SDL_HapticQuery(self.haptic)
        if not (caps & sdl2.SDL_HAPTIC_CUSTOM):
            logger.warning("SDL_HAPTIC_CUSTOM not supported. Falling back to SINE.")
            self.play_sine_fallback(duration_ms, magnitude)
            return

        # 1. Generate Buffer
        import math
        sample_count = duration_ms
        samples = []
        
        for t in range(sample_count):
            time_s = t / 1000.0
            # Linear chirp
            k = (end_freq - start_freq) / (duration_ms / 1000.0)
            phase = 2 * math.pi * (start_freq * time_s + 0.5 * k * time_s * time_s)
            
            val = int(magnitude * math.sin(phase))
            val = max(-32767, min(32767, val))
            samples.append(val)

        if not self.play_custom(samples, duration_ms):
            logger.warning("Custom Effect Failed. Falling back to SINE.")
            self.play_sine_fallback(duration_ms, magnitude)

    def play_custom(self, samples: list, length_ms: int):
        if not self.haptic: return False
            
        count = len(samples)
        data_type = ctypes.c_int16 * count
        data = data_type(*samples)
        
        effect = sdl2.SDL_HapticEffect()
        effect.type = sdl2.SDL_HAPTIC_CUSTOM
        effect.custom.type = sdl2.SDL_HAPTIC_CUSTOM
        effect.custom.direction.type = sdl2.SDL_HAPTIC_CARTESIAN
        effect.custom.direction.dir[0] = 0
        effect.custom.length = length_ms
        effect.custom.period = 0
        effect.custom.channels = 1
        effect.custom.data = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint16)) 
        effect.custom.samples = count
        
        self._current_custom_data = data 

        # Always recreate custom effect to ensure buffer update? 
        # Updating custom data pointers can be tricky. Safer to Destroy and New for Custom buffers.
        if self.effect_id != -1:
            sdl2.SDL_HapticDestroyEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
            
        if self.effect_id == -1:
             logger.error(f"Failed to upload custom effect: {sdl2.SDL_GetError()}")
             return False

        if sdl2.SDL_HapticRunEffect(self.haptic, self.effect_id, 1) != 0:
             logger.error(f"Failed to run effect: {sdl2.SDL_GetError()}")
             return False
        
        return True

    def play_sine_fallback(self, duration_ms: int, magnitude: int):
        """Plays a standard simple Sine wave for devices that don't support custom buffers."""
        logger.info("Playing Standard Sine Effect (Fallback)")
        effect = sdl2.SDL_HapticEffect()
        effect.type = sdl2.SDL_HAPTIC_SINE
        effect.periodic.type = sdl2.SDL_HAPTIC_SINE
        effect.periodic.direction.type = sdl2.SDL_HAPTIC_CARTESIAN
        effect.periodic.period = 50 # 20Hz approx
        effect.periodic.magnitude = magnitude
        effect.periodic.length = duration_ms
        effect.periodic.attack_length = 200
        effect.periodic.fade_length = 200
        
        if self.effect_id != -1:
            sdl2.SDL_HapticDestroyEffect(self.haptic, self.effect_id)
            self.effect_id = -1
            
        self.effect_id = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
        sdl2.SDL_HapticRunEffect(self.haptic, self.effect_id, 1)

    def stop_effect(self):
        if self.haptic and self.effect_id != -1:
            sdl2.SDL_HapticStopEffect(self.haptic, self.effect_id)

    def cleanup(self):
        self.close_device()
        sdl2.SDL_Quit()

    # --- Sequencer Methods ---
    def start_effect_sine(self, freq: int, magnitude: int, duration_ms: int):
        if not self.haptic: return -1
        
        # New Effect
        effect = sdl2.SDL_HapticEffect()
        effect.type = sdl2.SDL_HAPTIC_SINE
        effect.periodic.type = sdl2.SDL_HAPTIC_SINE
        effect.periodic.direction.type = sdl2.SDL_HAPTIC_CARTESIAN
        effect.periodic.period = int(1000 / max(1, freq))
        effect.periodic.magnitude = magnitude
        effect.periodic.length = duration_ms
        effect.periodic.attack_length = 100
        effect.periodic.fade_length = 100
        
        # Upload
        # Note: Ideally we manage a pool of effects. 
        # For this Native MVP, let's just create a new one every time (SDL handles ID limits, usually 16-32)
        # We need to recycle IDs in a real robust engine.
        
        new_id = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
        if new_id != -1:
            sdl2.SDL_HapticRunEffect(self.haptic, new_id, 1)
        return new_id

    def start_effect_constant(self, magnitude: int, duration_ms: int):
        if not self.haptic: return -1
        
        effect = sdl2.SDL_HapticEffect()
        effect.type = sdl2.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl2.SDL_HAPTIC_CARTESIAN
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        new_id = sdl2.SDL_HapticNewEffect(self.haptic, ctypes.byref(effect))
        if new_id != -1:
            sdl2.SDL_HapticRunEffect(self.haptic, new_id, 1)
        return new_id
        
    def stop_effect(self, effect_id: int = -1):
        if not self.haptic: return
        
        if effect_id == -1:
            # Stop All
            sdl2.SDL_HapticStopAll(self.haptic)
        else:
            sdl2.SDL_HapticStopEffect(self.haptic, effect_id)
            sdl2.SDL_HapticDestroyEffect(self.haptic, effect_id) # Cleanup immediately? Or sequence end?
            
    # -----------------------

# Global Instance
engine = HapticController()
