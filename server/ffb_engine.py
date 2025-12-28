import ctypes
import asyncio
import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

# PyInstaller windowed builds can null out stdout/stderr; SDL's logger expects them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = sys.stdout

from sdl3 import SDL_error as sdl_error
from sdl3 import SDL_gamepad as sdl_gp
from sdl3 import SDL_haptic as sdl_haptic
from sdl3 import SDL_init as sdl_init
from sdl3 import SDL_joystick as sdl_joy
from sdl3 import SDL_stdinc as sdl_std

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FFB_Engine")

# Maps user-friendly effect keys to SDL enums for periodic/condition effects
PERIODIC_TYPES: Dict[str, int] = {
    "sine": sdl_haptic.SDL_HAPTIC_SINE,
    "square": sdl_haptic.SDL_HAPTIC_SQUARE,
    "triangle": sdl_haptic.SDL_HAPTIC_TRIANGLE,
    "sawtoothup": sdl_haptic.SDL_HAPTIC_SAWTOOTHUP,
    "sawtoothdown": sdl_haptic.SDL_HAPTIC_SAWTOOTHDOWN,
}

CONDITION_TYPES: Dict[str, int] = {
    "spring": sdl_haptic.SDL_HAPTIC_SPRING,
    "damper": sdl_haptic.SDL_HAPTIC_DAMPER,
    "inertia": sdl_haptic.SDL_HAPTIC_INERTIA,
    "friction": sdl_haptic.SDL_HAPTIC_FRICTION,
}

DIRECTION_MODES: Dict[str, int] = {
    "polar": sdl_haptic.SDL_HAPTIC_POLAR,
    "cartesian": sdl_haptic.SDL_HAPTIC_CARTESIAN,
    "spherical": sdl_haptic.SDL_HAPTIC_SPHERICAL,
}


def _sdl_error() -> str:
    """Return the current SDL error string."""
    err = sdl_error.SDL_GetError()
    return err.decode("utf-8") if err else "<unknown>"

@dataclass
class DeviceInfo:
    index: int
    name: str

