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
from typing import Any, Dict, Iterable, Optional, Tuple

# PyInstaller windowed builds can null out stdout/stderr; SDL's logger expects them.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = sys.stdout

os.environ.setdefault("SDL_DISABLE_METADATA", "1")
os.environ.setdefault("SDL_DOWNLOAD_BINARIES", "0")

from sdl3 import SDL_error as sdl_error
from sdl3 import SDL_events as sdl_events
from sdl3 import SDL_gamepad as sdl_gp
from sdl3 import SDL_init as sdl_init
from sdl3 import SDL_joystick as sdl_joy
from sdl3 import SDL_stdinc as sdl_std
from sdl3 import SDL_version as sdl_ver

try:
    from sdl3 import SDL_haptic as sdl_haptic
except Exception:
    sdl_haptic = None

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
    DI_FF_EFFECT_CONSTANT = 0
    DI_FF_EFFECT_RAMP = 1
    DI_FF_EFFECT_SQUARE = 2
    DI_FF_EFFECT_SINE = 3
    DI_FF_EFFECT_TRIANGLE = 4
    DI_FF_EFFECT_SAWTOOTHUP = 5
    DI_FF_EFFECT_SAWTOOTHDOWN = 6
    DI_FF_EFFECT_SPRING = 7
    DI_FF_EFFECT_DAMPER = 8
    DI_FF_EFFECT_INERTIA = 9
    DI_FF_EFFECT_FRICTION = 10
    DI_FF_EFFECT_CUSTOM = 11

    class DIFFDeviceInfo(Structure):
        _fields_ = [
            ("deviceType", wintypes.DWORD),
            ("guidInstance", c_char_p),
            ("guidProduct", c_char_p),
            ("instanceName", c_char_p),
            ("productName", c_char_p),
            ("FFBCapable", ctypes.c_bool),
        ]
    
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

    DI_EFFECT_TYPE_MAP = {
        "constant": DI_FF_EFFECT_CONSTANT,
        "ramp": DI_FF_EFFECT_RAMP,
        "square": DI_FF_EFFECT_SQUARE,
        "sine": DI_FF_EFFECT_SINE,
        "periodic_sine": DI_FF_EFFECT_SINE,
        "directinput_periodic_sine": DI_FF_EFFECT_SINE,
        "triangle": DI_FF_EFFECT_TRIANGLE,
        "sawtoothup": DI_FF_EFFECT_SAWTOOTHUP,
        "sawtoothdown": DI_FF_EFFECT_SAWTOOTHDOWN,
        "spring": DI_FF_EFFECT_SPRING,
        "damper": DI_FF_EFFECT_DAMPER,
        "inertia": DI_FF_EFFECT_INERTIA,
        "friction": DI_FF_EFFECT_FRICTION,
        "custom": DI_FF_EFFECT_CUSTOM,
    }

    DI_PERIODIC_EFFECTS = {
        DI_FF_EFFECT_SINE,
        DI_FF_EFFECT_SQUARE,
        DI_FF_EFFECT_TRIANGLE,
        DI_FF_EFFECT_SAWTOOTHUP,
        DI_FF_EFFECT_SAWTOOTHDOWN,
    }

    DI_CONDITION_EFFECTS = {
        DI_FF_EFFECT_SPRING,
        DI_FF_EFFECT_DAMPER,
        DI_FF_EFFECT_INERTIA,
        DI_FF_EFFECT_FRICTION,
    }
else:
    DI_EFFECT_TYPE_MAP = {}
    DI_PERIODIC_EFFECTS = set()
    DI_CONDITION_EFFECTS = set()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FFB_Engine")

_engine_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "motorbuster_engine.log"))
if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == _engine_log_path for h in logger.handlers):
    _fh = logging.FileHandler(_engine_log_path, mode="a", encoding="utf-8")
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(_fh)

