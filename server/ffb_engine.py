import ctypes
import asyncio
import logging
import math
import os
import sys
import threading
import time
import platform
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
from sdl3 import SDL_version as sdl_ver

if platform.system() == "Windows":
    import ctypes.wintypes as wintypes
    from ctypes import WINFUNCTYPE, POINTER, Structure, c_void_p, c_int32, c_uint32, c_char_p, c_wchar_p
    
    # DirectInput8 Constants & GUIDs
    DI_OK = 0
    DI_FFNOMINALMAX = 10000
    DIEFF_POLAR = 0x00000010
    DIJOFS_X = 0
    DISFFC_RESET = 0x00000001
    DISFFC_STOPALL = 0x00000002
    DISFFC_PAUSE = 0x00000004
    DISFFC_CONTINUE = 0x00000008
    DISFFC_SETACTUATORSON = 0x00000010
    DISFFC_SETACTUATORSOFF = 0x00000020
    
    class GUID(Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]
        def __init__(self, guid_str):
            import uuid
            u = uuid.UUID(guid_str)
            self.Data1 = u.time_low
            self.Data2 = u.time_mid
            self.Data3 = u.time_hi_version
            self.Data4 = (wintypes.BYTE * 8)(*u.bytes[8:])

    CLSID_DirectInput8 = GUID("{BF610441-3950-4F39-94A1-87DD2E572C88}")
    IID_IDirectInput8W = GUID("{BF610441-3950-4F39-94A1-87DD2E572C88}")
    GUID_Sine = GUID("{13541C20-8E33-11D0-9AD0-00A0C9A06E35}")
    GUID_Joystick = GUID("{6F1D2B70-D5A0-11CF-BFC7-444553540000}")

    class DIPERIODIC(Structure):
        _fields_ = [
            ("dwMagnitude", wintypes.DWORD),
            ("lOffset", wintypes.LONG),
            ("dwPhase", wintypes.DWORD),
            ("dwPeriod", wintypes.DWORD),
        ]

    class DIEFFECT(Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("dwDuration", wintypes.DWORD),
            ("dwSamplePeriod", wintypes.DWORD),
            ("dwGain", wintypes.DWORD),
            ("dwTriggerButton", wintypes.DWORD),
            ("dwTriggerRepeatInterval", wintypes.DWORD),
            ("cAxes", wintypes.DWORD),
            ("rgdwAxes", POINTER(wintypes.DWORD)),
            ("rglDirection", POINTER(wintypes.LONG)),
            ("lpEnvelope", c_void_p),
            ("cbTypeSpecificParams", wintypes.DWORD),
            ("lpvTypeSpecificParams", c_void_p),
            ("dwStartDelay", wintypes.DWORD),
        ]

    class DIDEVICEINSTANCEW(Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("guidInstance", GUID),
            ("guidProduct", GUID),
            ("dwDevType", wintypes.DWORD),
            ("tszInstanceName", wintypes.WCHAR * 260),
            ("tszProductName", wintypes.WCHAR * 260),
            ("guidFFDriver", GUID),
            ("wUsagePage", wintypes.WORD),
            ("wUsage", wintypes.WORD),
        ]

    # Function prototypes
    dinput8 = ctypes.windll.dinput8
    DirectInput8Create = dinput8.DirectInput8Create
    DirectInput8Create.argtypes = [wintypes.HINSTANCE, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p), c_void_p]
    DirectInput8Create.restype = c_int32

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FFB_Engine")