class HapticController:
    def __init__(self):
        self.haptic = None
        self.joystick = None
        self.gamepad = None
        self.effect_id = -1
        self._device_ids: list[int] = []
        self._current_custom_data = None
        self.use_software_sine = True  # Toggle: True = Software Oscillator, False = Hardware SINE
        self.axis_baseline: dict[str, int] = {}
        self.preferred_axis_key: Optional[str] = None
        
    def init_sdl(self):
        flags = (
            sdl_init.SDL_INIT_VIDEO
            | sdl_init.SDL_INIT_JOYSTICK
            | sdl_init.SDL_INIT_HAPTIC
            | sdl_init.SDL_INIT_GAMEPAD
            | sdl_init.SDL_INIT_EVENTS
        )
        
        # Critical for FFB when window not focused or using script
        # Simagic/Asetek via DirectInput often REQUIRE Foreground Exclusive.
        from sdl3 import SDL_hints
        SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_THREAD", b"1")
        
        # FORCE DirectInput (Fixes 'Unknown' errors on Simagic/Moza/Fanatec)
        SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI", b"0")
        SDL_hints.SDL_SetHint(b"SDL_JOYSTICK_RAWINPUT", b"0")

        if not sdl_init.SDL_Init(flags):
            logger.error(f"SDL_Init Error: {_sdl_error()}")
            raise Exception("Failed to initialize SDL3")
        
    def list_devices(self):
        devices: list[DeviceInfo] = []
        count = ctypes.c_int(0)
        joystick_ids = sdl_joy.SDL_GetJoysticks(ctypes.byref(count))
        discovered: list[int] = []

        if joystick_ids:
            ids_array = ctypes.cast(joystick_ids, ctypes.POINTER(sdl_joy.SDL_JoystickID))
            discovered = [int(ids_array[i]) for i in range(count.value)]
            sdl_std.SDL_free(joystick_ids)

        logger.info(f"SDL3 found {len(discovered)} total joysticks.")
        self._device_ids = discovered

        for idx, joy_id in enumerate(discovered):
            name_ptr = sdl_joy.SDL_GetJoystickNameForID(joy_id)
            name_str = name_ptr.decode("utf-8") if name_ptr else "Unknown"

            is_gc = bool(sdl_gp.SDL_IsGamepad(joy_id))
            is_haptic = False

            tmp_joy = sdl_joy.SDL_OpenJoystick(joy_id)
            if tmp_joy:
                is_haptic = bool(sdl_haptic.SDL_IsJoystickHaptic(tmp_joy))
                sdl_joy.SDL_CloseJoystick(tmp_joy)

            logger.info(
                f"Device {idx} (id={joy_id}): '{name_str}' | Haptic: {is_haptic} | Gamepad: {is_gc}"
            )

            if is_haptic or is_gc:
                devices.append(DeviceInfo(index=idx, name=name_str))

        return devices

    def connect_device(self, index: int):
        self.close_device()

        if not self._device_ids:
            self.list_devices()

        if index < 0 or index >= len(self._device_ids):
            logger.error(f"Invalid device index {index}")
            return False

        joy_id = self._device_ids[index]
        self.effect_id = -1
        self._oscillator_thread = None
        self._stop_oscillator_event = threading.Event()
        self._osc_params = {}
        self._oscillator_active = False  # Track if oscillator should run at full speed

        self.joystick = sdl_joy.SDL_OpenJoystick(joy_id)
        if not self.joystick:
            logger.error(f"Joystick Open Failed: {_sdl_error()}")
            return False

        # Open gamepad handle if supported so we can read standardized axes
        if sdl_gp.SDL_IsGamepad(joy_id):
            self.gamepad = sdl_gp.SDL_OpenGamepad(joy_id)
            if not self.gamepad:
                logger.warning(f"Gamepad open failed for id {joy_id}: {_sdl_error()}")
        else:
            self.gamepad = None

        # Default axis preference: gamepad LEFTX if available, else joystick axis 0
        if self.gamepad:
            self.preferred_axis_key = "gp_0"  # LEFTX
        else:
            self.preferred_axis_key = "joy_0"

        self.haptic = sdl_haptic.SDL_OpenHapticFromJoystick(self.joystick)
        if not self.haptic:
            logger.error(f"Haptic Open Failed: {_sdl_error()}")
            self.close_device()
            return False

        # Explicitly set Gain to Max and Disable Autocenter
        try:
             sdl_haptic.SDL_SetHapticGain(self.haptic, 100)
             # sdl_haptic.SDL_SetHapticAutocenter(self.haptic, 0) # Causes error on Simagic
        except:
             logger.warning("Could not set Gain on this device.")
             sdl_error.SDL_ClearError()
        
        sdl_error.SDL_ClearError() # Ensure clean slate before Unpause/Resume

        # Explicitly Unpause (Important for some DD bases)
        try:
             sdl_haptic.SDL_ResumeHaptic(self.haptic)
        except AttributeError:
             pass # Fallback if function missing



        # Removed SDL_InitHapticRumble to prevent locking device into simple mode
        # if sdl_haptic.SDL_HapticRumbleSupported(self.haptic):
        #     sdl_haptic.SDL_InitHapticRumble(self.haptic)

        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        logger.info(f"Device Capabilities: {caps:08x}")
        if caps & sdl_haptic.SDL_HAPTIC_CONSTANT:
            logger.info("- CONSTANT Supported")
        if caps & sdl_haptic.SDL_HAPTIC_SINE:
            logger.info("- SINE Supported")
        if caps & sdl_haptic.SDL_HAPTIC_CUSTOM:
            logger.info("- CUSTOM Supported")

        logger.info(f"Connected to device idx={index} id={joy_id}")
        return True

    def close_device(self):
        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1
            
        if self.haptic:
            self._stop_oscillator()
            sdl_haptic.SDL_CloseHaptic(self.haptic)
            self.haptic = None
            
        if self.gamepad:
            sdl_gp.SDL_CloseGamepad(self.gamepad)
            self.gamepad = None

        if self.joystick:
            sdl_joy.SDL_CloseJoystick(self.joystick)
            self.joystick = None

        self.axis_baseline.clear()
        self.preferred_axis_key = None

    def play_constant(self, level: int, length: int = 5000):
        if not self.haptic: return
        
        # Ensure Constant supported, or fallback? 
        # Most devices support constant.
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 1
        effect.constant.direction.dir[1] = 0
        effect.constant.direction.dir[2] = 0
        effect.constant.length = length
        effect.constant.level = level
        
        if self.effect_id == -1:
            self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        else:
            sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(effect))

        if self.effect_id != -1:
            sdl_error.SDL_ClearError()
            if sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1) != 0:
                 logger.error(f"Failed to run constant effect: {_sdl_error()}")

    def play_rumble(self, strength: float, length: int):
        if not self.haptic: return
        if sdl_haptic.SDL_HapticRumbleSupported(self.haptic):
            sdl_haptic.SDL_HapticRumbleInit(self.haptic)
            sdl_haptic.SDL_HapticRumblePlay(self.haptic, strength, length)
            logger.info("Playing Rumble...")
            return True
        else:
            logger.warning("Rumble Not Supported.")
            return False

    def start_effect_sweep(self, start_freq: float, end_freq: float, duration_ms: int, magnitude: int = 10000):
        """Generates and plays a linear frequency sweep using Custom Force. Fallback to Sine if unsupported."""
        if not self.haptic: return -1

        # Check Capability
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        if not (caps & sdl_haptic.SDL_HAPTIC_CUSTOM):
            logger.warning("SDL_HAPTIC_CUSTOM not supported. Falling back to SINE.")
            return self.start_effect_sine(start_freq, magnitude, duration_ms)

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
            return self.start_effect_sine(start_freq, magnitude, duration_ms)
            
        return self.effect_id

    def play_custom(self, samples: list, length_ms: int):
        if not self.haptic: return False
            
        count = len(samples)
        data_type = ctypes.c_int16 * count
        data = data_type(*samples)
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CUSTOM
        effect.custom.type = sdl_haptic.SDL_HAPTIC_CUSTOM
        effect.custom.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.custom.direction.dir[0] = 1
        effect.custom.direction.dir[1] = 0
        effect.custom.direction.dir[2] = 0
        effect.custom.length = length_ms
        effect.custom.period = 0
        effect.custom.channels = 1
        effect.custom.data = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint16)) 
        effect.custom.samples = count
        
        self._current_custom_data = data 

        # Always recreate custom effect to ensure buffer update? 
        # Updating custom data pointers can be tricky. Safer to Destroy and New for Custom buffers.
        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
            
        if self.effect_id == -1:
            logger.error(f"Failed to upload custom effect: {_sdl_error()}")
            return False

        sdl_error.SDL_ClearError()
        if sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1) != 0:
            logger.error(f"Failed to run effect: {_sdl_error()}")
            return False
        
        return True

    def _apply_baseline(self, key: str, value: int) -> int:
        if key not in self.axis_baseline:
            self.axis_baseline[key] = value
        return value - self.axis_baseline.get(key, 0)

    def get_axis_value(self, axis_idx: int = 0) -> Optional[int]:
        """Read wheel position with baseline offset removal. Defaults to preferred X axis."""
        if not self.joystick:
            return None
        try:
            try:
                sdl_gp.SDL_UpdateGamepads()
            except Exception:
                pass
            try:
                sdl_joy.SDL_UpdateJoysticks()
            except Exception:
                pass

            axis_map = [
                sdl_gp.SDL_GAMEPAD_AXIS_LEFTX,
                sdl_gp.SDL_GAMEPAD_AXIS_LEFTY,
                sdl_gp.SDL_GAMEPAD_AXIS_RIGHTX,
                sdl_gp.SDL_GAMEPAD_AXIS_RIGHTY,
            ]

            # Decide which axis key to use
            key = None
            if self.preferred_axis_key:
                key = self.preferred_axis_key
            elif axis_idx >= 0:
                key = f"gp_{axis_idx}" if self.gamepad else f"joy_{axis_idx}"
            else:
                key = "gp_0" if self.gamepad else "joy_0"

            # Read from gamepad if key matches
            if key.startswith("gp_") and self.gamepad:
                idx = int(key.split("_")[1])
                if 0 <= idx < len(axis_map):
                    raw = int(sdl_gp.SDL_GetGamepadAxis(self.gamepad, axis_map[idx]))
                    return self._apply_baseline(key, raw)

            # Fallback to joystick
            axis_count = sdl_joy.SDL_GetNumJoystickAxes(self.joystick)
            if axis_count <= 0:
                return None
            idx = int(key.split("_")[1]) if key and key.startswith("joy_") else axis_idx
            if idx < 0 or idx >= axis_count:
                idx = 0
            raw = int(sdl_joy.SDL_GetJoystickAxis(self.joystick, idx))
            return self._apply_baseline(f"joy_{idx}", raw)
        except Exception:
            return None

    def play_sine_fallback(self, duration_ms: int, magnitude: int):
        """Plays a standard simple Sine wave for devices that don't support custom buffers."""
        logger.info("Playing Standard Sine Effect (Fallback)")
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.periodic.period = 50 # 20Hz approx
        effect.periodic.magnitude = magnitude
        effect.periodic.length = duration_ms
        effect.periodic.attack_length = 200
        effect.periodic.fade_length = 200
        
        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1
            
        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1)

    def cleanup(self):
        self.close_device()
        sdl_init.SDL_Quit()

    # --- Helpers for new effects ---
    @staticmethod
    def _clamp_short(value: float) -> int:
        return int(max(-32768, min(32767, value)))

    def _make_direction(self, mode: str, values: Optional[Dict[str, float]] = None):
        # FORCE CARTESIAN CONVERSION (Fix for Simagic/Moza/Fanatec DirectInput Unknown Error)
        # Even if "polar" is requested, we calculate the vector and send Cartesian.
        if mode == "polar" or mode == "cartesian":
            direction = sdl_haptic.SDL_HapticDirection()
            direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
            
            x, y = 0, 0
            if mode == "cartesian":
                 x = values.get("x", 1)
                 y = values.get("y", 0)
            else:
                 # Polar to Cartesian
                 # SDL 0=North, 90=East. 
                 angle = values.get("angle", 0) / 100.0
                 rad = math.radians(angle)
                 # Map to typical Wheel X-axis (Left/Right)
                 # Sin(angle) gives X component (assuming 0=North, 90=East)
                 x = math.sin(rad)
                 y = -math.cos(rad)
            
            # STRICT X-AXIS ONLY (Fix for Single-Axis Wheels)
            # We ignore Y/Z components completely to avoid "Invalid Axis" errors.
            direction.dir[0] = 1 # Force Positive X
            direction.dir[1] = 0
            direction.dir[2] = 0
            
            return direction

        if mode == "spherical":
            direction.type = sdl_haptic.SDL_HAPTIC_SPHERICAL
            comps = (values.get("yaw", 0), values.get("pitch", 0), values.get("distance", 1))
            for i, comp in enumerate(comps):
                direction.dir[i] = int(comp)
            return direction

        direction.type = DIRECTION_MODES.get(mode, sdl_haptic.SDL_HAPTIC_POLAR)
        comps = (values.get("angle", 0), values.get("radius", 1), 0)
        for i, comp in enumerate(comps):
            direction.dir[i] = int(comp)
        return direction

    def _apply_envelope(self, target, envelope: Dict[str, float]):
        target.attack_length = int(envelope.get("attack_length", 0))
        target.attack_level = int(envelope.get("attack_level", 0))
        target.fade_length = int(envelope.get("fade_length", 0))
        target.fade_level = int(envelope.get("fade_level", 0))

    def _period_from_frequency(self, freq_hz: float, period_override: Optional[int] = None) -> int:
        if period_override and period_override > 0:
            return max(1, int(period_override))
        safe_freq = max(0.1, float(freq_hz))
        return max(1, min(1000, int(round(1000.0 / safe_freq))))

    def _build_periodic(self, effect, kind: str, desc: Dict) -> None:
        periodic_type = PERIODIC_TYPES.get(kind, sdl_haptic.SDL_HAPTIC_SINE)
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        envelope = desc.get("envelope", {})

        effect.type = periodic_type
        effect.periodic.type = periodic_type
        effect.periodic.direction = direction
        effect.periodic.period = self._period_from_frequency(desc.get("frequency_hz", 50.0), desc.get("period_ms"))
        effect.periodic.magnitude = self._clamp_short(desc.get("magnitude", 20000))
        effect.periodic.length = sdl_haptic.SDL_HAPTIC_INFINITY
        effect.periodic.phase = int(desc.get("phase", 0))
        self._apply_envelope(effect.periodic, envelope)

    def _build_constant(self, effect, desc: Dict) -> None:
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        envelope = desc.get("envelope", {})

        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction = direction
        effect.constant.length = sdl_haptic.SDL_HAPTIC_INFINITY
        effect.constant.level = self._clamp_short(desc.get("magnitude", 20000))
        self._apply_envelope(effect.constant, envelope)

    def _build_ramp(self, effect, desc: Dict) -> None:
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        envelope = desc.get("envelope", {})

        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction = direction
        effect.ramp.length = sdl_haptic.SDL_HAPTIC_INFINITY
        effect.ramp.start = self._clamp_short(desc.get("start_mag", -20000))
        effect.ramp.end = self._clamp_short(desc.get("end_mag", 20000))
        self._apply_envelope(effect.ramp, envelope)

    def _build_condition(self, effect, kind: str, desc: Dict) -> None:
        cond_type = CONDITION_TYPES[kind]
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        axes = desc.get("axes", {})

        effect.type = cond_type
        effect.condition.type = cond_type
        effect.condition.direction = direction
        effect.condition.length = sdl_haptic.SDL_HAPTIC_INFINITY

    def _build_left_right(self, effect, desc: Dict) -> None:
        effect.type = sdl_haptic.SDL_HAPTIC_LEFTRIGHT
        effect.leftright.length = sdl_haptic.SDL_HAPTIC_INFINITY
        effect.leftright.large_magnitude = self._clamp_short(desc.get("large_magnitude", desc.get("magnitude", 20000)))
        effect.leftright.small_magnitude = self._clamp_short(desc.get("small_magnitude", desc.get("magnitude", 12000)))

    def build_effect_from_descriptor(self, desc: Dict) -> Optional[sdl_haptic.SDL_HapticEffect]:
        if not self.haptic:
            return None

        kind = (desc.get("type") or desc.get("effect") or "sine").lower()
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))

        if kind in PERIODIC_TYPES:
            self._build_periodic(effect, kind, desc)
        elif kind == "constant":
            self._build_constant(effect, desc)
        elif kind == "ramp":
            self._build_ramp(effect, desc)
        elif kind in CONDITION_TYPES:
            self._build_condition(effect, kind, desc)
        elif kind in {"leftright", "left_right"}:
            self._build_left_right(effect, desc)
        elif kind == "custom":
            # Custom requires data buffer from caller; reuse play_custom when invoked directly.
            return None
        else:
            logger.warning(f"Unsupported effect type '{kind}', falling back to sine")
            self._build_periodic(effect, "sine", desc)

        # Check capabilities and potential fallback
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        is_polar = desc.get("direction_mode", "polar").lower() == "polar"
        
        # If user requests Polar, but device lacks Polar support and has Cartesian support, convert.
        if is_polar and not (caps & sdl_haptic.SDL_HAPTIC_POLAR) and (caps & sdl_haptic.SDL_HAPTIC_CARTESIAN):
            # Convert Polar to Cartesian
            # SDL Polar: 0 = North, 90 = East (Clockwise)
            # SDL Cartesian: X=Right, Y=Down? Usually X=1 is East.
            # 0 deg (North) -> X=0, Y=-1 (if Y is Down) or Y=1 (if Y is Up). 
            # In typical Games, X is Right. 
            # Let's assume standard math mapping: 90 deg SDL = East = X+
            
            angle_hundredths = desc.get("direction", {}).get("angle", 0)
            angle_deg = angle_hundredths / 100.0
            rad = math.radians(angle_deg - 90) # SDL 0=North (User 90 in math?), Math 0=East
            # Actually simpler:
            # SDL 0 = North.
            # SDL 90 = East.
            # X axis is East. Y axis is South?
            # x = sin(theta) * radius
            # y = -cos(theta) * radius (for North=Up) or something.
            
            # Simple heuristic for wheels: Most only care about X. 
            # If angle is roughly 90 or 270, use X.
            # If angle is 0, use Y (which wheel ignores).
            
            # Use proper trig:
            angle_rad = math.radians(angle_deg)
            # 0 is North. 90 is East. 
            # x = sin(angle)
            # y = -cos(angle) (assuming Up is -Y like screen coords) or cos(angle)
            
            x_val = math.sin(angle_rad)
            y_val = -math.cos(angle_rad) 
            
            # Log the conversion for debug but don't warn
            # logger.info(f"Auto-converting Polar {angle_deg} deg to Cartesian ({x_val:.2f}, {y_val:.2f})")
            
            # Mutate the effect structure to be Cartesian
            if kind in PERIODIC_TYPES:
                effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.periodic.direction.dir[0] = int(x_val * 1000) # Scaling? No, direction is normalized vector usually or loose.
                # SDL Cartesian dir requires 3 ints.
                # Docs say: "Positions of the three axes."
                # If they are -1..1, we can just use 1, -1.
                # Actually, SDL docs say "The direction is encoded by three signed integers... usually -1, 0, 1?"
                # Or just large values?
                # "Logic: If x > 0, +X force."
                effect.periodic.direction.dir[0] = 1 if x_val > 0.5 else (-1 if x_val < -0.5 else 0)
                effect.periodic.direction.dir[1] = 1 if y_val > 0.5 else (-1 if y_val < -0.5 else 0)
                effect.periodic.direction.dir[2] = 0
            
            elif kind == "constant":
                effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.constant.direction.dir[0] = 1 if x_val > 0.5 else (-1 if x_val < -0.5 else 0)
                effect.constant.direction.dir[1] = 1 if y_val > 0.5 else (-1 if y_val < -0.5 else 0)
                effect.constant.direction.dir[2] = 0
            
            elif kind == "ramp":
                effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.ramp.direction.dir[0] = 1 if x_val > 0.5 else (-1 if x_val < -0.5 else 0)
                effect.ramp.direction.dir[1] = 1 if y_val > 0.5 else (-1 if y_val < -0.5 else 0)
                effect.ramp.direction.dir[2] = 0
                
            elif kind in CONDITION_TYPES:
                effect.condition.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.condition.direction.dir[0] = 1 if x_val > 0.5 else (-1 if x_val < -0.5 else 0)
                effect.condition.direction.dir[1] = 1 if y_val > 0.5 else (-1 if y_val < -0.5 else 0)
                effect.condition.direction.dir[2] = 0

        return effect

    def play_descriptor(self, desc: Dict) -> int:
        if not self.haptic:
            return -1

        kind = (desc.get("type") or desc.get("effect") or "sine").lower()

        # CONDITIONAL REDIRECT: Use software oscillator if enabled
        if self.use_software_sine and kind in ["sine", "square", "triangle", "sawtooth"]:
            freq = float(desc.get("frequency_hz", 10.0))
            mag = int(desc.get("magnitude", 10000))
            dur_ms = int(desc.get("length_ms", 2000))
            phase = int(desc.get("phase", 0))
            return self.start_effect_sine(freq, mag, dur_ms, phase)

        # Handle custom separately so we can upload the buffer
        if kind == "custom" and "samples" in desc:
            samples = desc.get("samples", [])
            length_ms = int(desc.get("length_ms", len(samples)))
            return self.play_custom(samples, length_ms) and self.effect_id or -1

        effect = self.build_effect_from_descriptor(desc)
        if not effect:
            return -1

        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        
        # Fallback: If creation failed and we used Polar (common on Wheels that only support Cartesian X), try Cartesian
        if self.effect_id == -1 and kind in PERIODIC_TYPES:
            err = _sdl_error()
            logger.warning(f"Effect creation failed ({err}). Retrying with Cartesian X-Axis.")
            
            effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
            effect.periodic.direction.dir[0] = 1 # X-Axis
            effect.periodic.direction.dir[1] = 0
            effect.periodic.direction.dir[2] = 0
            
            self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))

        if self.effect_id == -1:
            logger.error(f"Failed to create effect: {_sdl_error()}")
            return -1

        sdl_error.SDL_ClearError()
        run_res = sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1) # Iterations=1
        if run_res != 0:
            logger.warning(f"RunEffect returned error (ignoring): {_sdl_error()}")
        
        # Always return ID to keep effect alive
        return self.effect_id

    # --- Sequencer Methods ---
    def start_effect_sine(self, freq: float, magnitude: int, duration_ms: int, phase: int = 0):
        logger.info(f"[OSCILL] start_effect_sine called: freq={freq}, mag={magnitude}, dur={duration_ms}, phase={phase}")
        if not self.haptic:
            logger.error("[OSCILL] No haptic device!")
            return -1
        
        # SOFTWARE EMULATION FOR ROBUSTNESS
        # Instead of creating a Sine effect, we create a Constant effect and oscillate it manually
        self._stop_oscillator()
        
        sdl_error.SDL_ClearError()
        
        # 1. Create or Update Constant Effect (Base)
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        # USE CARTESIAN X-Axis (Matches play_constant which works reliably)
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 1 # X-Axis
        effect.constant.direction.dir[1] = 0
        effect.constant.direction.dir[2] = 0
        effect.constant.length = 60000 # 60 seconds (Simagic doesn't support INFINITY)
        effect.constant.level = 10000
        effect.constant.attack_length = 0
        effect.constant.fade_length = 0
        
        try:
            if self.effect_id == -1:
                # Create new effect
                logger.info("[OSCILL] Creating CONSTANT effect (Cartesian X)...")
                new_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
                logger.info(f"[OSCILL] CreateEffect returned ID: {new_id}")
                if new_id == -1:
                    logger.error(f"[OSCILL] CreateEffect FAILED: {_sdl_error()}")
                    return -1
                self.effect_id = new_id
            else:
                # Stop and update existing effect
                logger.info(f"[OSCILL] Stopping and updating existing effect ID: {self.effect_id}")
                sdl_haptic.SDL_StopHapticEffect(self.haptic, self.effect_id)
                sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(effect))
                new_id = self.effect_id
            
            # 2. Run the Constant Effect and start oscillator
            # Note: Simagic wheelbases often return errors from RunEffect even when
            # the effect works. Since CreateEffect/UpdateEffect succeeded, we'll
            # start the oscillator anyway and let it manage the effect.
            sdl_error.SDL_ClearError()
            run_res = sdl_haptic.SDL_RunHapticEffect(self.haptic, new_id, 1) # Iterations=1
            
            if run_res != 0:
                 logger.warning(f"RunEffect (1 iter) returned error: {_sdl_error()} (ignoring)")
                 # Don't retry with INFINITY since it also fails - just continue
            else:
                logger.info("RunEffect SUCCESS!")
            
            # 3. Start Oscillator Thread (even if RunEffect reported error)
            # The effect was created successfully, so the oscillator should be able to update it
            logger.info(f"Starting Software Oscillator (freq={freq}Hz, mag={magnitude})")
            self._osc_params = {"freq": freq, "mag": magnitude, "phase": phase}
            self._oscillator_active = True  # Enable high-speed updates
            self._stop_oscillator_event.clear()
            self._oscillator_thread = threading.Thread(target=self._sine_worker, daemon=True)
            self._oscillator_thread.start()
                
            return new_id
        except OSError:
            logger.error("Access Violation in start_effect_sine: Device likely disconnected.")
            self.close_device()
            return -1
        except Exception as e:
            logger.error(f"Error in start_effect_sine: {e}")
            return -1

    def update_effect_sine(self, effect_id: int, freq: float, magnitude: int, duration_ms: int, phase: int = 0):
        """Updates parameters for Software Oscillator."""
        if not self.haptic or effect_id == -1: return -1
        
        # Just update the thread params
        self._osc_params["freq"] = freq
        self._osc_params["mag"] = magnitude
        self._osc_params["phase"] = phase
        
        return effect_id 



    def update_effect_constant(self, effect_id: int, magnitude: int, duration_ms: int):
        if not self.haptic or effect_id == -1: return
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 1
        effect.constant.direction.dir[1] = 0
        effect.constant.direction.dir[2] = 0
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def update_effect_ramp(self, effect_id: int, start_mag: int, end_mag: int, duration_ms: int):
        if not self.haptic or effect_id == -1: return

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.ramp.direction.dir[0] = 1
        effect.ramp.direction.dir[1] = 0
        effect.ramp.direction.dir[2] = 0
        effect.ramp.length = duration_ms
        effect.ramp.start = start_mag
        effect.ramp.end = end_mag
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def update_effect_sawtooth(self, effect_id: int, magnitude: int, period: int, duration_ms: int):
        if not self.haptic or effect_id == -1: return

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_SAWTOOTHUP
        effect.periodic.type = sdl_haptic.SDL_HAPTIC_SAWTOOTHUP
        effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.periodic.direction.dir[0] = 1
        effect.periodic.direction.dir[1] = 0
        effect.periodic.direction.dir[2] = 0
        effect.periodic.period = period
        effect.periodic.magnitude = magnitude
        effect.periodic.length = duration_ms
        effect.periodic.attack_length = 50
        effect.periodic.fade_length = 50
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def start_effect_constant(self, magnitude: int, duration_ms: int):
        if not self.haptic: return -1
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 1
        effect.constant.direction.dir[1] = 0
        effect.constant.direction.dir[2] = 0
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        new_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if new_id != -1:
            sdl_haptic.SDL_RunHapticEffect(self.haptic, new_id, 1)
        return new_id
        
    def start_effect_ramp(self, start_mag: int, end_mag: int, duration_ms: int):
        if not self.haptic: return -1

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.ramp.direction.dir[0] = 1
        effect.ramp.direction.dir[1] = 0
        effect.ramp.direction.dir[2] = 0
        effect.ramp.length = duration_ms
        effect.ramp.start = start_mag
        effect.ramp.end = end_mag
        
        new_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if new_id != -1:
            sdl_haptic.SDL_RunHapticEffect(self.haptic, new_id, 1)
        return new_id

    def start_effect_sawtooth(self, magnitude: int, period: int, duration_ms: int):
        if not self.haptic: return -1

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_SAWTOOTHUP
        effect.periodic.type = sdl_haptic.SDL_HAPTIC_SAWTOOTHUP
        effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.periodic.direction.dir[0] = 1
        effect.periodic.direction.dir[1] = 0
        effect.periodic.direction.dir[2] = 0
        effect.periodic.period = period
        effect.periodic.magnitude = magnitude
        effect.periodic.length = duration_ms
        effect.periodic.attack_length = 50
        effect.periodic.fade_length = 50
        
        new_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if new_id != -1:
            sdl_haptic.SDL_RunHapticEffect(self.haptic, new_id, 1)
        return new_id
        
    def stop_effect(self, effect_id: int = -1):
        if not self.haptic: return
        
        try:
            if effect_id == -1:
                # Stop All
                self._stop_oscillator()
                sdl_haptic.SDL_StopHapticEffects(self.haptic)
            else:
                self._stop_oscillator() # Stop thread if stopping specific effect (assuming only 1 effect active usually)
                sdl_haptic.SDL_StopHapticEffect(self.haptic, effect_id)
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, effect_id) # Cleanup immediately? Or sequence end?
        except (OSError, Exception) as e:
            logger.error(f"Error in stop_effect: {e}")
            self.close_device()
            
    # --- Software Oscillator ---
    def _stop_oscillator(self):
        self._oscillator_active = False  # Trigger idle sleep mode
        if self._oscillator_thread:
            self._stop_oscillator_event.set()
            self._oscillator_thread.join()
            self._oscillator_thread = None
            
    def _sine_worker(self):
        """Thread that updates Constant Force to emulate Sine."""
        t0 = time.time()
        while not self._stop_oscillator_event.is_set():
            # Check if we should be actively running
            if not getattr(self, '_oscillator_active', False):
                # Sleep longer when idle (not playing)
                time.sleep(0.1)
                continue
                
            t = time.time() - t0
            params = self._osc_params.copy()
            if not params:
                time.sleep(0.0001)  # Minimal yield
                continue
                
            freq = params.get("freq", 10.0)
            mag = params.get("mag", 10000)
            # native_app sends phase as 0-35900
            phase_offset_rad = math.radians(params.get("phase", 0) / 100.0)
            
            # Calculate Sine
            val = math.sin(2 * math.pi * freq * t + phase_offset_rad)
            current_level = int(val * mag)
            
            # Update Effect (Reuse existing Constant Effect logic, but manually)
            # We assume self.effect_id is already a CONSTANT effect
            if self.haptic and self.effect_id != -1:
                effect = sdl_haptic.SDL_HapticEffect()
                effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
                effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.constant.direction.dir[0] = 1 # X-Axis
                effect.constant.direction.dir[1] = 0
                effect.constant.direction.dir[2] = 0
                effect.constant.length = 60000 # Match initial effect creation
                effect.constant.level = current_level
                
                sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(effect))
            
            # Update at device maximum rate (minimal yield to prevent CPU lock)
            time.sleep(0.0001)
    # -----------------------

# Global Instance
engine = HapticController()