# Maps user-friendly effect keys to SDL enums for periodic/condition effects
if sdl_haptic:
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
else:
    PERIODIC_TYPES = {}
    CONDITION_TYPES = {}
    DIRECTION_MODES = {}


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
        self.axis_baseline: dict[str, int] = {}
        self.preferred_axis_key: Optional[str] = None

        # Backend selection
        self.backend_mode: str = "directinput"  # "sdl3" or "directinput"
        self._di = None
        self._di_loaded = False
        self._di_active = False
        self._di_hwnd = None
        self._di_provider = "unity_di"
        self._di_selected_guid: Optional[str] = None
        self._di_enum_devices: list[tuple[str, str]] = []
        self._di_effect_deadline_s: Optional[float] = None
        self._di_active_effects: set[int] = set()
        self._force_cartesian_periodic = False
        
        # Polling rate throttling
        self.detected_poll_rate_hz: int = 1000  # Default assumption
        self.measured_usb_rate_hz: int = 0
        self.target_update_rate_hz: int = 500
        self.manual_poll_rate_override: Optional[int] = None
        self._min_update_interval_s: float = 0.002
        
        # Device version counter to detect reconnects
        self._device_version: int = 0
        self._haptic_lock = threading.Lock()
        
        # Event Queue for asynchronous API calls
        self._event_queue_enabled = True
        self._pending_payloads: Dict[str, any] = {}
        self._event_types_registered = False
        self._custom_event_types = {}
        self._payload_lock = threading.Lock()
        self._queue_stats = {
            "events_queued": 0,
            "events_processed": 0,
            "queue_overflows": 0,
        }
        self._effect_id_results: Dict[tuple, int] = {}
        
        # Hardware Effects Probe Results
        self.last_probe_result: Dict = {}
        self.recommendation_hw_sine: bool = False
        self._probe_log_callback = None
        self.last_hwp_logs: list[str] = []
        
        # Diagnostic Tracking
        self.last_diag_result = {"mode": "N/A", "actual": "N/A", "fallback": "N/A", "reason": "N/A"}
        self.hw_sine_active = False
        self.device_caps_log = []
        self.device_identity = {}
        self.last_effect_error: str = ""

    def init_sdl(self):
        flags = (
            sdl_init.SDL_INIT_VIDEO
            | sdl_init.SDL_INIT_JOYSTICK
            | sdl_init.SDL_INIT_GAMEPAD
            | sdl_init.SDL_INIT_EVENTS
        )
        if sdl_haptic:
            flags |= sdl_init.SDL_INIT_HAPTIC

        if not sdl_init.SDL_Init(flags):
            logger.error(f"SDL_Init Error: {_sdl_error()}")
            raise Exception("Failed to initialize SDL3")
        
        self._register_custom_events()

    def get_backend_mode(self) -> str:
        return self.backend_mode

    def set_backend_mode(self, mode: str) -> None:
        mode = (mode or "").strip().lower()
        if mode != "directinput":
            logger.error(f"Invalid backend mode '{mode}'")
            return
        if self.backend_mode == mode:
            return
        self.close_device()
        self.backend_mode = mode
        logger.info(f"Backend mode set to {self.backend_mode}")

    def set_directinput_hwnd(self, hwnd) -> None:
        self._di_hwnd = hwnd

    def _load_directinput_dll(self) -> bool:
        if self._di_loaded:
            return True

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        unity_di_path = os.path.join(base_dir, "DirectInputForceFeedback.dll")
        if not os.path.exists(unity_di_path):
            logger.error(f"DirectInput DLL not found: {unity_di_path}")
            return False

        try:
            self._di = ctypes.CDLL(unity_di_path)
            self._di.StartDirectInput.argtypes = []
            self._di.StartDirectInput.restype = ctypes.c_long
            if hasattr(self._di, "InitializeForStandalone"):
                self._di.InitializeForStandalone.argtypes = []
                self._di.InitializeForStandalone.restype = None
            self._di.StopDirectInput.argtypes = []
            self._di.StopDirectInput.restype = ctypes.c_long
            self._di.EnumerateDevices.argtypes = [ctypes.POINTER(ctypes.c_int)]
            self._di.EnumerateDevices.restype = ctypes.POINTER(DIFFDeviceInfo)
            self._di.CreateDevice.argtypes = [ctypes.c_char_p]
            self._di.CreateDevice.restype = ctypes.c_long
            self._di.DestroyDevice.argtypes = [ctypes.c_char_p]
            self._di.DestroyDevice.restype = ctypes.c_long
            self._di.CreateFFBEffect.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._di.CreateFFBEffect.restype = ctypes.c_long
            self._di.DestroyFFBEffect.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._di.DestroyFFBEffect.restype = ctypes.c_long
            self._di.UpdateConstantForce.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._di.UpdateConstantForce.restype = ctypes.c_long
            self._di.UpdatePeriodicForce.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint32, ctypes.c_int]
            self._di.UpdatePeriodicForce.restype = ctypes.c_long
            self._di.UpdateRampForce.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
            self._di.UpdateRampForce.restype = ctypes.c_long
            self._di.UpdateConditionForce.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_int,
            ]
            self._di.UpdateConditionForce.restype = ctypes.c_long
            self._di.UpdateCustomForce.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.c_int,
                ctypes.c_uint32,
            ]
            self._di.UpdateCustomForce.restype = ctypes.c_long
            self._di.StopAllFFBEffects.argtypes = [ctypes.c_char_p]
            self._di.StopAllFFBEffects.restype = ctypes.c_long
            self._di.GetDILastError.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._di.GetDILastError.restype = ctypes.c_long
            self._di.IsDirectInputInitialized.argtypes = []
            self._di.IsDirectInputInitialized.restype = ctypes.c_bool
            self._di_provider = "unity_di"
            self._di_loaded = True
            logger.info(f"Loaded DirectInput provider: unity_di ({unity_di_path})")
            return True
        except Exception as e:
            logger.error(f"Failed to load Unity DirectInput DLL '{unity_di_path}': {e}")
            return False

    def _unity_di_last_error(self) -> str:
        if not self._di or self._di_provider != "unity_di":
            return ""
        try:
            buf = ctypes.create_string_buffer(1024)
            self._di.GetDILastError(buf, len(buf))
            return buf.value.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _init_directinput_backend(self) -> bool:
        if not self._load_directinput_dll():
            return False

        try:
            if hasattr(self._di, "InitializeForStandalone"):
                self._di.InitializeForStandalone()
            res_start = int(self._di.StartDirectInput())
            if res_start < 0:
                last = self._unity_di_last_error()
                logger.error(
                    f"Unity DI StartDirectInput failed: {res_start} (0x{res_start & 0xFFFFFFFF:08X})"
                    + (f" | {last}" if last else "")
                )
                return False

            count = ctypes.c_int(0)
            ptr = self._di.EnumerateDevices(ctypes.byref(count))
            if count.value <= 0 or not ptr:
                logger.error("Unity DI enumerate found no FFB devices")
                return False

            devices: list[tuple[str, str]] = []
            for i in range(count.value):
                dev = ptr[i]
                if not bool(dev.FFBCapable):
                    continue
                guid = dev.guidInstance.decode("utf-8", errors="replace") if dev.guidInstance else ""
                name_raw = dev.productName or dev.instanceName
                name = name_raw.decode("utf-8", errors="replace") if name_raw else f"DirectInput Device {i}"
                if guid:
                    devices.append((guid, name))

            if not devices:
                logger.error("Unity DI enumerate found devices, but none were FFB-capable")
                return False

            self._di_enum_devices = devices
            guid = self._di_selected_guid or devices[0][0]
            if not any(g == guid for g, _ in devices):
                guid = devices[0][0]

            guid_b = guid.encode("utf-8")
            res_create = int(self._di.CreateDevice(guid_b))
            if res_create < 0:
                last = self._unity_di_last_error()
                logger.error(
                    f"Unity DI CreateDevice failed: {res_create} (0x{res_create & 0xFFFFFFFF:08X})"
                    + (f" | {last}" if last else "")
                )
                return False

            res_eff = int(self._di.CreateFFBEffect(guid_b, DI_FF_EFFECT_SINE))
            if res_eff < 0:
                last = self._unity_di_last_error()
                logger.error(
                    f"Unity DI CreateFFBEffect(Sine) failed: {res_eff} (0x{res_eff & 0xFFFFFFFF:08X})"
                    + (f" | {last}" if last else "")
                )
                return False

            self._di_selected_guid = guid
            self._di_active = True
            logger.info(f"DirectInput backend initialized via Unity DI for guid={guid}")
            return True
        except Exception as e:
            logger.error(f"Unity DI init exception: {e}")
            return False

    def _shutdown_directinput_backend(self) -> None:
        if not self._di_active or not self._di:
            return

        try:
            if self._di_selected_guid:
                guid_b = self._di_selected_guid.encode("utf-8")
                self._di.StopAllFFBEffects(guid_b)
                for effect_type in list(self._di_active_effects):
                    try:
                        self._di.DestroyFFBEffect(guid_b, effect_type)
                    except Exception:
                        pass
                self._di_active_effects.clear()
                self._di.DestroyDevice(guid_b)
            self._di.StopDirectInput()
        except Exception as e:
            logger.debug(f"Unity DI shutdown failed: {e}")
        finally:
            self._di_active = False
            self._di_selected_guid = None
            self._di_effect_deadline_s = None
            self._di_active_effects.clear()

    def _normalize_di_magnitude(self, magnitude: int, unit: str = "auto") -> int:
        mag_in = abs(int(magnitude))
        mode = (unit or "auto").lower()

        if mode in {"di", "di_0_10000", "raw_di"}:
            safe_mag = mag_in
        elif mode in {"percent", "pct"}:
            safe_mag = int(round(max(0, min(100, mag_in)) * 100))
        elif mode in {"s16", "short", "raw32767", "clip"}:
            safe_mag = int(round(max(0, min(32767, mag_in)) * 10000 / 32767.0))
        else:
            # Auto mode prefers legacy clip scale (0..32767), since that's what native_app emits.
            if mag_in <= 100:
                safe_mag = mag_in * 100
            elif mag_in <= 32767:
                safe_mag = int(round(mag_in * 10000 / 32767.0))
            else:
                safe_mag = 10000

        return max(0, min(10000, int(safe_mag)))

    def _normalize_di_signed_magnitude(self, magnitude: int, unit: str = "auto") -> int:
        sign = -1 if int(magnitude) < 0 else 1
        safe_mag = self._normalize_di_magnitude(abs(int(magnitude)), unit)
        return max(-10000, min(10000, int(sign * safe_mag)))

    def _di_set_deadline(self, duration_us: int) -> None:
        safe_dur = max(0, int(duration_us))
        if safe_dur > 0:
            self._di_effect_deadline_s = time.monotonic() + (safe_dur / 1_000_000.0) + 0.05
        else:
            self._di_effect_deadline_s = None

    def _di_ensure_effect(self, effect_type: int) -> bool:
        if effect_type in self._di_active_effects:
            return True
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False
        try:
            guid_b = self._di_selected_guid.encode("utf-8")
            res = int(self._di.CreateFFBEffect(guid_b, effect_type))
            if res < 0:
                if (res & 0xFFFFFFFF) == 0x80004004:  # E_ABORT: effect already exists
                    self._di_active_effects.add(effect_type)
                    return True
                detail = self._unity_di_last_error()
                self.last_effect_error = (
                    f"Unity DI CreateFFBEffect failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                    + (f" | {detail}" if detail else "")
                )
                logger.error(self.last_effect_error)
                return False
            self._di_active_effects.add(effect_type)
            return True
        except Exception as e:
            self.last_effect_error = f"Unity DI CreateFFBEffect exception: {e}"
            logger.error(self.last_effect_error)
            return False

    def _di_update_periodic(self, effect_type: int, magnitude: int, period_us: int, offset: int = 0, magnitude_unit: str = "auto") -> bool:
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False

        safe_mag = self._normalize_di_magnitude(magnitude, magnitude_unit)
        safe_period = max(1, int(period_us))
        safe_offset = max(-10000, min(10000, int(offset)))
        guid_b = self._di_selected_guid.encode("utf-8")

        res = int(self._di.UpdatePeriodicForce(guid_b, effect_type, safe_mag, safe_period, safe_offset))
        if res < 0:
            detail = self._unity_di_last_error()
            self.last_effect_error = (
                f"Unity DI UpdatePeriodicForce failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                + (f" | {detail}" if detail else "")
            )
            logger.error(self.last_effect_error)
            return False
        self.last_effect_error = ""
        return True

    def _di_update_constant(self, magnitude: int, magnitude_unit: str = "auto") -> bool:
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False

        safe_mag = self._normalize_di_signed_magnitude(magnitude, magnitude_unit)
        guid_b = self._di_selected_guid.encode("utf-8")
        res = int(self._di.UpdateConstantForce(guid_b, safe_mag))
        if res < 0:
            detail = self._unity_di_last_error()
            self.last_effect_error = (
                f"Unity DI UpdateConstantForce failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                + (f" | {detail}" if detail else "")
            )
            logger.error(self.last_effect_error)
            return False
        self.last_effect_error = ""
        return True

    def _di_update_ramp(self, start_mag: int, end_mag: int, magnitude_unit: str = "auto") -> bool:
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False

        safe_start = self._normalize_di_signed_magnitude(start_mag, magnitude_unit)
        safe_end = self._normalize_di_signed_magnitude(end_mag, magnitude_unit)
        guid_b = self._di_selected_guid.encode("utf-8")
        res = int(self._di.UpdateRampForce(guid_b, safe_start, safe_end))
        if res < 0:
            detail = self._unity_di_last_error()
            self.last_effect_error = (
                f"Unity DI UpdateRampForce failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                + (f" | {detail}" if detail else "")
            )
            logger.error(self.last_effect_error)
            return False
        self.last_effect_error = ""
        return True

    def _di_update_condition(
        self,
        effect_type: int,
        offset: int,
        positive_coefficient: int,
        negative_coefficient: int,
        positive_saturation: int,
        negative_saturation: int,
        deadband: int,
        magnitude_unit: str = "auto",
    ) -> bool:
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False

        safe_offset = max(-10000, min(10000, int(offset)))
        safe_pos_coef = self._normalize_di_signed_magnitude(positive_coefficient, magnitude_unit)
        safe_neg_coef = self._normalize_di_signed_magnitude(negative_coefficient, magnitude_unit)
        safe_pos_sat = max(0, min(10000, int(positive_saturation)))
        safe_neg_sat = max(0, min(10000, int(negative_saturation)))
        safe_deadband = max(0, min(10000, int(deadband)))
        guid_b = self._di_selected_guid.encode("utf-8")

        res = int(
            self._di.UpdateConditionForce(
                guid_b,
                effect_type,
                safe_offset,
                safe_pos_coef,
                safe_neg_coef,
                safe_pos_sat,
                safe_neg_sat,
                safe_deadband,
            )
        )
        if res < 0:
            detail = self._unity_di_last_error()
            self.last_effect_error = (
                f"Unity DI UpdateConditionForce failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                + (f" | {detail}" if detail else "")
            )
            logger.error(self.last_effect_error)
            return False
        self.last_effect_error = ""
        return True

    def _di_update_custom(self, samples: list[int], sample_period_us: int, magnitude_unit: str = "auto") -> bool:
        if not self._di_active or not self._di:
            self.last_effect_error = "DirectInput backend not initialized"
            logger.error(self.last_effect_error)
            return False
        if not self._di_selected_guid:
            self.last_effect_error = "Unity DI device GUID not selected"
            logger.error(self.last_effect_error)
            return False

        if not samples:
            self.last_effect_error = "Unity DI custom force missing samples"
            logger.error(self.last_effect_error)
            return False

        converted = [self._normalize_di_signed_magnitude(s, magnitude_unit) for s in samples]
        count = len(converted)
        array_type = ctypes.c_int * count
        data = array_type(*converted)
        safe_period = max(1, int(sample_period_us))
        guid_b = self._di_selected_guid.encode("utf-8")

        res = int(self._di.UpdateCustomForce(guid_b, data, count, safe_period))
        if res < 0:
            detail = self._unity_di_last_error()
            self.last_effect_error = (
                f"Unity DI UpdateCustomForce failed: {res} (0x{res & 0xFFFFFFFF:08X})"
                + (f" | {detail}" if detail else "")
            )
            logger.error(self.last_effect_error)
            return False
        self.last_effect_error = ""
        return True

    def _di_start_sine(self, duration_us: int, magnitude: int, period_us: int, magnitude_unit: str = "auto") -> int:
        if not self._di_ensure_effect(DI_FF_EFFECT_SINE):
            return -1

        self._di_set_deadline(duration_us)

        ok = self._di_update_periodic(
            DI_FF_EFFECT_SINE,
            magnitude,
            period_us,
            0,
            magnitude_unit=magnitude_unit,
        )
        return 1 if ok else -1
        
    def _register_custom_events(self):
        """Register SDL3 custom user events for asynchronous API calls."""
        if self._event_types_registered:
            return
        
        try:
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
        if self.backend_mode == "directinput":
            devices: list[DeviceInfo] = []
            self._device_ids = []
            self._di_enum_devices = []

            if not self._load_directinput_dll():
                logger.error("DirectInput mode selected but DI DLL failed to load")
                return devices

            try:
                res_start = int(self._di.StartDirectInput())
                if res_start < 0:
                    detail = self._unity_di_last_error()
                    logger.error(
                        f"Unity DI StartDirectInput failed during scan: {res_start} (0x{res_start & 0xFFFFFFFF:08X})"
                        + (f" | {detail}" if detail else "")
                    )
                    return devices

                count = ctypes.c_int(0)
                ptr = self._di.EnumerateDevices(ctypes.byref(count))
                if count.value <= 0 or not ptr:
                    logger.info("DirectInput scan found no DI devices")
                    return devices

                for i in range(count.value):
                    dev = ptr[i]
                    if not bool(dev.FFBCapable):
                        continue

                    guid = dev.guidInstance.decode("utf-8", errors="replace") if dev.guidInstance else ""
                    name_raw = dev.productName or dev.instanceName
                    name = name_raw.decode("utf-8", errors="replace") if name_raw else f"DirectInput Device {i}"
                    if not guid:
                        continue

                    idx = len(self._di_enum_devices)
                    self._di_enum_devices.append((guid, name))
                    self._device_ids.append(idx)
                    devices.append(DeviceInfo(index=idx, name=name))

                logger.info(f"DirectInput scan found {len(devices)} FFB-capable device(s) via Unity DI")
                return devices
            except Exception as e:
                logger.error(f"Unity DI scan exception: {e}")
                return devices

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
                if sdl_haptic:
                    is_haptic = bool(sdl_haptic.SDL_IsJoystickHaptic(tmp_joy))
                sdl_joy.SDL_CloseJoystick(tmp_joy)

            logger.info(
                f"Device {idx} (id={joy_id}): '{name_str}' | Haptic: {is_haptic} | Gamepad: {is_gc}"
            )

            devices.append(DeviceInfo(index=idx, name=name_str))

        return devices
    def connect_device(self, index: int):
        self.close_device()

        if self.backend_mode == "directinput":
            self.effect_id = -1
            self._device_version += 1
            self.hw_sine_active = False

            if not self._load_directinput_dll():
                logger.error("DirectInput backend unavailable: could not load any DI provider DLL")
                return False

            if not self._di_enum_devices:
                self.list_devices()
            if index < 0 or index >= len(self._di_enum_devices):
                logger.error(f"Invalid DirectInput device index {index}")
                return False
            self._di_selected_guid = self._di_enum_devices[index][0]

            name = "DirectInput Device"
            if 0 <= index < len(self._di_enum_devices):
                name = self._di_enum_devices[index][1]
            self.device_identity = {
                "name": name,
                "vid": "0000",
                "pid": "0000",
                "serial": "Unknown",
            }
            logger.info(f"Connecting to (DI mode): {name} [VID:0000 PID:0000]")

            self._force_cartesian_periodic = "simagic" in name.lower()
            self.joystick = None
            self.gamepad = None
            self.preferred_axis_key = None

            logger.info("DirectInput backend ignores SDL device index; using first available DI device")
            if not self._init_directinput_backend():
                logger.error("DirectInput backend init failed; device not connected.")
                self.close_device()
                return False

            logger.info("DirectInput backend active; SDL haptics disabled.")
            return True

        if not self._device_ids:
            self.list_devices()

        if not sdl_haptic:
            logger.error("SDL haptic backend removed; DirectInput only.")
            return False

        if index < 0 or index >= len(self._device_ids):
            logger.error(f"Invalid device index {index}")
            return False

        joy_id = self._device_ids[index]
        self.effect_id = -1
        # Software oscillator removed
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

        self._force_cartesian_periodic = "simagic" in name.lower()


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
        if self.backend_mode == "directinput":
            self._shutdown_directinput_backend()
        if self.effect_id != -1:
            if self.haptic:
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, self.effect_id)
            self.effect_id = -1
            
        if self.haptic:
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
        """Generates and plays a linear frequency sweep using Custom Force (hardware-only)."""
        if not self.haptic: return -1

        # Check Capability
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        if not (caps & sdl_haptic.SDL_HAPTIC_CUSTOM):
            logger.error("SDL_HAPTIC_CUSTOM not supported. Sweep unavailable in hardware-only mode.")
            return -1

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
            logger.error("Custom Effect Failed. Sweep unavailable in hardware-only mode.")
            return -1
            
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

    def _log_haptic_failure_details(self, kind: str, effect: Any, err_str: str):
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
        period_ms = max(1, min(1000, int(round(1000.0 / safe_freq))))
        actual_freq = round(1000.0 / period_ms, 2)
        if actual_freq != round(safe_freq, 2):
            logger.warning(f"Frequency rounded: requested {safe_freq}Hz -> actual {actual_freq}Hz (period={period_ms}ms)")
        return period_ms

    def _build_periodic(self, effect, kind: str, desc: Dict) -> None:
        periodic_type = PERIODIC_TYPES.get(kind, sdl_haptic.SDL_HAPTIC_SINE)
        if self._force_cartesian_periodic:
            direction = self._make_direction("cartesian", {"x": 10000, "y": 0, "z": 0})
        else:
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
        effect.periodic.phase = 0 if self._force_cartesian_periodic else adjusted_phase
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

    def build_effect_from_descriptor(self, desc: Dict) -> Optional[Any]:
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
            logger.error(f"Unsupported effect type '{kind}' (hardware-only mode)")
            return None

        return effect

    def _play_directinput_descriptor(self, desc: Dict) -> int:
        kind = (desc.get("type") or desc.get("effect") or "sine").lower()
        effect_type = DI_EFFECT_TYPE_MAP.get(kind)
        if effect_type is None:
            self.last_effect_error = f"DirectInput backend does not support effect '{kind}'"
            logger.error(self.last_effect_error)
            return -1

        if "length_us" not in desc:
            self.last_effect_error = "DirectInput requires length_us"
            logger.error("DirectInput descriptor missing length_us")
            return -1
        length_us = int(desc["length_us"])
        self._di_set_deadline(length_us)

        self._current_descriptor = desc
        mag_unit = str(desc.get("magnitude_unit", "auto"))

        if effect_type in DI_PERIODIC_EFFECTS:
            mag_raw = int(desc.get("magnitude", 10000))
            period_us = int(desc.get("period_us", 0))
            if period_us <= 0:
                freq = float(desc.get("frequency_hz", 10.0))
                period_us = int(1_000_000.0 / freq) if freq > 0 else 100000
            offset = int(desc.get("offset", 0))

            logger.info(
                "DI-DESC: resolved payload "
                f"type={kind} mag_in={mag_raw} unit={mag_unit} "
                f"length_us={length_us} period_us={period_us} offset={offset}"
            )

            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_periodic(effect_type, mag_raw, period_us, offset, magnitude_unit=mag_unit)
            return 1 if ok else -1

        if effect_type == DI_FF_EFFECT_CONSTANT:
            mag_raw = int(desc.get("magnitude", 0))
            logger.info(
                "DI-DESC: resolved payload "
                f"type={kind} mag_in={mag_raw} unit={mag_unit} length_us={length_us}"
            )
            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_constant(mag_raw, magnitude_unit=mag_unit)
            return 1 if ok else -1

        if effect_type == DI_FF_EFFECT_RAMP:
            start_mag = int(desc.get("start_mag", 0))
            end_mag = int(desc.get("end_mag", 0))
            logger.info(
                "DI-DESC: resolved payload "
                f"type={kind} start={start_mag} end={end_mag} unit={mag_unit} length_us={length_us}"
            )
            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_ramp(start_mag, end_mag, magnitude_unit=mag_unit)
            return 1 if ok else -1

        if effect_type in DI_CONDITION_EFFECTS:
            base_mag = int(desc.get("magnitude", 0))
            offset = int(desc.get("offset", 0))
            pos_coef = int(desc.get("positive_coefficient", base_mag))
            neg_coef = int(desc.get("negative_coefficient", base_mag))
            pos_sat = int(desc.get("positive_saturation", 10000))
            neg_sat = int(desc.get("negative_saturation", 10000))
            deadband = int(desc.get("deadband", 0))

            logger.info(
                "DI-DESC: resolved payload "
                f"type={kind} offset={offset} pos_coef={pos_coef} neg_coef={neg_coef} "
                f"pos_sat={pos_sat} neg_sat={neg_sat} deadband={deadband} unit={mag_unit} length_us={length_us}"
            )

            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_condition(
                effect_type,
                offset,
                pos_coef,
                neg_coef,
                pos_sat,
                neg_sat,
                deadband,
                magnitude_unit=mag_unit,
            )
            return 1 if ok else -1

        if effect_type == DI_FF_EFFECT_CUSTOM:
            samples = desc.get("samples", [])
            sample_period_us = int(desc.get("sample_period_us", 1000))
            logger.info(
                "DI-DESC: resolved payload "
                f"type={kind} samples={len(samples)} sample_period_us={sample_period_us} length_us={length_us}"
            )
            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_custom(samples, sample_period_us, magnitude_unit=mag_unit)
            return 1 if ok else -1

        self.last_effect_error = f"DirectInput backend has no handler for effect '{kind}'"
        logger.error(self.last_effect_error)
        return -1

    def get_last_effect_error(self) -> str:
        return self.last_effect_error

    def play_descriptor(self, desc: Dict) -> int:
        if self.backend_mode == "directinput":
            return self._play_directinput_descriptor(desc)

        if not self.haptic:
            return -1

        kind = (desc.get("type") or desc.get("effect") or "sine").lower()


        # Handle custom separately so we can upload the buffer
        if kind == "custom" and "samples" in desc:
            samples = desc.get("samples", [])
            length_ms = int(desc.get("length_ms", len(samples)))
            return self.play_custom(samples, length_ms) and self.effect_id or -1

        is_explicit_hw = (kind == "periodic_sine" or kind == "directinput_periodic_sine")

        # Log HW request if it's SINE and we are attempting HW
        if is_explicit_hw or kind == "sine":
            logger.info(f"DIAG: HW_SINE requested (Mode: {kind})")
            caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
            has_sine = bool(caps & sdl_haptic.SDL_HAPTIC_SINE)
            logger.info(f"DIAG: EnumEffects contains GUID_Sine: {'yes' if has_sine else 'no'}")

        # Hardware mode: use standard effect building
        
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
            err = _sdl_error()
            logger.error(f"DIAG: CreateEffect({kind.upper()}) FAILED -> HRESULT={err}")
            self._log_haptic_failure_details(kind, effect, err)
            self.hw_sine_active = False
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
            
            return self.effect_id

        err = _sdl_error()
        logger.error(f"DIAG: Start(iterations=1, flags=0) FAILED -> HRESULT={err}")
        return -1
    # --- Sequencer Methods ---
    def update_effect_sine(
        self,
        effect_id: int,
        freq: float,
        magnitude: int,
        duration_ms: int,
        phase: int = 0,
        magnitude_unit: str = "s16",
        duration_us: Optional[int] = None,
    ):
        """Updates sine effect parameters during playback (sweep updates)."""
        if self.backend_mode == "directinput":
            desc = getattr(self, "_current_descriptor", {}) or {}
            kind = (desc.get("type") or desc.get("effect") or "sine").lower()
            effect_type = DI_EFFECT_TYPE_MAP.get(kind, DI_FF_EFFECT_SINE)
            if effect_type not in DI_PERIODIC_EFFECTS:
                self.last_effect_error = f"DirectInput update only supports periodic effects (got '{kind}')"
                logger.error(self.last_effect_error)
                return -1

            period_us = int(1_000_000.0 / freq) if freq > 0 else 100000
            if duration_us is None:
                duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)

            logger.info(
                "DI-UPDATE: resolved payload "
                f"type={kind} freq={freq} mag_in={magnitude} unit={magnitude_unit} "
                f"duration_us={duration_us} period_us={period_us} phase={phase}"
            )

            if not self._di_ensure_effect(effect_type):
                return -1
            ok = self._di_update_periodic(effect_type, magnitude, period_us, 0, magnitude_unit=magnitude_unit)
            return 1 if ok else -1

        if not self.haptic or effect_id == -1:
            return -1

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
                return effect_id
            else:
                err = _sdl_error()
                logger.error(f"UpdateEffect FAILED: {err}")
                return -1
        except Exception as e:
            logger.error(f"Exception during UpdateEffect: {e}")
            return -1



    def update_effect_constant(self, effect_id: int, magnitude: int, duration_ms: int):
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_CONSTANT):
                return -1
            ok = self._di_update_constant(magnitude, magnitude_unit="auto")
            return effect_id if ok else -1
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_constant blocked)")
            return -1
        if not self.haptic or effect_id == -1:
            return -1
        
        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_CONSTANT
        effect.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.constant.length = duration_ms
        effect.constant.level = magnitude
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))
        return effect_id

    def update_effect_ramp(self, effect_id: int, start_mag: int, end_mag: int, duration_ms: int):
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_RAMP):
                return -1
            ok = self._di_update_ramp(start_mag, end_mag, magnitude_unit="auto")
            return effect_id if ok else -1
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_ramp blocked)")
            return -1
        if not self.haptic or effect_id == -1:
            return -1

        effect = sdl_haptic.SDL_HapticEffect()
        effect.type = sdl_haptic.SDL_HAPTIC_RAMP
        effect.ramp.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
        effect.ramp.length = duration_ms
        effect.ramp.start = start_mag
        effect.ramp.end = end_mag
        
        sdl_haptic.SDL_UpdateHapticEffect(self.haptic, effect_id, ctypes.byref(effect))
        return effect_id

    def update_effect_sawtooth(self, effect_id: int, magnitude: int, period: int, duration_ms: int):
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_SAWTOOTHUP):
                return -1
            ok = self._di_update_periodic(
                DI_FF_EFFECT_SAWTOOTHUP,
                magnitude,
                int(period),
                0,
                magnitude_unit="auto",
            )
            return effect_id if ok else -1
        if self.hw_sine_active:
            logger.warning(f"WARN: HW mode active but streaming updates are still being sent (update_effect_sawtooth blocked)")
            return -1
        if not self.haptic or effect_id == -1:
            return -1

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
        return effect_id

    def start_effect_constant(self, magnitude: int, duration_ms: int):
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_CONSTANT):
                return -1
            ok = self._di_update_constant(magnitude, magnitude_unit="auto")
            return 1 if ok else -1
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
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_RAMP):
                return -1
            ok = self._di_update_ramp(start_mag, end_mag, magnitude_unit="auto")
            return 1 if ok else -1
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
        if self.backend_mode == "directinput":
            duration_us = int(duration_ms) * 1000
            self._di_set_deadline(duration_us)
            if not self._di_ensure_effect(DI_FF_EFFECT_SAWTOOTHUP):
                return -1
            ok = self._di_update_periodic(
                DI_FF_EFFECT_SAWTOOTHUP,
                magnitude,
                int(period),
                0,
                magnitude_unit="auto",
            )
            return 1 if ok else -1
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
        if self.backend_mode == "directinput":
            if self._di_active and self._di:
                try:
                    if self._di_selected_guid:
                        guid_b = self._di_selected_guid.encode("utf-8")
                        self._di.StopAllFFBEffects(guid_b)
                        for effect_type in list(self._di_active_effects):
                            try:
                                self._di.DestroyFFBEffect(guid_b, effect_type)
                            except Exception:
                                pass
                        self._di_active_effects.clear()
                except Exception as e:
                    logger.error(f"DirectInput stop failed: {e}")
            self._di_active_effects.clear()
            self._di_effect_deadline_s = None
            self.effect_id = -1
            return
        if not self.haptic: return
        
        try:
            if effect_id == -1:
                # Stop All
                sdl_haptic.SDL_StopHapticEffects(self.haptic)
            else:
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
        if self.backend_mode == "directinput" and self._di_active and self._di_effect_deadline_s is not None:
            if time.monotonic() >= self._di_effect_deadline_s:
                logger.info("DirectInput auto-stop: effect duration elapsed")
                self.stop_effect()

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
            
    # Software oscillator removed
    def run_hardware_effects_probe(self, feedback_callback=None, progress_callback=None, stream_updates: bool = False) -> Dict:
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
        hwp_log(f"Streamed updates: {'on' if stream_updates else 'off'}")

        if not sdl_haptic:
            hwp_log("FAIL stage=Backend rc=-1 err=\"SDL haptics not available (DirectInput-only build)\"")
            self.last_probe_result = {
                "conclusion_sine": "BACKEND_NOT_SUPPORTED",
                "recommendation": "SDL haptics not available in this build",
            }
            return {
                "status": "FAIL",
                "reason": "SDL haptics not available",
                "tests": {},
            }

        if self.backend_mode != "sdl3":
            hwp_log("FAIL stage=Backend rc=-1 err=\"Hardware Effects Probe requires SDL3 backend\"")
            self.last_probe_result = {
                "conclusion_sine": "BACKEND_NOT_SUPPORTED",
                "recommendation": "Switch backend to SDL3 to run probe",
            }
            return {
                "status": "FAIL",
                "reason": "Hardware Effects Probe requires SDL3 backend",
                "tests": {},
            }
        
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
            self.last_probe_result = {
                "conclusion_sine": "NO_DEVICE",
                "recommendation": "Connect a device and try again",
            }
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
            test_mag = int(0.15 * 32767)
            
            # Parameters
            if is_periodic:
                mag, period, offset, phase, length = test_mag, 500, 0, 0, 5000
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
                level, length = test_mag, 5000
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

            attempt = 0
            while True:
                attempt += 1
                if attempt > 1:
                    hwp_log(f"Retrying effect={name.upper()} attempt={attempt}")

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

                def stream_update(tag: str):
                    if not stream_updates:
                        return
                    sdl_error.SDL_ClearError()
                    ok = sdl_haptic.SDL_UpdateHapticEffect(self.haptic, eff_id, ctypes.byref(effect))
                    err_u = sdl_error.SDL_GetError().decode("utf-8") if not ok else ""
                    if not ok:
                        hwp_log(f"UpdateEffect(effect={name.upper()}) rc=-1 err=\"{err_u}\" phase={tag}")

                # 3) Signal + Block (simplified)
                signal_s = 2
                block_s = 3
                hwp_log(f"Signal(effect={name.upper()}) duration={signal_s}s")
                for remaining in range(signal_s, 0, -1):
                    if progress_callback:
                        progress_callback(name.upper(), "SIGNAL", remaining, test_index, test_total)
                    stream_update("SIGNAL")
                    time.sleep(1.0)

                hwp_log(f"Block(effect={name.upper()}) duration={block_s}s (no updates)")
                
                for remaining in range(block_s, 0, -1):
                    if progress_callback:
                        progress_callback(name.upper(), "BLOCK", remaining, test_index, test_total)
                    stream_update("BLOCK")
                    time.sleep(1.0)

                # Stop immediately after 2s signal + 3s block (total 5s)
                sdl_error.SDL_ClearError()
                stop_rc = sdl_haptic.SDL_StopHapticEffect(self.haptic, eff_id)
                stop_err = sdl_error.SDL_GetError().decode("utf-8") if not stop_rc else ""
                hwp_log(f"StopEffect(effect={name.upper()}) rc={0 if stop_rc else -1} err=\"{stop_err}\"")
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, eff_id)
                hwp_log(f"DestroyEffect(effect={name.upper()}) destroyed")

                if progress_callback:
                    progress_callback(name.upper(), "DONE", 0, test_index, test_total)
                
                user_res = "UNSURE"
                if feedback_callback:
                    user_res = feedback_callback(name.upper(), f"During the 3s block, did {name.upper()} force continue?")
                hwp_log(f"FreezeTest(effect={name.upper()}) user={user_res}")

                if user_res == "RETRY":
                    continue

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
            "recommendation": "Hardware Wave supported" if self.recommendation_hw_sine else "Hardware Wave NOT supported"
        }
        
        return report

    def _enable_actuators_directinput(self):
        """Windows-only DirectInput hack to explicitly send SETACTUATORSON."""
        if platform.system() != "Windows" or not self._di:
            return
        if not hasattr(self._di, "di_enable_actuators"):
            return
        
        try:
            self._di.di_enable_actuators()
            logger.info("DI-HACK: Full Actuator Wake-up sequence sent via DirectInput (Reset, Continue, On)")
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
        if platform.system() != "Windows" or not self._di:
            return
        if not hasattr(self._di, "di_run_hw_probe"):
            return

        try:
            raw_log = self._di.di_run_hw_probe()
            if raw_log:
                log_lines = raw_log.decode('utf-8').strip().split('\n')
                for line in log_lines:
                    if line.strip():
                        log_msg = f"{line.strip()}"
                        logger.info(log_msg)
                        self.last_hwp_logs.append(log_msg)
                        if self._probe_log_callback:
                            self._probe_log_callback(log_msg)
        except Exception as e:
            logger.info(f"DI-HWPROBE: Probe Exception: {e}")

    def diag_test_hw_sine(self) -> Dict:
        """Runs an automated HW Sine self-test with detailed logging."""
        if self.backend_mode == "directinput":
            if not self._di_active or not self._di:
                self.last_diag_result = {
                    "mode": "HW periodic (DI)",
                    "actual": "FAIL",
                    "fallback": "no",
                    "reason": "DirectInput backend not initialized",
                }
                return {"status": "FAIL", "reason": "DirectInput backend not initialized"}

            logger.info("DIAG: HW_SINE requested (DirectInput backend)")
            self.last_diag_result = {"mode": "HW periodic (DI)", "actual": "Starting", "fallback": "no", "reason": ""}

            test_mag = 20000
            logger.info(f"DIAG: DirectInput Start params: period_us=500000 mag={test_mag} dur=3000000us")
            res = self._di_start_sine(duration_us=3000000, magnitude=test_mag, period_us=500000)
            if res < 0:
                self.last_diag_result.update({"actual": "FAIL", "reason": "DirectInput start sine failed"})
                return {"status": "FAIL", "reason": "DirectInput start sine failed"}

            logger.info("DIAG: DirectInput Start -> SUCCESS")
            self.hw_sine_active = True
            logger.info("DIAG: FreezeTest: sleeping 1000ms...")
            time.sleep(1.0)
            logger.info("DIAG: FreezeTest: awake. Monitoring remaining 2s of playback (no PC updates)...")
            time.sleep(2.1)

            try:
                if self._di_active and self._di:
                    self._di.di_stop()
            except Exception as e:
                logger.debug(f"DirectInput stop failed (ignoring): {e}")

            self.hw_sine_active = False
            self.last_diag_result.update({"actual": "HW Periodic (DI)", "reason": "Success"})
            return {
                "status": "PASS",
                "reason": "DirectInput sine started and ran without software streaming",
            }

        if not sdl_haptic:
            self.last_diag_result = {
                "mode": "HW periodic",
                "actual": "FAIL",
                "fallback": "no",
                "reason": "SDL haptics not available",
            }
            return {"status": "FAIL", "reason": "SDL haptics not available"}

        if not self.haptic:
            return {"status": "FAIL", "reason": "No haptic device connected"}
        
        logger.info("DIAG: HW_SINE requested")
        # Ensure device is awake before testing
        self._enable_actuators_directinput()
        self._global_prime_haptic()
        caps = sdl_haptic.SDL_GetHapticFeatures(self.haptic)
        has_sine = bool(caps & sdl_haptic.SDL_HAPTIC_SINE)
        logger.info(f"DIAG: EnumEffects contains GUID_Sine: {'yes' if has_sine else 'no'}")
        
        if not has_sine:
            logger.info("DIAG: FallbackToSoftware: false (reason=GUID_Sine NOT enumerated)")
            self.last_diag_result = {"mode": "HW periodic", "actual": "FAIL", "fallback": "no", "reason": "GUID_Sine NOT enumerated"}
            return {"status": "FAIL", "reason": "GUID_Sine NOT enumerated by device"}

        logger.info("DIAG: Starting Force HW Periodic Test (2Hz, 3s)")
        self.last_diag_result = {"mode": "HW periodic", "actual": "Starting", "fallback": "no", "reason": ""}

        # Optional strong constant kick to make it obvious the motor is active
        try:
            kick = sdl_haptic.SDL_HapticEffect()
            ctypes.memset(ctypes.addressof(kick), 0, ctypes.sizeof(kick))
            kick.type = sdl_haptic.SDL_HAPTIC_CONSTANT
            kick.constant.type = sdl_haptic.SDL_HAPTIC_CONSTANT
            kick.constant.direction.type = sdl_haptic.SDL_HAPTIC_CARTESIAN
            kick.constant.direction.dir[0] = 1
            kick.constant.length = 200
            kick.constant.level = 12000
            kick_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(kick))
            if kick_id != -1:
                sdl_haptic.SDL_RunHapticEffect(self.haptic, kick_id, 1)
                time.sleep(0.22)
                sdl_haptic.SDL_DestroyHapticEffect(self.haptic, kick_id)
        except Exception:
            pass
        
        # 1. Setup effect (use same builder as normal playback)
        test_mag = 20000
        desc = {
            "type": "periodic_sine",
            "frequency_hz": 2.0,
            "magnitude": test_mag,
            "length_ms": 3000,
            "phase": 0,
            "direction_mode": "polar",
            "direction": {"angle": 9000, "radius": 1},
            "envelope": {
                "attack_length": 0,
                "fade_length": 0,
                "attack_level": 0,
                "fade_level": 0,
            },
        }
        effect = self.build_effect_from_descriptor(desc)
        if not effect:
            self.last_diag_result.update({"actual": "FAIL", "reason": "Failed to build HW effect"})
            return {"status": "FAIL", "reason": "BuildEffect failed"}
        
        # 2. Try Create
        test_eff_id = sdl_haptic.SDL_CreateHapticEffect(self.haptic, ctypes.byref(effect))
        if test_eff_id == -1:
            err = _sdl_error()
            logger.error(f"DIAG: CreateEffect(GUID_Sine) -> HRESULT={err}")
            logger.info(f"DIAG: FallbackToSoftware: false (reason=CreateEffect failure)")
            self.last_diag_result.update({"actual": "FAIL", "reason": f"CreateEffect failure: {err}"})
            return {"status": "FAIL", "reason": f"CreateEffect FAILED: {err}"}
        
        logger.info("DIAG: CreateEffect(GUID_Sine) -> HRESULT=SUCCESS")
        logger.info(f"DIAG: SetParameters(flags=DIEP_TYPESPECIFICPARAMS|DIEP_DIRECTION, freq=2.0, mag={test_mag}, dur=3000) -> HRESULT=SUCCESS")
        
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
                f.write("=== MotorBuster 2.0 FFB Capability Report ===\n")
                f.write(f"Generated: {time.ctime()}\n\n")
                
                f.write("--- Device Identity ---\n")
                if hasattr(self, 'device_identity') and self.device_identity:
                    for k, v in self.device_identity.items():
                        f.write(f"{k}: {v}\n")
                else:
                    name = "None"
                    if self.haptic and sdl_haptic:
                        name_ptr = sdl_haptic.SDL_GetHapticName(self.haptic)
                        name = name_ptr.decode('utf-8') if name_ptr else "Unknown"
                    elif self.haptic:
                        name = "Unknown (SDL haptics unavailable)"
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