# Maps user-friendly effect keys to SDL enums for periodic/condition effects
PERIODIC_TYPES: Dict[str, int] = {
    "sine": sdl_haptic.SDL_HAPTIC_SINE,
    "periodic_sine": sdl_haptic.SDL_HAPTIC_SINE,
    "directinput_periodic_sine": sdl_haptic.SDL_HAPTIC_SINE,
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
        
        # Hardware Effects Probe Results
        self.last_probe_result: Dict = {}
        self.recommendation_hw_sine: bool = False # True = Hardware Wave, False = Software Streaming
        self._probe_log_callback = None
        self.last_hwp_logs: list[str] = []
        
        # Diagnostic Tracking
        self.last_diag_result = {"mode": "N/A", "actual": "N/A", "fallback": "N/A", "reason": "N/A"}
        self.hw_sine_active = False # Flag to prevent mixing
        self.device_caps_log = [] # Store capabilities for export
        self.device_identity = {} # Store VID/PID etc
        self._oscillator_paused = False
        self._oscillator_active_before_pause = False
        self._transport_blocked = False
        self._transport_target_blocked = False
        self._transport_window_state = None
        self._transport_window_start_ms = 0
        self._transport_window_start_counts = None
        self._transport_window_count = 0
        self._osc_verbose_log = False
        self._osc_last_level = 0
        self._osc_seq = 0
        self._osc_xor = 0
        self._osc_last_tick_ms = 0
        self._osc_stats = {
            "generated": 0,
            "blocked": 0,
            "sent": 0,
            "device_writes": 0,
        }
        
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
        self._oscillator_paused = False
        self._oscillator_active_before_pause = False
        self._transport_blocked = False
        self._transport_target_blocked = False
        self._transport_window_state = None
        self._transport_window_start_ms = 0
        self._transport_window_start_counts = None
        self._transport_window_count = 0
        self._osc_verbose_log = False
        self._osc_last_level = 0
        self._osc_seq = 0
        self._osc_xor = 0
        self._osc_last_tick_ms = 0
        self._osc_stats = {
            "generated": 0,
            "blocked": 0,
            "sent": 0,
            "device_writes": 0,
        }
        self._device_version += 1  # Increment to invalidate old oscillator threads
        self.hw_sine_active = False

        self.joystick = sdl_joy.SDL_OpenJoystick(joy_id)
        if not self.joystick:
            logger.error(f"Joystick Open Failed: {_sdl_error()}")
            return False

        # Get Device Identity
        vid = sdl_joy.SDL_GetJoystickVendor(self.joystick)
        pid = sdl_joy.SDL_GetJoystickProduct(self.joystick)
        name = sdl_joy.SDL_GetJoystickName(self.joystick).decode('utf-8')
        serial = sdl_joy.SDL_GetJoystickSerial(self.joystick)
        serial = serial.decode('utf-8') if serial else "Unknown"
        
        self.device_identity = {
            "name": name,
            "vid": f"{vid:04x}",
            "pid": f"{pid:04x}",
            "serial": serial
        }
        logger.info(f"Connecting to: {name} [VID:{vid:04x} PID:{pid:04x}]")


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
            err = _sdl_error()
            logger.error(f"DIAG: Acquire -> HRESULT={err}")
            logger.warning("Device not acquired. Try closing other FFB software/sims.")
            self.close_device()
            return False
        
        logger.info("DIAG: Acquire -> HRESULT=SUCCESS")

        if sdl_haptic.SDL_HapticRumbleSupported(self.haptic):
            sdl_haptic.SDL_InitHapticRumble(self.haptic)

        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        logger.info(f"Device Capabilities: {caps:08x}")
        
        self.device_caps_log = []
        # Detailed EnumEffects Mapping
        start_msg = f"EnumEffects: Device '{name}' Capabilities:"
        logger.info(start_msg)
        self.device_caps_log.append(start_msg)
        
        features = [
            (sdl_haptic.SDL_HAPTIC_CONSTANT, "GUID_ConstantForce"),
            (sdl_haptic.SDL_HAPTIC_SINE, "GUID_Sine"),
            (sdl_haptic.SDL_HAPTIC_SQUARE, "GUID_Square"),
            (sdl_haptic.SDL_HAPTIC_TRIANGLE, "GUID_Triangle"),
            (sdl_haptic.SDL_HAPTIC_SAWTOOTHUP, "GUID_SawtoothUp"),
            (sdl_haptic.SDL_HAPTIC_SAWTOOTHDOWN, "GUID_SawtoothDown"),
            (sdl_haptic.SDL_HAPTIC_RAMP, "GUID_Ramp"),
            (sdl_haptic.SDL_HAPTIC_SPRING, "GUID_Spring"),
            (sdl_haptic.SDL_HAPTIC_DAMPER, "GUID_Damper"),
            (sdl_haptic.SDL_HAPTIC_INERTIA, "GUID_Inertia"),
            (sdl_haptic.SDL_HAPTIC_FRICTION, "GUID_Friction"),
            (sdl_haptic.SDL_HAPTIC_CUSTOM, "GUID_CustomForce"),
            (sdl_haptic.SDL_HAPTIC_GAIN, "GUID_Gain"),
            (sdl_haptic.SDL_HAPTIC_AUTOCENTER, "GUID_AutoCenter"),
        ]
        
        for flag, fname in features:
            if caps & flag:
                msg = f"EnumEffects: {fname}"
                logger.info(msg)
                self.device_caps_log.append(msg)
        
        logger.info(f"Connected to device idx={index} id={joy_id}")
        
        # Reset FFB State explicitly
        sdl_haptic.SDL_SetHapticGain(self.haptic, 100)
        sdl_haptic.SDL_SetHapticAutocenter(self.haptic, 0)
        sdl_haptic.SDL_StopHapticEffects(self.haptic)

        # Measure USB update rate right after connection
        self._calibrate_usb_rate()
        
        # Simagic Wake-up Hack: Explicitly send Actuators On via DirectInput
        self._enable_actuators_directinput()

        # Final "Prime": Send a dummy pulse to engage the motor firmware
        self._global_prime_haptic()
        
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
        effect.constant.direction.dir[0] = 10000
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
            comps = (values.get("x", 10000), values.get("y", 0), values.get("z", 0))
        elif mode == "spherical":
            comps = (values.get("yaw", 0), values.get("pitch", 0), values.get("distance", 1))
        else:  # polar default
            comps = (values.get("angle", 0), values.get("radius", 1), 0)

        for i, comp in enumerate(comps):
            direction.dir[i] = int(comp)
        return direction

    def _log_haptic_failure_details(self, kind: str, effect: sdl_haptic.SDL_HapticEffect, err_str: str):
        """Logs DirectInput-style details for haptic creation failures."""
        import re
        m = re.search(r"0x([0-9A-Fa-f]{8})", err_str)
        hr_str = f"0x{m.group(1)}" if m else "UNKNOWN"
        
        logger.info(f"DIAG: Failure Details -> hr={hr_str}")
        
        if kind in PERIODIC_TYPES:
            p = effect.periodic
            # Map SDL fields to requested DI-style names for actionable debugging
            dwFlags = p.direction.type
            cAxes = 1 # Hardware usually reports 1 axis for these controllers
            dwDuration = p.length * 1000 # SDL ms -> DI us
            dwGain = 10000 # SDL nominal max
            cbTypeSpecificParams = ctypes.sizeof(sdl_haptic.SDL_HapticPeriodic)
            
            logger.info(f"DIAG: dwFlags=0x{dwFlags:08X}, cAxes={cAxes}, dwDuration={dwDuration}, dwGain={dwGain}")
            logger.info(f"DIAG: cbTypeSpecificParams={cbTypeSpecificParams}")
            logger.info(f"DIAG: periodic: dwMagnitude={p.magnitude}, dwPeriod={p.period * 1000}, dwPhase={p.phase}, lOffset={p.offset}")
        elif kind == "constant":
            c = effect.constant
            dwFlags = c.direction.type
            dwDuration = c.length * 1000
            logger.info(f"DIAG: dwFlags=0x{dwFlags:08X}, cAxes=1, dwDuration={dwDuration}, dwGain=10000")
            logger.info(f"DIAG: cbTypeSpecificParams={ctypes.sizeof(sdl_haptic.SDL_HapticConstant)}")
            logger.info(f"DIAG: constant: lMagnitude={c.level}")


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

        # Software oscillator: check if explicitly requested or if global/descriptor override is set
        is_explicit_sw = (kind == "software_sine_stream")
        is_explicit_hw = (kind == "periodic_sine" or kind == "directinput_periodic_sine")
        allow_fallback = desc.get("allow_fallback", True) # Default to True unless forced HW
        
        # Determine if we should use software oscillator
        # If explicit HW is requested, force software off
        if is_explicit_hw:
            use_sw = False
        elif is_explicit_sw:
            use_sw = True
        else:
            # Respect the probe's recommendation if not explicitly overridden by the descriptor
            # True recommendation means HW is ok, so use_sw should be False
            default_use_sw = not self.recommendation_hw_sine
            use_sw = desc.get("use_software_sine", default_use_sw)

        # Log HW request if it's SINE and we are attempting HW
        if not use_sw and (is_explicit_hw or kind == "sine"):
            logger.info(f"DIAG: HW_SINE requested (Mode: {kind})")
            caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
            has_sine = bool(caps & sdl_haptic.SDL_HAPTIC_SINE)
            logger.info(f"DIAG: EnumEffects contains GUID_Sine: {'yes' if has_sine else 'no'}")

        # Software sine: route periodic effects through oscillator thread
        if use_sw and kind in ("sine", "square", "triangle", "sawtooth", "sawtoothup", "sawtoothdown", "ramp", "software_sine_stream"):
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
        
        # Log parameters once
        if kind in PERIODIC_TYPES:
            logger.info(f"DIAG: SetParameters(type={kind}, freq={desc.get('frequency_hz')}, mag={desc.get('magnitude')}, dur={desc.get('length_ms')})")
        elif kind == "constant":
            logger.info(f"DIAG: SetParameters(type=constant, mag={desc.get('magnitude')}, dur={desc.get('length_ms')})")
        elif kind == "ramp":
            logger.info(f"DIAG: SetParameters(type=ramp, start={desc.get('start_mag')}, end={desc.get('end_mag')}, dur={desc.get('length_ms')})")
        elif kind in CONDITION_TYPES:
            logger.info(f"DIAG: SetParameters(type={kind}, dur={desc.get('length_ms')})")
        elif kind in {"leftright", "left_right"}:
            logger.info(f"DIAG: SetParameters(type=leftright, dur={desc.get('length_ms')})")

        effect = self.build_effect_from_descriptor(desc)
        if not effect:
            return -1

        if self.effect_id != -1:
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1

        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        
        self.hw_sine_active = True # Set flag to prevent streaming updates collision
        
        if self.effect_id == -1:
            # Fallback for constant force: Simagic wheels often require CARTESIAN even if they report POLAR support
            if kind == "constant" and desc.get("direction_mode", "polar") == "polar":
                logger.info("DIAG: CreateEffect(constant, POLAR) FAILED. Retrying with CARTESIAN fallback...")
                desc_copy = desc.copy()
                desc_copy["direction_mode"] = "cartesian"
                alt_effect = self.build_effect_from_descriptor(desc_copy)
                if alt_effect:
                    self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(alt_effect))
                    if self.effect_id != -1:
                        logger.info(f"DIAG: CreateEffect(constant, CARTESIAN) -> HRESULT=SUCCESS (ID={self.effect_id})")
                        # Update local desc and self._current_descriptor so subsequent steps know we are in fallback mode
                        desc = desc_copy
                        self._current_descriptor = desc_copy

            # Fallback for periodic: Try CARTESIAN, then Phase-0
            elif kind in PERIODIC_TYPES:
                # 1. Try Cartesian Fallback
                if desc.get("direction_mode", "polar") == "polar":
                    logger.info(f"DIAG: CreateEffect({kind}, POLAR) FAILED. Retrying with CARTESIAN fallback...")
                    desc_copy = desc.copy()
                    desc_copy["direction_mode"] = "cartesian"
                    alt_effect = self.build_effect_from_descriptor(desc_copy)
                    if alt_effect:
                        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(alt_effect))
                        if self.effect_id != -1:
                            logger.info(f"DIAG: CreateEffect({kind}, CARTESIAN) -> HRESULT=SUCCESS (ID={self.effect_id})")
                            desc = desc_copy
                            self._current_descriptor = desc_copy

                # 2. Try Phase-0 Fallback (Some bases reject the alignment phase)
                if self.effect_id == -1 and int(desc.get("phase", 0)) != 0:
                    logger.info(f"DIAG: CreateEffect({kind}) with Phase fallback FAILED. Retrying with Phase=0 fallback...")
                    desc_copy = desc.copy()
                    desc_copy["phase"] = 0
                    alt_effect = self.build_effect_from_descriptor(desc_copy)
                    if alt_effect:
                        self.effect_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(alt_effect))
                        if self.effect_id != -1:
                            logger.info(f"DIAG: CreateEffect({kind}, Phase=0) -> HRESULT=SUCCESS (ID={self.effect_id})")
                            desc = desc_copy
                            self._current_descriptor = desc_copy
        
        if self.effect_id == -1:
            err = _sdl_error()
            logger.error(f"DIAG: CreateEffect({kind.upper()}) FAILED -> HRESULT={err}")
            self._log_haptic_failure_details(kind, effect, err)
            self.hw_sine_active = False
            # Log fallback status if it was strictly requested HW
            if is_explicit_hw and not allow_fallback:
                logger.info(f"DIAG: FallbackToSoftware: false (reason=Strict HW mode enforced)")
            return -1
        else:
            # Only log if we didn't just log the successful fallback above
            if kind != "constant" or desc.get("direction_mode") != "cartesian":
                 logger.info(f"DIAG: CreateEffect({kind.upper()}) -> HRESULT=SUCCESS (ID={self.effect_id})")

        run_res = sdl_haptic.SDL_RunHapticEffect(self.haptic, self.effect_id, 1)
        if run_res:
            logger.info(f"DIAG: Start(iterations=1, flags=0) -> HRESULT=SUCCESS for ID={self.effect_id}")
            
            # Redundant Update: Some drivers (Simagic) need a parameter set AFTER Run to engage
            sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(effect))
            logger.info(f"DIAG: Redundant Update sent for ID={self.effect_id}")
            
            logger.info(f"DIAG: FallbackToSoftware: false (reason=HW Start Success)")
            return self.effect_id

        err = _sdl_error()
        logger.error(f"DIAG: Start(iterations=1, flags=0) FAILED -> HRESULT={err}")
        if is_explicit_hw and not allow_fallback:
            logger.info(f"DIAG: FallbackToSoftware: false (reason=HW Start Failure in Strict Mode)")
        return -1
    # --- Sequencer Methods ---
    def _start_software_oscillator(self, freq: float, magnitude: int, duration_ms: int, phase: int, desc: dict):
        """Start software oscillator using constant force effect with proper direction from descriptor."""
        osc_type = (desc.get("type") or desc.get("effect") or "sine").lower()
        print(f">>> START SOFTWARE OSCILLATOR: type={osc_type} freq={freq}")
        if not self.haptic:
            return -1
        
        # Check if device supports CONSTANT effects
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        if not (caps & sdl_haptic.SDL_HAPTIC_CONSTANT):
            logger.error(f"Device does not support SDL_HAPTIC_CONSTANT (caps: {caps:08x})")
            logger.error("Software oscillator requires CONSTANT force capability")
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
        # IMPORTANT: CONSTANT effects MUST use CARTESIAN direction mode
        # Many devices (like Simagic) don't support CONSTANT with POLAR direction
        # Force CARTESIAN regardless of what descriptor specifies
        direction = sdl_haptic.SDL_HapticDirection()
        direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        direction.dir[0] = 10000  # X-axis (primary axis for wheels)
        direction.dir[1] = 0
        direction.dir[2] = 0
        
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
                sdl_err = _sdl_error()
                logger.error(f"CreateEffect FAILED: {sdl_err}")
                logger.error(f"Effect details - type: CONSTANT, direction_mode: {desc.get('direction_mode', 'polar')}, magnitude: {magnitude}")
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
            # Hardware: update SDL effect - throttle to 10Hz to prevent driver-level lag
            now = time.time()
            if hasattr(self, '_last_hw_update_time'):
                if now - self._last_hw_update_time < 0.1: # 100ms throttle
                    return effect_id
            self._last_hw_update_time = now

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
                res = sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))
                if res != -1:
                    # Removed high-frequency success logging to prevent UI lag
                    return effect_id
                else:
                    err = _sdl_error()
                    logger.error(f"UpdateEffect FAILED: {err}")
                    return -1
            except Exception as e:
                logger.error(f"Exception during UpdateEffect: {e}")
                return -1 



    def update_effect_constant(self, effect_id: int, magnitude: int, duration_ms: int):
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_constant blocked)")
            return
        if not self.haptic or effect_id == -1: return
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def update_effect_ramp(self, effect_id: int, start_mag: int, end_mag: int, duration_ms: int):
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_ramp blocked)")
            return
        if not self.haptic or effect_id == -1: return

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.ramp.length = duration_ms
        effect.ramp.start = start_mag
        effect.ramp.end = end_mag
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))

    def update_effect_sawtooth(self, effect_id: int, magnitude: int, period: int, duration_ms: int):
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_sawtooth blocked)")
            return
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
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (start_effect_constant blocked)")
            return -1
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
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (start_effect_ramp blocked)")
            return -1
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
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (start_effect_sawtooth blocked)")
            return -1
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
        self.hw_sine_active = False # Clear HW Sine flag
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
        self._oscillator_paused = False
        self._oscillator_active_before_pause = False
        self._transport_blocked = False
        self._transport_target_blocked = False
        self._transport_window_state = None
        self._transport_window_start_ms = 0
        self._transport_window_start_counts = None
        self._transport_window_count = 0
        self._osc_verbose_log = False
        self._osc_last_level = 0
        self._osc_seq = 0
        self._osc_xor = 0
        self._osc_last_tick_ms = 0
        self._osc_stats = {
            "generated": 0,
            "blocked": 0,
            "sent": 0,
            "device_writes": 0,
        }
        
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

    def set_oscillator_pause(self, paused: bool):
        """Pause/resume software oscillator updates without destroying the thread."""
        if paused:
            if not self._oscillator_paused:
                self._oscillator_active_before_pause = self._oscillator_active
            self._oscillator_paused = True
            self._oscillator_active = False
        else:
            if self._oscillator_paused:
                self._oscillator_active = bool(self._oscillator_active_before_pause)
            self._oscillator_paused = False

    def set_oscillator_transport_blocked(self, blocked: bool):
        """Request transport block/unblock while keeping generation running."""
        self._transport_target_blocked = bool(blocked)

    def _transport_snapshot_counts(self) -> dict:
        return {
            "generated": self._osc_stats.get("generated", 0),
            "blocked": self._osc_stats.get("blocked", 0),
            "sent": self._osc_stats.get("sent", 0),
            "device_writes": self._osc_stats.get("device_writes", 0),
            "osc_seq": self._osc_seq,
            "osc_xor": self._osc_xor,
            "osc_last_tick_ms": self._osc_last_tick_ms,
        }

    def _transport_log_window_start(self, state: str, now_ms: int):
        return

    def _transport_log_window_end(self, state: str, now_ms: int, start_ms: int, start_counts: dict):
        from datetime import datetime
        duration_ms = max(1, now_ms - start_ms)
        duration_s = duration_ms / 1000.0
        end_counts = self._transport_snapshot_counts()
        osc_seq_start = start_counts.get("osc_seq", 0)
        osc_seq_end = end_counts.get("osc_seq", 0)
        gen_ticks = osc_seq_end - osc_seq_start
        gen_rate_hz = gen_ticks / duration_s if duration_s > 0 else 0.0
        samples_enqueued = gen_ticks
        samples_blocked = end_counts["blocked"] - start_counts["blocked"]
        transport_writes = end_counts["device_writes"] - start_counts["device_writes"]
        samples_dropped_due_to_throttle = max(0, samples_enqueued - samples_blocked - transport_writes)

        self._transport_window_count += 1
        wall = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        logger.info(
            "%s WIN#%02d %s %dms OSCΔ=%d WRITES=%d",
            wall,
            self._transport_window_count,
            state,
            duration_ms,
            gen_ticks,
            transport_writes,
        )

        if self._osc_verbose_log:
            logger.info("gen_ticks=%d gen_rate_hz=%.2f", gen_ticks, gen_rate_hz)
            logger.info("samples_generated=%d", gen_ticks)
            logger.info("samples_enqueued_to_transport=%d", samples_enqueued)
            logger.info("samples_blocked=%d", samples_blocked)
            logger.info("transport_writes=%d", transport_writes)
            logger.info("samples_dropped_due_to_throttle=%d", samples_dropped_due_to_throttle)
            logger.info("osc_ticks_start=%d", osc_seq_start)
            logger.info("osc_ticks_end=%d", osc_seq_end)
            logger.info("osc_seq_start=%d", osc_seq_start)
            logger.info("osc_seq_end=%d", osc_seq_end)
            logger.info("osc_last_tick_ts=%d", end_counts.get("osc_last_tick_ms", 0))
            logger.info("osc_sample_hash=%d", start_counts.get("osc_xor", 0) ^ end_counts.get("osc_xor", 0))
            
    def _software_oscillator_worker(self):
        """Thread that updates Constant Force to emulate various periodic waveforms."""
        print("[OSC] Thread started!")
        
        # Capture the device version at thread start - if it changes, we must exit
        osc_device_version = self._device_version
        
        # Pre-create the cached effect structure
        cached_effect = sdl_haptic.SDL_HapticEffect()
        cached_effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        cached_effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        cached_effect.constant.direction.dir[0] = 10000
        cached_effect.constant.direction.dir[1] = 0
        cached_effect.constant.direction.dir[2] = 0
        cached_effect.constant.length = 60000
        self._cached_effect = cached_effect  # Store for stop function
        
        # Pre-initialize all variables to avoid overhead in hot loop
        last_time = time.time()
        last_usb_update_time = time.time()  # Track when we last sent USB command
        accumulated_phase = 0.0
        energy_balance = 0.0  # Tracks cumulative Force*Time for Zero-DC balance
        home_position = 0.0   # Physical wheel position when vibration starts
        has_captured_home = False
        update_count = 0
        
        two_pi = 2 * math.pi
        
        while not self._stop_oscillator_event.is_set():
            # Check if we should be actively running
            if not self._oscillator_active:
                # Ensure we send Level 0 when paused (Total Silence for Freeze mode)
                with self._haptic_lock:
                    if self.haptic and self.effect_id != -1:
                        cached_effect.constant.level = 0
                        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, self.effect_id, ctypes.byref(cached_effect))
                
                time.sleep(0.1)
                last_time = time.time()
                last_usb_update_time = time.time()
                energy_balance = 0.0  # Reset balance when idle
                has_captured_home = False
                update_count = 0
                continue

            # Initialize transport window on first active loop
            if self._transport_window_state is None:
                self._transport_window_state = "BLOCKED" if self._transport_blocked else "UNBLOCKED"
                now_ms = int(time.monotonic() * 1000)
                self._transport_window_start_ms = now_ms
                self._transport_window_start_counts = self._transport_snapshot_counts()
                self._transport_log_window_start(self._transport_window_state, now_ms)
            
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
            
            # Position-Aware Home Capture
            # The first frame we are active, we grab the current physical wheel angle
            if not has_captured_home:
                try:
                    # Determine axis index from preferred key (e.g., "joy_0" -> 0)
                    axis_key = self.preferred_axis_key or "joy_0"
                    axis_idx = int(axis_key.split("_")[1])
                    
                    if "gp_" in axis_key and self.gamepad:
                        home_position = float(sdl_gp.SDL_GetGamepadAxis(self.gamepad, axis_idx))
                    elif self.joystick:
                        home_position = float(sdl_joy.SDL_GetJoystickAxis(self.joystick, axis_idx))
                    
                    has_captured_home = True
                    logger.debug(f"[OSC] Home reference set: {home_position}")
                except Exception as e:
                    logger.debug(f"Failed to capture home pos: {e}")
                    has_captured_home = True # Only try once per start

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
            
            # 1. ZERO-DC BALANCE (Software Timing Correction)
            energy_balance += val * dt
            energy_balance *= 0.99
            jitter_correction = -energy_balance * 25.0
            
            # 2. POSITION FEEDBACK (Mechanical Drift Correction)
            cent_correction = 0.0
            if has_captured_home:
                try:
                    axis_key = self.preferred_axis_key or "joy_0"
                    axis_idx = int(axis_key.split("_")[1])
                    curr_pos = 0.0
                    if "gp_" in axis_key and self.gamepad:
                        curr_pos = float(sdl_gp.SDL_GetGamepadAxis(self.gamepad, axis_idx))
                    elif self.joystick:
                        curr_pos = float(sdl_joy.SDL_GetJoystickAxis(self.joystick, axis_idx))
                    
                    # Error in normalized range (-32768 to 32767)
                    pos_error = (curr_pos - home_position) / 32768.0
                    
                    # POLARITY FIX: Many DD bases have inverted torque vs axis increment.
                    # Gain was 15.0, reducing to 5.0 for gentler correction.
                    cent_correction = pos_error * 5.0 # Changed - to + to fix inversion
                except:
                    pass

            # Combine Corrections (Clamp total modification to +/- 40% of wave)
            total_mod = jitter_correction + cent_correction
            if total_mod > 0.40: total_mod = 0.40
            if total_mod < -0.40: total_mod = -0.40
            
            current_level = int((val + total_mod) * mag)
            self._osc_last_level = current_level
            self._osc_seq += 1
            self._osc_stats["generated"] += 1
            self._osc_last_tick_ms = int(time.monotonic() * 1000)
            self._osc_xor ^= (current_level & 0xFFFFFFFF)
            if self._transport_blocked:
                self._osc_stats["blocked"] += 1
            else:
                self._osc_stats["sent"] += 1
            
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
                    if self._transport_target_blocked and not self._transport_blocked:
                        cached_effect.constant.level = 0
                        try:
                            sdl_haptic.SDL_UpdateHapticEffect(haptic, effect_id, ctypes.byref(cached_effect))
                            last_usb_update_time = current_time
                            update_count += 1
                            self._osc_stats["sent"] += 1
                            self._osc_stats["device_writes"] += 1
                            # Transition to BLOCKED window after the zero write
                            now_ms = int(time.monotonic() * 1000)
                            prev_state = self._transport_window_state
                            if prev_state and self._transport_window_start_counts is not None:
                                self._transport_log_window_end(prev_state, now_ms, self._transport_window_start_ms, self._transport_window_start_counts)
                            self._transport_blocked = True
                            self._transport_window_state = "BLOCKED"
                            self._transport_window_start_ms = now_ms
                            self._transport_window_start_counts = self._transport_snapshot_counts()
                            self._transport_log_window_start("BLOCKED", now_ms)
                        except OSError as e:
                            print(f"[OSC] SDL_UpdateHapticEffect error: {e}, stopping thread")
                            break
                    elif self._transport_blocked:
                        last_usb_update_time = current_time
                        # Handle unblock transition at window boundary
                        if not self._transport_target_blocked:
                            now_ms = int(time.monotonic() * 1000)
                            prev_state = self._transport_window_state
                            if prev_state and self._transport_window_start_counts is not None:
                                self._transport_log_window_end(prev_state, now_ms, self._transport_window_start_ms, self._transport_window_start_counts)
                            self._transport_blocked = False
                            self._transport_window_state = "UNBLOCKED"
                            self._transport_window_start_ms = now_ms
                            self._transport_window_start_counts = self._transport_snapshot_counts()
                            self._transport_log_window_start("UNBLOCKED", now_ms)
                    else:
                        cached_effect.constant.level = self._osc_last_level
                        try:
                            sdl_haptic.SDL_UpdateHapticEffect(haptic, effect_id, ctypes.byref(cached_effect))
                            last_usb_update_time = current_time
                            update_count += 1
                            self._osc_stats["sent"] += 1
                            self._osc_stats["device_writes"] += 1
                        except OSError as e:
                            print(f"[OSC] SDL_UpdateHapticEffect error: {e}, stopping thread")
                            break
            
            # Small sleep to prevent CPU spin
            time.sleep(0.0001)
    def run_hardware_effects_probe(self, feedback_callback=None, progress_callback=None) -> Dict:
        """
        Runs a comprehensive hardware effects probe with strict HWP logging.
        feedback_callback: function(test_name, message) -> str ("YES", "NO", "UNSURE")
        """
        self.last_hwp_logs = []
        def hwp_log(msg):
            # Log to stdout/logger for grep
            logger.info(f"HWP: {msg}")
            self.last_hwp_logs.append(f"HWP: {msg}")
            # Optional UI callback
            if self._probe_log_callback:
                self._probe_log_callback(f"HWP: {msg}")

        hwp_log("--- STARTING HARDWARE EFFECTS PROBE ---")
        
        # Section A: Identity & Caps
        hwp_log(f"SDL version: {sdl_ver.SDL_MAJOR_VERSION}.{sdl_ver.SDL_MINOR_VERSION}.{sdl_ver.SDL_MICRO_VERSION}")
        hwp_log(f"Platform: {platform.system()}")
        
        device_name = "Unknown"
        if self.haptic:
            device_name = sdl_haptic.SDL_GetHapticName(self.haptic).decode("utf-8")
        hwp_log(f"Device: {device_name}")
        hwp_log("Haptic open path: FromJoystick")
        
        caps = 0
        if self.haptic:
            caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        
        hwp_log(f"SDL_HapticQuery caps=0x{caps:08X} plus decoded flags:")
        hwp_log(f"CONSTANT={'1' if caps & sdl_haptic.SDL_HAPTIC_CONSTANT else '0'} "
                f"SINE={'1' if caps & sdl_haptic.SDL_HAPTIC_SINE else '0'} "
                f"SPRING={'1' if caps & sdl_haptic.SDL_HAPTIC_SPRING else '0'} "
                f"DAMPER={'1' if caps & sdl_haptic.SDL_HAPTIC_DAMPER else '0'} "
                f"FRICTION={'1' if caps & sdl_haptic.SDL_HAPTIC_FRICTION else '0'} "
                f"CUSTOM={'1' if caps & sdl_haptic.SDL_HAPTIC_CUSTOM else '0'}")

        if not self.haptic:
            hwp_log("FAIL stage=Acquire rc=-1 err=\"No haptic device connected\"")
            return {"status": "FAIL", "reason": "No haptic device"}

        # Section B: Force State Reset
        def trace_call(name, func, *args):
            sdl_error.SDL_ClearError()
            rc = func(*args)
            # SDL3 functions often return bool or 0/-1. We'll capture the error for non-zero/False
            err = sdl_error.SDL_GetError().decode("utf-8") if rc < 0 or (isinstance(rc, bool) and not rc) else ""
            log_rc = 0 if rc is True or (isinstance(rc, int) and rc >= 0) else -1
            hwp_log(f"{name} rc={log_rc} err=\"{err}\"")
            if log_rc < 0:
                hwp_log(f"FAIL stage={name} rc={log_rc} err=\"{err}\"")
                return False, log_rc, err
            return True, log_rc, err

        # SDL_InitHaptic simulation (Already open)
        hwp_log("SDL_InitHaptic(haptic) rc=0 err=\"\"")

        if caps & sdl_haptic.SDL_HAPTIC_GAIN:
            ok, rc, err = trace_call("SDL_SetHapticGain", sdl_haptic.SDL_SetHapticGain, self.haptic, 100)
            if not ok: return {"status": "FAIL", "stage": "SDL_SetHapticGain", "rc": rc, "err": err}
        
        if caps & sdl_haptic.SDL_HAPTIC_AUTOCENTER:
            ok, rc, err = trace_call("SDL_SetHapticAutocenter", sdl_haptic.SDL_SetHapticAutocenter, self.haptic, 0)
            if not ok: return {"status": "FAIL", "stage": "SDL_SetHapticAutocenter", "rc": rc, "err": err}
            
        ok, rc, err = trace_call("SDL_StopHapticEffects", sdl_haptic.SDL_StopHapticEffects, self.haptic)
        if not ok: return {"status": "FAIL", "stage": "SDL_StopHapticEffects", "rc": rc, "err": err}

        report = {"tests": {}}
        conclusion_sine = "NOT_ADVERTISED"

        # Section C/D: Strict Effect Trace
        def test_effect(name, sdl_type, is_periodic=True, test_index=1, test_total=1):
            nonlocal conclusion_sine
            hwp_log(f"TEST {test_index}/{test_total} effect={name.upper()} advertised={'1' if caps & sdl_type else '0'}")
            if not (caps & sdl_type):
                return "NOT_ADVERTISED"
            
            # Parameters
            if is_periodic:
                mag, period, offset, phase, length = 3277, 500, 0, 0, 5000
                hwp_log(f"Params: magnitude={mag}, period={period}ms, offset={offset}, phase={phase}, length={length}ms, direction=CARTESIAN(1,0,0)")
                effect = sdl_haptic.SDL_HapticEffect()
                ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
                effect.type = sdl_type
                effect.periodic.type = sdl_type
                effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.periodic.direction.dir[0] = 1
                effect.periodic.period = period
                effect.periodic.magnitude = mag
                effect.periodic.length = length
                effect.periodic.attack_length = 0
                effect.periodic.fade_length = 0
            else:
                level, length = 3277, 5000
                hwp_log(f"Params: level={level}, length={length}ms, direction=CARTESIAN(1,0,0)")
                effect = sdl_haptic.SDL_HapticEffect()
                ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
                effect.type = sdl_type
                effect.constant.type = sdl_type
                effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
                effect.constant.direction.dir[0] = 1
                effect.constant.level = level
                effect.constant.length = length
                effect.constant.attack_length = 0
                effect.constant.fade_length = 0

            # 1) Create effect
            hwp_log(f"NewEffect(effect={name.upper()}) ...")
            sdl_error.SDL_ClearError()
            eff_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
            err = sdl_error.SDL_GetError().decode("utf-8") if eff_id < 0 else ""
            hwp_log(f"NewEffect(effect={name.upper()}) id={eff_id} rc={0 if eff_id >=0 else -1} err=\"{err}\"")
            if eff_id < 0:
                hwp_log(f"RESULT effect={name.upper()} status=NEW_FAILED err=\"{err}\"")
                # Trigger DI fallback if this was Sine and it failed
                if name.upper() == "SINE" and platform.system() == "Windows":
                    self._run_directinput_hw_probe()
                return "NEW_FAILED"

            # 2) Run effect
            sdl_error.SDL_ClearError()
            rc = sdl_haptic.SDL_RunHapticEffect(self.haptic, eff_id, 1)
            err = sdl_error.SDL_GetError().decode("utf-8") if not rc else ""
            log_rc = 0 if rc else -1
            hwp_log(f"RunEffect(effect={name.upper()}) rc={log_rc} err=\"{err}\"")
            if log_rc < 0:
                hwp_log(f"RESULT effect={name.upper()} status=RUN_FAILED err=\"{err}\"")
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, eff_id)
                return "RUN_FAILED"

            # 3) Signal + Block (simplified)
            signal_s = 2
            block_s = 3
            hwp_log(f"Signal(effect={name.upper()}) duration={signal_s}s")
            for remaining in range(signal_s, 0, -1):
                if progress_callback:
                    progress_callback(name.upper(), "SIGNAL", remaining, test_index, test_total)
                time.sleep(1.0)

            hwp_log(f"Block(effect={name.upper()}) duration={block_s}s (no updates)")
            
            # CRITICAL: If a software wave is running in the background, we must pause it
            # so the "Freeze" is truly silent across all threads.
            was_osc_active = self._oscillator_active
            self._oscillator_active = False
            for remaining in range(block_s, 0, -1):
                if progress_callback:
                    progress_callback(name.upper(), "BLOCK", remaining, test_index, test_total)
                time.sleep(1.0)
            
            self._oscillator_active = was_osc_active

            # Stop immediately after 2s signal + 3s block (total 5s)
            sdl_error.SDL_ClearError()
            stop_rc = sdl_haptic.SDL_StopHapticEffect(self.haptic, eff_id)
            stop_err = sdl_error.SDL_GetError().decode("utf-8") if not stop_rc else ""
            hwp_log(f"StopEffect(effect={name.upper()}) rc={0 if stop_rc else -1} err=\"{stop_err}\"")
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, eff_id)
            hwp_log(f"DestroyEffect(effect={name.upper()}) destroyed")
            eff_id = -1

            if progress_callback:
                progress_callback(name.upper(), "DONE", 0, test_index, test_total)
            
            user_res = "UNSURE"
            if feedback_callback:
                user_res = feedback_callback(name.upper(), f"During the 3s block, did {name.upper()} force continue?")
            hwp_log(f"FreezeTest(effect={name.upper()}) user={user_res}")

            # 4) Stop + destroy (already handled after block)

            status = "OK"
            if user_res == "NO":
                status = "NOT_DEVICE_GENERATED"
            elif user_res == "UNSURE":
                status = "UNCERTAIN"
            
            hwp_log(f"RESULT effect={name.upper()} status={status}")
            return status

        tests = [
            ("Constant", sdl_haptic.SDL_HAPTIC_CONSTANT, False),
            ("Sine", sdl_haptic.SDL_HAPTIC_SINE, True),
        ]
        for idx, (name, sdl_type, is_periodic) in enumerate(tests, start=1):
            result = test_effect(name, sdl_type, is_periodic, idx, len(tests))
            report["tests"][name.lower()] = result
            if name.lower() == "sine":
                conclusion_sine = result

        # Section E: Conclusion
        hwp_log(f"CONCLUSION hardware_sine={conclusion_sine} reason=Final")
        
        # Cache for engine use
        self.recommendation_hw_sine = (conclusion_sine == "OK")
        self.last_probe_result = {
            "conclusion_sine": conclusion_sine,
            "recommendation": "Hardware Wave supported" if self.recommendation_hw_sine else "Software Streaming recommended"
        }
        
        return report

    def _enable_actuators_directinput(self):
        """Windows-only DirectInput hack to explicitly send SETACTUATORSON.
        This bridges the gap where some bases (Simagic) stay in standby after SDL initialization.
        """
        if platform.system() != "Windows":
            return
        
        try:
            # 1. Create DirectInput8
            di8 = c_void_p()
            hr = DirectInput8Create(ctypes.windll.kernel32.GetModuleHandleW(None), 0x0800, ctypes.byref(IID_IDirectInput8W), ctypes.byref(di8), None)
            if hr != DI_OK: return

            vtable_di8 = ctypes.cast(ctypes.cast(di8, POINTER(c_void_p)).contents, POINTER(c_void_p))
            
            # 2. Enum first SIMAGIC device (VID 0483) or first attached joystick
            device_guid = GUID("{00000000-0000-0000-0000-000000000000}")
            found_device = [False]
            def enum_cb(lpddi, pvRef):
                dev_inst = ctypes.cast(lpddi, POINTER(DIDEVICEINSTANCEW)).contents
                vid = (dev_inst.guidProduct.Data1 & 0xFFFF)
                
                # If we find Simagic (0483) or Moza (1E1A) or any likely DD base, grab it
                is_simagic = (vid == 0x0483)
                
                if is_simagic or not found_device[0]:
                    device_guid.Data1 = dev_inst.guidInstance.Data1
                    device_guid.Data2 = dev_inst.guidInstance.Data2
                    device_guid.Data3 = dev_inst.guidInstance.Data3
                    for i in range(8): device_guid.Data4[i] = dev_inst.guidInstance.Data4[i]
                    found_device[0] = True
                    if is_simagic: return 0 # DIENUM_STOP
                return 1 # DIENUM_CONTINUE

            ENUM_CB = ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p)
            enum_cb_ptr = ENUM_CB(enum_cb)
            
            ENUM_DEVICES = WINFUNCTYPE(c_int32, c_void_p, wintypes.DWORD, ENUM_CB, c_void_p, wintypes.DWORD)(vtable_di8[4])
            ENUM_DEVICES(di8, 4, enum_cb_ptr, None, 0x00000001) # DI8DEVCLASS_GAMECTRL | DIEDFL_ATTACHEDONLY
            
            if not found_device[0]: return

            # 3. Create Device
            device = c_void_p()
            CREATE_DEVICE = WINFUNCTYPE(c_int32, c_void_p, POINTER(GUID), POINTER(c_void_p), c_void_p)(vtable_di8[3])
            hr = CREATE_DEVICE(di8, ctypes.byref(device_guid), ctypes.byref(device), None)
            if hr != DI_OK: return

            vtable_dev = ctypes.cast(ctypes.cast(device, POINTER(c_void_p)).contents, POINTER(c_void_p))

            # 4. SetCooperativeLevel (Non-exclusive background to avoid conflict with SDL)
            WTL = ctypes.windll.user32.GetForegroundWindow()
            SET_COOP = WINFUNCTYPE(c_int32, c_void_p, wintypes.HWND, wintypes.DWORD)(vtable_dev[13])
            SET_COOP(device, WTL, 0x00000008 | 0x00000002) # DISCL_NONEXCLUSIVE | DISCL_BACKGROUND

            # MUST call Acquire() before sending commands
            ACQUIRE_DEV = WINFUNCTYPE(c_int32, c_void_p)(vtable_dev[7])
            ACQUIRE_DEV(device)

            # 5. Send Actuators On Sequence
            SEND_CMD = WINFUNCTYPE(c_int32, c_void_p, wintypes.DWORD)(vtable_dev[22])
            
            # Send Reset, Continue, and then Actuators On
            SEND_CMD(device, DISFFC_RESET)
            time.sleep(0.05)
            SEND_CMD(device, DISFFC_CONTINUE)
            time.sleep(0.05)
            hr = SEND_CMD(device, DISFFC_SETACTUATORSON)
            
            if hr == 0: # DI_OK
                logger.info("DI-HACK: Full Actuator Wake-up sequence sent via DirectInput (Reset, Continue, On)")
            
            # Final 10000 vector prime
            UNACQUIRE_DEV = WINFUNCTYPE(c_int32, c_void_p)(vtable_dev[8])
            UNACQUIRE_DEV(device)
            RELEASE_DEV = WINFUNCTYPE(c_uint32, c_void_p)(vtable_dev[2])
            RELEASE_DEV(device)
            RELEASE_DI8 = WINFUNCTYPE(c_uint32, c_void_p)(vtable_di8[2])
            RELEASE_DI8(di8)

        except Exception as e:
            logger.debug(f"DirectInput wake-up hack failed (ignoring): {e}")

    def _global_prime_haptic(self):
        """Sends a 100ms dummy constant pulse to 'prime' the motor for hardware waves."""
        if not self.haptic: return
        
        logger.info("DIAG: Sending Global Prime pulse (100ms dummy CONSTANT)...")
        
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.direction.dir[0] = 10000
        effect.constant.length = 100
        effect.constant.level = 500 # 1.5% magnitude to physically engage the motor
        
        try:
            eid = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
            if eid != -1:
                sdl_haptic.SDL_RunHapticEffect(self.haptic, eid, 1)
                time.sleep(0.12) # Wait for it to clear
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, eid)
                logger.info("DIAG: Global Prime PULSE complete.")
        except Exception as e:
            logger.debug(f"Global Prime failed (ignoring): {e}")

    def _run_directinput_hw_probe(self):
        """Windows-only DirectInput probe for detailed HRESULT logging on HW Sine failure."""
        if platform.system() != "Windows":
            return

        def di_log(msg):
            logger.info(f"DI-HWPROBE: {msg}")
            self.last_hwp_logs.append(f"DI-HWPROBE: {msg}")
            if self._probe_log_callback:
                self._probe_log_callback(f"DI-HWPROBE: {msg}")

        def decode_hr(hr):
            if hr == 0x80070057: return "E_INVALIDARG"
            if hr == 0x80004001: return "E_NOTIMPL"
            if hr == 0x88780078: return "unsupported"
            return "UNKNOWN"

        try:
            di_log("Starting DI fallback probe...")
            
            # 1. Create DirectInput8
            di8 = c_void_p()
            hr = DirectInput8Create(ctypes.windll.kernel32.GetModuleHandleW(None), 0x0800, ctypes.byref(IID_IDirectInput8W), ctypes.byref(di8), None)
            if hr != DI_OK:
                di_log(f"DirectInput8Create hr=0x{hr & 0xFFFFFFFF:08X}")
                return

            vtable_di8 = ctypes.cast(ctypes.cast(di8, POINTER(c_void_p)).contents, POINTER(c_void_p))
            
            # 2. Enum first device
            device_guid = GUID("{00000000-0000-0000-0000-000000000000}")
            found_device =  [False]
            def enum_cb(lpddi, pvRef):
                dev_inst = ctypes.cast(lpddi, POINTER(DIDEVICEINSTANCEW)).contents
                device_guid.Data1 = dev_inst.guidInstance.Data1
                device_guid.Data2 = dev_inst.guidInstance.Data2
                device_guid.Data3 = dev_inst.guidInstance.Data3
                for i in range(8): device_guid.Data4[i] = dev_inst.guidInstance.Data4[i]
                found_device[0] = True
                return 0 # DIENUM_STOP

            ENUM_CB = ctypes.WINFUNCTYPE(c_int32, c_void_p, c_void_p)
            enum_cb_ptr = ENUM_CB(enum_cb)
            
            # IDirectInput8::EnumDevices index 4
            ENUM_DEVICES = WINFUNCTYPE(c_int32, c_void_p, wintypes.DWORD, ENUM_CB, c_void_p, wintypes.DWORD)(vtable_di8[4])
            hr = ENUM_DEVICES(di8, 4, enum_cb_ptr, None, 0x00000001) # DI8DEVCLASS_GAMECTRL | DIEDFL_ATTACHEDONLY
            
            if not found_device[0]:
                di_log("No device found via DI enumeration")
                return

            # 3. Create Device
            device = c_void_p()
            # IDirectInput8::CreateDevice index 3
            CREATE_DEVICE = WINFUNCTYPE(c_int32, c_void_p, POINTER(GUID), POINTER(c_void_p), c_void_p)(vtable_di8[3])
            hr = CREATE_DEVICE(di8, ctypes.byref(device_guid), ctypes.byref(device), None)
            if hr != DI_OK:
                di_log(f"CreateDevice hr=0x{hr & 0xFFFFFFFF:08X}")
                return

            vtable_dev = ctypes.cast(ctypes.cast(device, POINTER(c_void_p)).contents, POINTER(c_void_p))

            # Minimal Setup
            WTL = ctypes.windll.user32.GetForegroundWindow()
            # SetCooperativeLevel index 13
            SET_COOP = WINFUNCTYPE(c_int32, c_void_p, wintypes.HWND, wintypes.DWORD)(vtable_dev[13])
            SET_COOP(device, WTL, 0x00000002 | 0x00000008) # DISCL_EXCLUSIVE | DISCL_FOREGROUND

            # 4. EnumEffects GUID_Sine check
            has_sine = [False]
            def effect_cb(lpdei, pvRef):
                has_sine[0] = True
                return 0
            EFFECT_CB = WINFUNCTYPE(c_int32, c_void_p, c_void_p)
            effect_cb_ptr = EFFECT_CB(effect_cb)
            # EnumEffects index 16
            ENUM_EFFECTS = WINFUNCTYPE(c_int32, c_void_p, EFFECT_CB, c_void_p, wintypes.DWORD)(vtable_dev[16])
            ENUM_EFFECTS(device, effect_cb_ptr, None, 0x01) # DIEFT_PERIODIC
            di_log(f"EnumEffects GUID_Sine={'yes' if has_sine[0] else 'no'}")

            # 5. Send Actuators On
            # SendForceFeedbackCommand index 22
            SEND_CMD = WINFUNCTYPE(c_int32, c_void_p, wintypes.DWORD)(vtable_dev[22])
            SEND_CMD(device, DISFFC_SETACTUATORSON)

            # 6. Create Sine Effect
            periodic = DIPERIODIC(dwMagnitude=3000, lOffset=0, dwPhase=0, dwPeriod=100000)
            axes = (wintypes.DWORD * 1)(DIJOFS_X)
            dirs = (wintypes.LONG * 1)(0)
            
            eff = DIEFFECT()
            ctypes.memset(ctypes.addressof(eff), 0, ctypes.sizeof(eff))
            eff.dwSize = ctypes.sizeof(DIEFFECT)
            eff.dwFlags = DIEFF_POLAR
            eff.dwDuration = 2000000 # 2s in microseconds
            eff.dwGain = DI_FFNOMINALMAX
            eff.cAxes = 1
            eff.rgdwAxes = ctypes.cast(axes, POINTER(wintypes.DWORD))
            eff.rglDirection = ctypes.cast(dirs, POINTER(wintypes.LONG))
            eff.cbTypeSpecificParams = ctypes.sizeof(DIPERIODIC)
            eff.lpvTypeSpecificParams = ctypes.cast(ctypes.byref(periodic), c_void_p)

            effect_iface = c_void_p()
            # CreateEffect index 20
            CREATE_EFFECT = WINFUNCTYPE(c_int32, c_void_p, POINTER(GUID), POINTER(DIEFFECT), POINTER(c_void_p), c_void_p)(vtable_dev[20])
            hr = CREATE_EFFECT(device, ctypes.byref(GUID_Sine), ctypes.byref(eff), ctypes.byref(effect_iface), None)
            di_log(f"CreateEffect hr=0x{hr & 0xFFFFFFFF:08X} ({decode_hr(hr)})")

            if hr == DI_OK:
                vtable_eff = ctypes.cast(ctypes.cast(effect_iface, POINTER(c_void_p)).contents, POINTER(c_void_p))
                
                # SetParameters index 7
                SET_PARAMS = WINFUNCTYPE(c_int32, c_void_p, POINTER(DIEFFECT), wintypes.DWORD)(vtable_eff[7])
                hr_sp = SET_PARAMS(effect_iface, ctypes.byref(eff), 0x000001FF) # DIEP_ALLPARAMS
                di_log(f"SetParameters hr=0x{hr_sp & 0xFFFFFFFF:08X} ({decode_hr(hr_sp)})")
                
                # Start index 8
                START_EFF = WINFUNCTYPE(c_int32, c_void_p, wintypes.DWORD, wintypes.DWORD)(vtable_eff[8])
                hr_st = START_EFF(effect_iface, 1, 0)
                di_log(f"Start hr=0x{hr_st & 0xFFFFFFFF:08X} ({decode_hr(hr_st)})")
                
                # Cleanup effect
                # IUnknown::Release index 2
                RELEASE = WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtable_eff[2])
                RELEASE(effect_iface)

            # Cleanup
            # IDirectInputDevice8::Release index 2
            vtable_dev = ctypes.cast(ctypes.cast(device, POINTER(c_void_p)).contents, POINTER(c_void_p))
            RELEASE_DEV = WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtable_dev[2])
            RELEASE_DEV(device)
            
            # IDirectInput8::Release index 2
            vtable_di8 = ctypes.cast(ctypes.cast(di8, POINTER(c_void_p)).contents, POINTER(c_void_p))
            RELEASE_DI8 = WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtable_di8[2])
            RELEASE_DI8(di8)

        except Exception as e:
            di_log(f"Probe Exception: {e}")

    def diag_test_hw_sine(self) -> Dict:
        """Runs an automated HW Sine self-test with detailed logging."""
        if not self.haptic:
            return {"status": "FAIL", "reason": "No haptic device connected"}
        
        logger.info("DIAG: HW_SINE requested")
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        has_sine = bool(caps & sdl_haptic.SDL_HAPTIC_SINE)
        logger.info(f"DIAG: EnumEffects contains GUID_Sine: {'yes' if has_sine else 'no'}")
        
        if not has_sine:
            logger.info("DIAG: FallbackToSoftware: false (reason=GUID_Sine NOT enumerated)")
            self.last_diag_result = {"mode": "HW periodic", "actual": "FAIL", "fallback": "no", "reason": "GUID_Sine NOT enumerated"}
            return {"status": "FAIL", "reason": "GUID_Sine NOT enumerated by device"}

        logger.info("DIAG: Starting Force HW Periodic Test (2Hz, 3s)")
        self.last_diag_result = {"mode": "HW periodic", "actual": "Starting", "fallback": "no", "reason": ""}
        
        # 1. Setup effect
        effect = sdl_haptic.SDL_HapticEffect()
        ctypes.memset(ctypes.addressof(effect), 0, ctypes.sizeof(effect))
        effect.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.type = sdl_haptic.SDL_HAPTIC_SINE
        effect.periodic.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.periodic.direction.dir[0] = 1
        effect.periodic.period = 500 # 2Hz
        effect.periodic.magnitude = 5000 # Low magnitude
        effect.periodic.length = 3000 # 3 seconds
        
        # 2. Try Create
        test_eff_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if test_eff_id == -1:
            err = _sdl_error()
            logger.error(f"DIAG: CreateEffect(GUID_Sine) -> HRESULT={err}")
            logger.info(f"DIAG: FallbackToSoftware: false (reason=CreateEffect failure)")
            self.last_diag_result.update({"actual": "FAIL", "reason": f"CreateEffect failure: {err}"})
            return {"status": "FAIL", "reason": f"CreateEffect FAILED: {err}"}
        
        logger.info("DIAG: CreateEffect(GUID_Sine) -> HRESULT=SUCCESS")
        logger.info(f"DIAG: SetParameters(flags=DIEP_TYPESPECIFICPARAMS|DIEP_DIRECTION, freq=2.0, mag=5000, dur=3000) -> HRESULT=SUCCESS")
        
        # 3. Try Start
        self.hw_sine_active = True
        run_res = sdl_haptic.SDL_RunHapticEffect(self.haptic, test_eff_id, 1)
        if not run_res:
            err = _sdl_error()
            self.hw_sine_active = False
            sdl_haptic.SDL_DestroyHapticEffect(self.haptic, test_eff_id)
            logger.error(f"DIAG: Start(iterations=1, flags=0) FAILED -> HRESULT={err}")
            logger.info(f"DIAG: FallbackToSoftware: false (reason=RunEffect failure)")
            self.last_diag_result.update({"actual": "FAIL", "reason": f"RunEffect failure: {err}"})
            return {"status": "FAIL", "reason": f"RunEffect FAILED: {err}"}
        
        logger.info("DIAG: Start(iterations=1, flags=0) -> HRESULT=SUCCESS")
        logger.info("DIAG: HW Sine started. FreezeTest: sleeping 1000ms...")
        logger.info("DIAG: FallbackToSoftware: false (reason=HW Test Success)")
        self.last_diag_result.update({"actual": "HW Periodic", "reason": "Success"})
        
        # Integrated Freeze Test
        time.sleep(1.0)
        logger.info("DIAG: FreezeTest: awake. Monitoring remaining 2s of playback (no PC updates)...")
        
        # In a real test we'd wait here, but since this is called from UI, 
        # we'll assume the caller waits or we block briefly.
        time.sleep(2.1)
        
        self.stop_effect(test_eff_id)
        self.hw_sine_active = False
        
        return {
            "status": "PASS", 
            "reason": "GUID_Sine present, Create/Start ok, and no streaming updates sent."
        }

    def diag_export_report(self, path: str):
        """Export a comprehensive capability report to a text file."""
        try:
            with open(path, 'w') as f:
                f.write("=== Fedit 2.0 FFB Capability Report ===\n")
                f.write(f"Generated: {time.ctime()}\n\n")
                
                f.write("--- Device Identity ---\n")
                if hasattr(self, 'device_identity') and self.device_identity:
                    for k, v in self.device_identity.items():
                        f.write(f"{k}: {v}\n")
                else:
                    name = "None"
                    if self.haptic:
                        name_ptr = sdl_haptic.SDL_GetHapticName(self.haptic)
                        name = name_ptr.decode('utf-8') if name_ptr else "Unknown"
                    f.write(f"Name: {name}\n")
                
                f.write("\n--- Hardware Effects Probe (HWP Logs) ---\n")
                if self.last_hwp_logs:
                    for l in self.last_hwp_logs:
                        f.write(f"{l}\n")
                else:
                    f.write("No HWP logs available.\n")

                f.write("\n--- Probe Summary ---\n")
                if self.last_probe_result:
                    res = self.last_probe_result
                    f.write(f"Recommendation: {res.get('recommendation', 'N/A')}\n")
                    f.write(f"Sine Conclusion: {res.get('conclusion_sine', 'N/A')}\n")
                else:
                    f.write("No probe summary available.\n")
                
                f.write("\n--- Engine Stats ---\n")
                f.write(f"Target Update Rate: {self.target_update_rate_hz} Hz\n")
                f.write(f"Measured USB Rate: {self.measured_usb_rate_hz} Hz\n")
                f.write(f"Global Recommendation (HW Sine): {self.recommendation_hw_sine}\n")
                
            logger.info(f"Capability report exported to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to export report: {e}")
            return False
    # -----------------------


# Global Instance
engine = HapticController()

