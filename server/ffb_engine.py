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
from sdl3 import SDL_events as sdl_events
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
        
        # Polling rate throttling
        self.detected_poll_rate_hz: int = 1000  # Default assumption
        self.measured_usb_rate_hz: int = 0  # Actual measured rate from oscillator
        self.target_update_rate_hz: int = 500   # 50% of detected (USB shared with position polling)
        self.manual_poll_rate_override: Optional[int] = None  # User can override
        self._min_update_interval_s: float = 0.002  # 2ms = 500Hz
        
        # Device version counter to detect reconnects
        self._device_version: int = 0
        self._haptic_lock = threading.Lock()
        
        # Event Queue for asynchronous API calls
        self._event_queue_enabled = True
        self._pending_payloads: Dict[str, any] = {}  # UUID -> payload data
        self._event_types_registered = False
        self._custom_event_types = {}
        self._payload_lock = threading.Lock()
        self._queue_stats = {
            "events_queued": 0,
            "events_processed": 0,
            "queue_overflows": 0,
        }
        # Store effect_ids created by async events: (track_idx, clip_id) -> effect_id
        self._effect_id_results: Dict[tuple, int] = {}
        # High-water mark before purging stale events (SDL queue limit ~65535)
        self._event_queue_high_watermark = 65000
        
    def init_sdl(self):
        flags = (
            sdl_init.SDL_INIT_VIDEO
            | sdl_init.SDL_INIT_JOYSTICK
            | sdl_init.SDL_INIT_HAPTIC
            | sdl_init.SDL_INIT_GAMEPAD
            | sdl_init.SDL_INIT_EVENTS
        )

        if not sdl_init.SDL_Init(flags):
            logger.error(f"SDL_Init Error: {_sdl_error()}")
            raise Exception("Failed to initialize SDL3")
        
        # Register custom event types
        self._register_custom_events()
        
    def _register_custom_events(self):
        """Register SDL3 custom user events for asynchronous API calls."""
        if self._event_types_registered:
            return
        
        try:
            # Register 3 custom event types
            base_event = sdl_events.SDL_RegisterEvents(3)
            if base_event == ctypes.c_uint32(-1).value:
                logger.error("Failed to register SDL custom events")
                self._event_queue_enabled = False
                return
            
            self._custom_event_types = {
                "EFFECT_START": base_event,
                "EFFECT_STOP": base_event + 1,
                "POSITION_REQUEST": base_event + 2,
            }
            self._event_types_registered = True
            logger.info(f"SDL3 custom events registered: {self._custom_event_types}")
        except Exception as e:
            logger.error(f"Failed to register custom events: {e}")
            self._event_queue_enabled = False
        
    def list_devices(self):
        # Pump events to refresh the device list (detects plug/unplug)
        sdl_events.SDL_PumpEvents()
        
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

            # Include ALL joysticks, not just haptic/gamepad ones
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
        self._device_version += 1  # Increment to invalidate old oscillator threads

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

        if sdl_haptic.SDL_HapticRumbleSupported(self.haptic):
            sdl_haptic.SDL_InitHapticRumble(self.haptic)

        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        logger.info(f"Device Capabilities: {caps:08x}")
        if caps & sdl_haptic.SDL_HAPTIC_CONSTANT:
            logger.info("- CONSTANT Supported")
        if caps & sdl_haptic.SDL_HAPTIC_SINE:
            logger.info("- SINE Supported")
        if caps & sdl_haptic.SDL_HAPTIC_CUSTOM:
            logger.info("- CUSTOM Supported")

        logger.info(f"Connected to device idx={index} id={joy_id}")
        
        # Measure USB update rate right after connection
        self._calibrate_usb_rate()
        
        return True

    def _calibrate_usb_rate(self):
        """Measure USB update rate with 5 samples, using slowest - 10%."""
        if not self.haptic:
            return
        
        # Create a temporary constant effect for measurement
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 1
        effect.constant.length = 1000
        effect.constant.level = 0  # Silent
        
        temp_effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if temp_effect_id == -1:
            logger.warning("Could not create temp effect for USB rate measurement")
            return
        
        try:
            sdl_haptic.SDL_RunHapticEffect(self.haptic, temp_effect_id, 1)
            
            logger.info("Measuring USB update rate (5 samples)...")
            rates = []
            
            for i in range(5):
                # Measure how many updates we can do in 100ms
                sample_start = time.time()
                sample_count = 0
                
                while (time.time() - sample_start) < 0.1:  # 100ms per sample
                    try:
                        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, temp_effect_id, ctypes.byref(effect))
                        sample_count += 1
                    except OSError:
                        break
                
                sample_elapsed = time.time() - sample_start
                if sample_elapsed > 0 and sample_count > 0:
                    rate = int(sample_count / sample_elapsed)
                    rates.append(rate)
            
            if rates:
                slowest_rate = min(rates)
                safe_rate = int(slowest_rate * 0.9) # 10% safety margin
                
                self.measured_usb_rate_hz = safe_rate
                self.detected_poll_rate_hz = safe_rate
                self.target_update_rate_hz = safe_rate
                self._min_update_interval_s = 1.0 / safe_rate
                logger.info(f"USB update rate: {safe_rate}Hz (slowest {slowest_rate}Hz - 10%, samples: {rates})")
        except OSError as e:
            logger.warning(f"Error measuring USB rate: {e}")
        finally:
            sdl_haptic.SDL_StopHapticEffect(self.haptic, temp_effect_id)
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, temp_effect_id)

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

    def detect_device_poll_rate(self, test_duration_s: float = 0.5) -> int:
        """Returns the measured USB update rate."""
        return self.measured_usb_rate_hz if self.measured_usb_rate_hz > 0 else self.target_update_rate_hz

    def set_target_update_rate(self, rate_hz: int, is_manual_override: bool = False):
        """
        Set the target update rate for effect updates.
        If is_manual_override is True, this rate will persist until reset.
        """
        rate_hz = max(10, min(1000, rate_hz))  # Clamp to valid range
        
        if is_manual_override:
            self.manual_poll_rate_override = rate_hz
        
        self.target_update_rate_hz = rate_hz
        self._min_update_interval_s = 1.0 / rate_hz
        logger.info(f"Target update rate set to {rate_hz}Hz (interval: {self._min_update_interval_s*1000:.2f}ms)")
    
    def clear_manual_poll_rate_override(self):
        """Clear manual override and reset to 75% of detected rate."""
        self.manual_poll_rate_override = None
        self.set_target_update_rate(int(self.detected_poll_rate_hz * 0.75))
        logger.info("Manual poll rate override cleared, using auto-detected rate")

    def get_effective_update_rate(self) -> int:
        """Return the currently active update rate."""
        if self.manual_poll_rate_override is not None:
            return self.manual_poll_rate_override
        return self.target_update_rate_hz


    def play_constant(self, level: int, length: int = 5000):
        if not self.haptic: return
        
        # Ensure Constant supported, or fallback? 
        # Most devices support constant.
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 0
        effect.constant.length = length
        effect.constant.level = level
        
        if self.effect_id == -1:
            self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        else:
            sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(effect))

        if self.effect_id != -1:
            sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1)

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
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
            
        if self.effect_id == -1:
            logger.error(f"Failed to upload custom effect: {_sdl_error()}")
            return False

        if not sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1):
            logger.error(f"Failed to run effect: {_sdl_error()}")
            return False
        
        return True

    def _apply_baseline(self, key: str, value: int) -> int:
        # Return raw value without baseline offset to avoid stale baseline issues
        return value

    def poll_input(self):
        """Polls SDL events to update joystick/gamepad state. Call once per frame."""
        # SDL_PumpEvents updates the event queue and input device states.
        sdl_events.SDL_PumpEvents()
        # Explicit update calls if needed, though PumpEvents covers most cases.
        # Keeping them for robustness if PumpEvents isn't enough for Gamepad API.
        try:
            sdl_gp.SDL_UpdateGamepads()
            sdl_joy.SDL_UpdateJoysticks()
        except: pass

    def get_axis_value(self, axis_idx: int = 0) -> Optional[int]:
        """Read wheel position with baseline offset removal. Defaults to preferred X axis."""
        if not self.joystick:
            return None
        try:
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
                logger.warning(f"SDL_GetNumJoystickAxes returned {axis_count}, joystick may be disconnected")
                return None
            idx = int(key.split("_")[1]) if key and key.startswith("joy_") else axis_idx
            if idx < 0 or idx >= axis_count:
                idx = 0
            raw = int(sdl_joy.SDL_GetJoystickAxis(self.joystick, idx))
            return self._apply_baseline(f"joy_{idx}", raw)
        except Exception as e:
            logger.warning(f"Exception in get_axis_value: {e}")
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
        direction = sdl_haptic.SDL_HapticDirection()
        direction.type = DIRECTION_MODES.get(mode, sdl_haptic.SDL_HAPTIC_POLAR)
        values = values or {}

        # SDL expects three components, interpretation depends on the type
        if mode == "cartesian":
            comps = (values.get("x", 1), values.get("y", 0), values.get("z", 0))
        elif mode == "spherical":
            comps = (values.get("yaw", 0), values.get("pitch", 0), values.get("distance", 1))
        else:  # polar default
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
        effect.periodic.length = int(desc.get("length_ms", sdl_haptic.SDL_HAPTIC_INFINITY))
        # Offset phase by +60 degrees to compensate for SDL's phase reference
        raw_phase = int(desc.get("phase", 0))
        adjusted_phase = (raw_phase - 9000) % 36000  
        effect.periodic.phase = adjusted_phase
        self._apply_envelope(effect.periodic, envelope)

    def _build_constant(self, effect, desc: Dict) -> None:
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        envelope = desc.get("envelope", {})

        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction = direction
        effect.constant.length = int(desc.get("length_ms", sdl_haptic.SDL_HAPTIC_INFINITY))
        effect.constant.level = self._clamp_short(desc.get("magnitude", 20000))
        self._apply_envelope(effect.constant, envelope)

    def _build_ramp(self, effect, desc: Dict) -> None:
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        envelope = desc.get("envelope", {})

        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction = direction
        effect.ramp.length = int(desc.get("length_ms", sdl_haptic.SDL_HAPTIC_INFINITY))
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
        effect.condition.length = int(desc.get("length_ms", sdl_haptic.SDL_HAPTIC_INFINITY))

        defaults = {
            "right_sat": 16000,
            "left_sat": 16000,
            "right_coeff": 8000,
            "left_coeff": 8000,
            "deadband": 0,
            "center": 0,
        }

        for axis in range(3):
            axis_key = {0: "x", 1: "y", 2: "z"}[axis]
            axis_vals = axes.get(axis_key, {})
            effect.condition.right_sat[axis] = int(axis_vals.get("right_sat", defaults["right_sat"]))
            effect.condition.left_sat[axis] = int(axis_vals.get("left_sat", defaults["left_sat"]))
            effect.condition.right_coeff[axis] = self._clamp_short(axis_vals.get("right_coeff", defaults["right_coeff"]))
            effect.condition.left_coeff[axis] = self._clamp_short(axis_vals.get("left_coeff", defaults["left_coeff"]))
            effect.condition.deadband[axis] = int(axis_vals.get("deadband", defaults["deadband"]))
            effect.condition.center[axis] = self._clamp_short(axis_vals.get("center", defaults["center"]))

    def _build_left_right(self, effect, desc: Dict) -> None:
        effect.type = sdl_haptic.SDL_HAPTIC_LEFTRIGHT
        effect.leftright.length = int(desc.get("length_ms", 1000))
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

        return effect

    def play_descriptor(self, desc: Dict) -> int:
        if not self.haptic:
            return -1

        kind = (desc.get("type") or desc.get("effect") or "sine").lower()

        # Handle custom separately so we can upload the buffer
        if kind == "custom" and "samples" in desc:
            samples = desc.get("samples", [])
            length_ms = int(desc.get("length_ms", len(samples)))
            return self.play_custom(samples, length_ms) and self.effect_id or -1

        # Check for software sine override in descriptor
        use_sw = desc.get("use_software_sine", self.use_software_sine)

        # Software sine: route periodic effects through oscillator thread
        if use_sw and kind in ("sine", "square", "triangle", "sawtooth", "sawtoothup", "sawtoothdown", "ramp"):
            freq = float(desc.get("frequency_hz", 10.0))
            mag = int(desc.get("magnitude", 10000))
            length_ms = int(desc.get("length_ms", 5000))
            phase = int(desc.get("phase", 0))
            # Store desc for direction/envelope reference
            self._current_descriptor = desc
            return self._start_software_oscillator(freq, mag, length_ms, phase, desc)

        # Hardware mode: use standard effect building
        self._stop_oscillator()
        
        # Store descriptor and start phase for update_effect_sine
        self._current_descriptor = desc
        raw_phase = int(desc.get("phase", 0))
        self._start_phase = (raw_phase - 9000) % 36000  # Same offset as _build_periodic
        
        effect = self.build_effect_from_descriptor(desc)
        if not effect:
            return -1

        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if self.effect_id == -1:
            logger.error(f"Failed to create effect: {_sdl_error()}")
            return -1

        run_res = sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1)
        if run_res:
            return self.effect_id

        logger.error(f"Failed to run effect: {_sdl_error()}")
        return -1
    # --- Sequencer Methods ---
    def _start_software_oscillator(self, freq: float, magnitude: int, duration_ms: int, phase: int, desc: dict):
        """Start software oscillator using constant force effect with proper direction from descriptor."""
        osc_type = (desc.get("type") or desc.get("effect") or "sine").lower()
        print(f">>> START SOFTWARE OSCILLATOR: type={osc_type} freq={freq}")
        if not self.haptic:
            return -1
        
        self._stop_oscillator()
        
        # Destroy existing effect
        if self.effect_id != -1:
            try:
                sdl_haptic.SDL_StopHapticEffect(self.haptic, self.effect_id)
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            except:
                pass
            self.effect_id = -1
        
        # Build direction from descriptor
        direction = self._make_direction(desc.get("direction_mode", "polar"), desc.get("direction"))
        
        sdl_error.SDL_ClearError()
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction = direction
        effect.constant.length = 60000
        effect.constant.level = magnitude
        effect.constant.attack_length = 0
        effect.constant.fade_length = 0
        
        try:
            new_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
            if new_id == -1:
                logger.error(f"CreateEffect FAILED: {_sdl_error()}")
                return -1
            self.effect_id = new_id
            
            sdl_haptic.SDL_RunHapticEffect(self.haptic, new_id, 1)
            
            # Adjust phase offset like _build_periodic does
            adjusted_phase = (phase - 9000) % 36000
            
            # Start oscillator
            self._osc_params = {
                "freq": freq, 
                "mag": magnitude, 
                "phase": adjusted_phase,
                "type": osc_type
            }
            self._oscillator_active = True
            self._stop_oscillator_event.clear()
            self._oscillator_thread = threading.Thread(target=self._software_oscillator_worker, daemon=True)
            self._oscillator_thread.start()
            return new_id
        except Exception as e:
            logger.error(f"Error in _start_software_oscillator: {e}")
            # Don't close device here - joystick input should remain functional
            return -1

    def update_effect_sine(self, effect_id: int, freq: float, magnitude: int, duration_ms: int, phase: int = 0):
        """Updates sine effect parameters during playback (sweep updates)."""
        if not self.haptic or effect_id == -1: return -1
        
        # Check if we are running software oscillator for this descriptor
        desc = getattr(self, '_current_descriptor', {})
        use_sw = desc.get("use_software_sine", self.use_software_sine)

        if use_sw:
            # Software: only update freq and mag - phase is accumulated internally by oscillator
            self._osc_params["freq"] = freq
            self._osc_params["mag"] = magnitude
            # Note: phase is NOT updated here - oscillator handles phase accumulation
            return effect_id
        else:
            # Hardware: update SDL effect - use stored descriptor for proper direction
            desc = getattr(self, '_current_descriptor', None)
            direction = self._make_direction(
                desc.get("direction_mode", "polar") if desc else "polar",
                desc.get("direction") if desc else None
            )
            
            if not desc:
                # Fallback to sine if no descriptor
                sdl_type = sdl_haptic.SDL_HAPTIC_SINE
            else:
                kind = (desc.get("type") or desc.get("effect") or "sine").lower()
                sdl_type = PERIODIC_TYPES.get(kind, sdl_haptic.SDL_HAPTIC_SINE)

            effect = sdl_haptic.SDL_HapticEffect()
            ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
            effect.type = sdl_type
            effect.periodic.type = sdl_type
            effect.periodic.direction = direction
            effect.periodic.period = int(1000 / max(0.1, float(freq)))
            effect.periodic.magnitude = magnitude
            effect.periodic.length = duration_ms
            effect.periodic.attack_length = 0
            effect.periodic.fade_length = 0
            # Keep phase fixed from clip start - don't use the changing phase value
            effect.periodic.phase = getattr(self, '_start_phase', 0)
            
            try:
                sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))
                return effect_id
            except Exception as e:
                logger.error(f"Error updating haptic effect: {e}")
                # Don't close device here - joystick input should remain functional
                return -1 



    def update_effect_constant(self, effect_id: int, magnitude: int, duration_ms: int):
        if not self.haptic or effect_id == -1: return
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def update_effect_ramp(self, effect_id: int, start_mag: int, end_mag: int, duration_ms: int):
        if not self.haptic or effect_id == -1: return

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
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
        self._flush_custom_event_queue()
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
            # Don't close device here - joystick input should remain functional
    
    # --- Event Queue Methods ---
    def queue_effect_start(self, descriptor: Dict, track_idx: int, clip_id: str, effect_id: int = -1) -> bool:
        """Queue an effect start command as SDL event. Returns True if queued successfully."""

        if not self._event_queue_enabled:
            # Fallback to synchronous execution
            result_id = self.play_descriptor(descriptor)
            return result_id != -1

        pending = max(0, self._queue_stats["events_queued"] - self._queue_stats["events_processed"])
        if pending >= self._event_queue_high_watermark:
            logger.warning(
                "SDL event queue near limit (%d pending entries), purging stale events",
                pending,
            )

        # Always clear stale events before adding another start request
        self._flush_custom_event_queue()

        try:
            import uuid
            
            # Store payload
            payload_id = str(uuid.uuid4())
            with self._payload_lock:
                self._pending_payloads[payload_id] = {
                    "descriptor": descriptor,
                    "track_idx": track_idx,
                    "clip_id": clip_id,
                    "effect_id": effect_id,
                }
            
            # Create and push SDL event
            event = sdl_events.SDL_Event()
            event.type = self._custom_event_types["EFFECT_START"]
            # Store payload_id in user data (using windowID as a hack for string storage)
            # We'll use code to store hash of payload_id for quick lookup
            event.user.code = hash(payload_id) & 0x7FFFFFFF  # Keep positive
            event.user.data1 = id(payload_id)  # Python object id
            
            # Store mapping for retrieval
            with self._payload_lock:
                self._pending_payloads[f"hash_{event.user.code}"] = payload_id
            
            result = sdl_events.SDL_PushEvent(ctypes.byref(event))
            if result:
                self._queue_stats["events_queued"] += 1
                return True
            else:
                logger.warning(f"SDL_PushEvent failed for EFFECT_START: {_sdl_error()}")
                self._queue_stats["queue_overflows"] += 1
                # Fallback to synchronous
                with self._payload_lock:
                    del self._pending_payloads[payload_id]
                    if f"hash_{event.user.code}" in self._pending_payloads:
                        del self._pending_payloads[f"hash_{event.user.code}"]
                result_id = self.play_descriptor(descriptor)
                return result_id != -1
        except Exception as e:
            logger.error(f"Error queuing effect start: {e}")
            return False
    
    def queue_effect_stop(self, effect_id: int, track_idx: int = -1) -> bool:
        """Queue an effect stop command as SDL event. Returns True if queued successfully."""
        if not self._event_queue_enabled:
            # Fallback to synchronous execution
            self.stop_effect(effect_id)
            return True
        
        pending = max(0, self._queue_stats["events_queued"] - self._queue_stats["events_processed"])
        if pending >= self._event_queue_high_watermark:
            logger.warning(
                "SDL event queue near limit (%d pending entries) before stop, purging",
                pending,
            )

        self._flush_custom_event_queue()

        try:
            import uuid
            
            # Store payload
            payload_id = str(uuid.uuid4())
            with self._payload_lock:
                self._pending_payloads[payload_id] = {
                    "effect_id": effect_id,
                    "track_idx": track_idx,
                }
            
            # Create and push SDL event
            event = sdl_events.SDL_Event()
            event.type = self._custom_event_types["EFFECT_STOP"]
            event.user.code = hash(payload_id) & 0x7FFFFFFF
            event.user.data1 = id(payload_id)
            
            with self._payload_lock:
                self._pending_payloads[f"hash_{event.user.code}"] = payload_id
            
            result = sdl_events.SDL_PushEvent(ctypes.byref(event))
            if result:
                self._queue_stats["events_queued"] += 1
                return True
            else:
                logger.warning(f"SDL_PushEvent failed for EFFECT_STOP: {_sdl_error()}")
                self._queue_stats["queue_overflows"] += 1
                # Fallback to synchronous
                with self._payload_lock:
                    del self._pending_payloads[payload_id]
                    if f"hash_{event.user.code}" in self._pending_payloads:
                        del self._pending_payloads[f"hash_{event.user.code}"]
                self.stop_effect(effect_id)
                return True
        except Exception as e:
            logger.error(f"Error queuing effect stop: {e}")
            return False

    def _flush_custom_event_queue(self):
        """Drop pending SDL custom events and clear their payloads."""
        if not self._event_queue_enabled or not self._event_types_registered:
            return

        if not self._custom_event_types:
            return

        min_type = self._custom_event_types.get("EFFECT_START")
        max_type = self._custom_event_types.get("POSITION_REQUEST")
        if min_type is None or max_type is None:
            return

        pending_before = max(0, self._queue_stats["events_queued"] - self._queue_stats["events_processed"])
        sdl_events.SDL_FlushEvents(min_type, max_type)

        with self._payload_lock:
            self._pending_payloads.clear()

        self._queue_stats["events_queued"] = 0
        self._queue_stats["events_processed"] = 0
        if pending_before:
            logger.info("Flushed %d pending SDL custom events", pending_before)

    def process_event_queue(self, max_events: int = 32):
        """Process pending SDL events from the queue. Call this regularly from logic thread."""
        if not self._event_queue_enabled or not self._event_types_registered:
            return
        
        event = sdl_events.SDL_Event()
        processed = 0
        
        while processed < max_events:
            # Peek events in our custom range
            min_type = self._custom_event_types["EFFECT_START"]
            max_type = self._custom_event_types["POSITION_REQUEST"]
            
            result = sdl_events.SDL_PeepEvents(
                ctypes.byref(event), 1,
                sdl_events.SDL_GETEVENT,  # Remove from queue
                min_type, max_type
            )
            
            if result <= 0:
                break  # No more events
            
            # Dispatch event
            try:
                if event.type == self._custom_event_types["EFFECT_START"]:
                    self._handle_effect_start_event(event)
                elif event.type == self._custom_event_types["EFFECT_STOP"]:
                    self._handle_effect_stop_event(event)
                elif event.type == self._custom_event_types["POSITION_REQUEST"]:
                    self._handle_position_request_event(event)
                
                self._queue_stats["events_processed"] += 1
            except Exception as e:
                logger.error(f"Error processing event type {event.type}: {e}")
            
            processed += 1
    
    def _handle_effect_start_event(self, event):
        """Handle EFFECT_START event by executing play_descriptor."""
        try:
            # Retrieve payload
            hash_key = f"hash_{event.user.code}"
            with self._payload_lock:
                payload_id = self._pending_payloads.get(hash_key)
                if not payload_id:
                    logger.warning("EFFECT_START event has no payload")
                    return
                
                payload = self._pending_payloads.get(payload_id)
                if not payload:
                    logger.warning(f"Payload {payload_id} not found")
                    return
                
                # Extract payload data
                descriptor = payload["descriptor"]
                track_idx = payload["track_idx"]
                clip_id = payload["clip_id"]
                prev_effect_id = payload["effect_id"]
                
                # Clean up
                del self._pending_payloads[payload_id]
                del self._pending_payloads[hash_key]
            
            # Execute the API call (now asynchronous from the caller's perspective)
            new_effect_id = self.play_descriptor(descriptor)
            
            # Store the result so the app can retrieve it
            with self._payload_lock:
                self._effect_id_results[(track_idx, clip_id)] = new_effect_id
            
        except Exception as e:
            logger.error(f"Error handling EFFECT_START event: {e}")
    
    def _handle_effect_stop_event(self, event):
        """Handle EFFECT_STOP event by executing stop_effect."""
        try:
            # Retrieve payload
            hash_key = f"hash_{event.user.code}"
            with self._payload_lock:
                payload_id = self._pending_payloads.get(hash_key)
                if not payload_id:
                    logger.warning("EFFECT_STOP event has no payload")
                    return
                
                payload = self._pending_payloads.get(payload_id)
                if not payload:
                    logger.warning(f"Payload {payload_id} not found")
                    return
                
                effect_id = payload["effect_id"]
                
                # Clean up
                del self._pending_payloads[payload_id]
                del self._pending_payloads[hash_key]
            
            # Execute the API call
            self.stop_effect(effect_id)
            
        except Exception as e:
            logger.error(f"Error handling EFFECT_STOP event: {e}")
    
    def _handle_position_request_event(self, event):
        """Handle POSITION_REQUEST event (placeholder for future use)."""
        # Position polling is already handled by poll_input() in logic thread
        # This is a placeholder for future enhancements
        pass
    
    def get_effect_id_result(self, track_idx: int, clip_id: str) -> Optional[int]:
        """
        Retrieve the effect_id for an async-created effect.
        Returns the effect_id if available, or None if not yet created.
        Consumes the result (removes it from cache).
        """
        with self._payload_lock:
            key = (track_idx, clip_id)
            if key in self._effect_id_results:
                effect_id = self._effect_id_results[key]
                del self._effect_id_results[key]  # Consume the result
                return effect_id
        return None
    
    def get_queue_stats(self) -> Dict[str, int]:
        """Return event queue statistics."""
        return self._queue_stats.copy()
            
    # --- Software Oscillator ---
    def _stop_oscillator(self):
        self._oscillator_active = False  # Trigger idle sleep mode
        
        # Zero the level before stopping to prevent force spike
        if hasattr(self, '_cached_effect') and self.haptic and self.effect_id != -1:
            self._cached_effect.constant.level = 0
            try:
                sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(self._cached_effect))
            except:
                pass
        
        if hasattr(self, '_oscillator_thread') and self._oscillator_thread:
            self._stop_oscillator_event.set()
            self._oscillator_thread.join()
            self._oscillator_thread = None
            print("[OSC] Thread stopped")
            
    def _software_oscillator_worker(self):
        """Thread that updates Constant Force to emulate various periodic waveforms."""
        print("[OSC] Thread started!")
        
        # Capture the device version at thread start - if it changes, we must exit
        osc_device_version = self._device_version
        
        # Pre-create the cached effect structure
        cached_effect = sdl_haptic.SDL_HapticEffect()
        cached_effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        cached_effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        cached_effect.constant.direction.dir[0] = 1
        cached_effect.constant.direction.dir[1] = 0
        cached_effect.constant.direction.dir[2] = 0
        cached_effect.constant.length = 60000
        self._cached_effect = cached_effect  # Store for stop function
        
        # Pre-initialize all variables to avoid overhead in hot loop
        last_time = time.time()
        last_usb_update_time = time.time()  # Track when we last sent USB command
        accumulated_phase = 0.0
        update_count = 0
        
        two_pi = 2 * math.pi
        
        while not self._stop_oscillator_event.is_set():
            # Check if we should be actively running
            if not self._oscillator_active:
                time.sleep(0.1)
                last_time = time.time()
                last_usb_update_time = time.time()
                update_count = 0
                continue
            
            current_time = time.time()
            dt = current_time - last_time
            last_time = current_time
            
            params = self._osc_params
            if not params:
                time.sleep(0.001)
                continue
            
            freq = params.get("freq", 10.0)
            mag = params.get("mag", 10000)
            phase_deg = params.get("phase", 0)
            phase_offset_rad = math.radians(phase_deg / 100.0) if phase_deg > 360 else math.radians(phase_deg)
            osc_type = params.get("type", "sine")
            
            # Accumulate phase (this runs at full speed for accuracy)
            accumulated_phase += two_pi * freq * dt
            if accumulated_phase > 6283.185:
                accumulated_phase %= 6283.185
            
            # Calculate waveform value (-1.0 to 1.0)
            val = 0.0
            total_phase = (accumulated_phase + phase_offset_rad) % two_pi
            norm_phase = total_phase / two_pi  # 0.0 to 1.0
            
            if osc_type == "sine":
                val = math.sin(total_phase)
            elif osc_type == "square":
                val = 1.0 if math.sin(total_phase) >= 0 else -1.0
            elif osc_type == "triangle":
                # Triangle: 0->1 (0-0.25), 1->-1 (0.25-0.75), -1->0 (0.75-1.0)
                # Adjusted to match Sine phase (starts at 0, going up)
                if norm_phase < 0.25:
                    val = 4.0 * norm_phase
                elif norm_phase < 0.75:
                    val = 2.0 - 4.0 * norm_phase
                else:
                    val = 4.0 * norm_phase - 4.0
            elif osc_type == "sawtoothup":
                # Sawtooth Up: -1 to 1 over one period
                # Starts at -1 (phase 0) if we use 2*t - 1
                val = -1.0 + 2.0 * norm_phase
            elif osc_type == "sawtoothdown":
                # Sawtooth Down: 1 to -1 over one period
                val = 1.0 - 2.0 * norm_phase
            elif osc_type == "ramp":
                # Treat Ramp as Sawtooth Up for periodic context
                val = -1.0 + 2.0 * norm_phase
            else:
                val = math.sin(total_phase)
                
            current_level = int(val * mag)
            
            # Only send USB update if enough time has passed
            time_since_last_usb = current_time - last_usb_update_time
            if time_since_last_usb >= self._min_update_interval_s:
                with self._haptic_lock:
                    haptic = self.haptic
                    effect_id = self.effect_id
                    device_version = self._device_version
                
                # Check validity
                if device_version != osc_device_version:
                    print(f"[OSC] Device version changed, stopping thread")
                    break
                
                if haptic and effect_id != -1:
                    cached_effect.constant.level = current_level
                    try:
                        sdl_haptic.SDL_UpdateHapticEffect(haptic, effect_id, ctypes.byref(cached_effect))
                        last_usb_update_time = current_time
                        update_count += 1
                    except OSError as e:
                        print(f"[OSC] SDL_UpdateHapticEffect error: {e}, stopping thread")
                        break
            
            # Small sleep to prevent CPU spin
            time.sleep(0.0001)
    # -----------------------

# Global Instance
engine = HapticController()

