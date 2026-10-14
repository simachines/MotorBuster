import serial
import serial.tools.list_ports
import threading
import time
import struct
import logging
import math
import os
from typing import Optional, Callable

logger = logging.getLogger('Serial_Manager')

# Persist serial traffic to the same log file used by the FFB engine so the
# exact command/response bytes can be inspected after a sweep.
_serial_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'motorbuster_engine.log'))
if not any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', '') == _serial_log_path for h in logger.handlers):
    _serial_fh = logging.FileHandler(_serial_log_path, mode='a', encoding='utf-8')
    _serial_fh.setLevel(logging.INFO)
    _serial_fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s:%(message)s'))
    logger.addHandler(_serial_fh)


def _hex(data: bytes) -> str:
    '''Return a human-readable hex dump of bytes, e.g. 'AA 01 00 00 ...'.'''
    return ' '.join(f'{b:02X}' for b in data)


# Binary protocol constants
START_BYTE = 0xAA
RESPONSE_BYTE = 0xBB

# Command types (matches finalized STM32 firmware)
CMD_SINE = 0x01       # Bode_Start() sweep point; expects 0xBB response
CMD_CONSTANT = 0x06   # Bode_Start() constant torque; expects 0xBB response
CMD_CENTER = 0x08     # Bode_Center(); no response
CMD_SCALE = 0x09      # Bode_ScaleCal(); no response
CMD_SELFTEST = 0xFE   # Synthetic cosine sweep; expects 0xBB response
CMD_STOP = 0xFF       # Bode_Stop(); no response

# Debug stream commands (raw ADC + reference phase capture)
CMD_STREAM_START = 0x0A   # start streaming; freqHz in bytes [2..5] (LE)
CMD_STREAM_STOP  = 0x0B   # stop streaming
CMD_MARK = 0x0C           # latch current timestamp into t_marker; no response

# STREAM2 commands (bench-mark-phase firmware: DMA block capture + firmware target)
CMD_STREAM2_START = 0x0D  # start STREAM2 block capture; fs (u32) in freqHz field
CMD_STREAM2_STOP  = 0x0E  # stop STREAM2 block capture
CMD_TARGET_SINE   = 0x0F  # set firmware target: freqHz = float32 bits, mag = int16 DI
CMD_TARGET_VALUE        = 0x10  # step target value: mag = int16 DI value (does not reset mark count)
CMD_TARGET_VALUE_BEGIN  = 0x11  # step target value; resets mark count (MARK2 phase-0 latch)

# Debug stream record framing
STREAM_SYNC = 0xCC        # stream record sync marker
STREAM_RECORD_LEN = 15    # [0xCC][adc:2B][theta:4B][t_us:4B][t_marker:4B] little-endian

# STREAM2 block framing
STREAM2_SYNC = b'\xDD\xAD'  # block sync marker
STREAM2_BLOCK_LEN = 574     # 60-byte header + 512-byte samples + 2-byte CRC
STREAM2_VERSION = 2
STREAM2_DRAIN_SECONDS = 0.3  # after STREAM2_STOP, discard in-flight STREAM2 bytes for this long

# Map effect type names to command bytes
EFFECT_CMD_MAP = {
    'sine': CMD_SINE,
    'constant': CMD_CONSTANT,
    'center': CMD_CENTER,
    'scale': CMD_SCALE,
    'selftest': CMD_SELFTEST,
    'stop': CMD_STOP,
    'stream_start': CMD_STREAM_START,
    'stream_stop': CMD_STREAM_STOP,
}

# Reverse map: command byte -> human-readable name (for response logging)
CMD_NAME_MAP = {
    CMD_SINE: 'sine',
    CMD_CONSTANT: 'constant',
    CMD_CENTER: 'center',
    CMD_SCALE: 'scale',
    CMD_SELFTEST: 'selftest',
    CMD_STOP: 'stop',
    CMD_STREAM_START: 'stream_start',
    CMD_STREAM_STOP: 'stream_stop',
}


def scan_ports() -> list[dict]:
    '''Scan available serial ports and return list of {port, description}.'''
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append({
            'port': p.device,
            'description': f'{p.description} ({p.device})',
        })
    return ports


def _pack_command(effect_type: str, freq_hz: float = 0.0, magnitude: int = 0,
                  settle_periods: int = 0, ramp_periods: int = 0,
                  max_torque_nm: float = 0.0) -> Optional[bytes]:
    '''Pack a command into the 13-byte binary format for the MCU.

    Format: [0xAA][cmd:1B][freqHz:4B][mag:2B][settle:1B][ramp:1B][maxTorque_cH:2B][checksum:1B]
    Struct:  <BBIhBBH (12 bytes) + 1-byte XOR checksum.
    max_torque_nm is encoded as centinewton-meters (e.g. 25.0 Nm = 2500 cN.m).
    '''
    cmd_byte = EFFECT_CMD_MAP.get((effect_type or '').lower())
    if cmd_byte is None:
        logger.error(f'Unknown effect type for serial: {effect_type}')
        return None

    freq_hz_int = int(round(max(1.0, min(10000.0, float(freq_hz)))))
    mag_clamped = max(-32767, min(32767, int(magnitude)))  # int16 signed
    settle_c = max(0, min(50, int(settle_periods)))         # 0..50 (rtP.s_period)
    ramp_c = max(0, min(1, int(ramp_periods)))              # 0..1 (rtP.r_period)
    torque_cH = int(max(0, min(65535, round(max_torque_nm * 100))))  # cN.m

    body = struct.pack(
        '<BBIhBBH',
        START_BYTE,
        cmd_byte,
        freq_hz_int,
        mag_clamped,
        settle_c,
        ramp_c,
        torque_cH,
    )

    # Simple XOR checksum over bytes 0..11
    checksum = 0
    for b in body:
        checksum ^= b
    return body + struct.pack('<B', checksum)


def _crc16_ccitt_false(data: bytes) -> int:
    '''CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final XOR).'''
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _pack_command_raw(cmd_byte: int, freq_u32: int = 0, mag_s16: int = 0,
                      settle: int = 0, ramp: int = 0, torque_cH: int = 0) -> bytes:
    '''Pack a 13-byte command from raw field values (no remapping).

    Same wire format as _pack_command (12-byte body + XOR checksum) but accepts
    arbitrary u32/s16 payloads so STREAM2 can place an fs value or float32 bit
    pattern into the freqHz field.
    '''
    freq_u32 = int(freq_u32) & 0xFFFFFFFF
    mag_s16 = max(-32768, min(32767, int(mag_s16)))
    body = struct.pack('<BBIhBBH', START_BYTE, int(cmd_byte), freq_u32, mag_s16,
                       int(settle), int(ramp), int(torque_cH))
    checksum = 0
    for b in body:
        checksum ^= b
    return body + struct.pack('<B', checksum)


def _pack_stream2_start(fs_hz: int) -> bytes:
    '''STREAM2_START (0x0D): fs (u32) placed in the freqHz field.'''
    return _pack_command_raw(CMD_STREAM2_START, freq_u32=int(fs_hz))


def _pack_stream2_stop() -> bytes:
    '''STREAM2_STOP (0x0E).'''
    return _pack_command_raw(CMD_STREAM2_STOP)


def _pack_target_sine(freq_hz: float, amplitude_s16: int) -> bytes:
    '''TARGET_SINE (0x0F): freqHz field = float32 bit pattern, mag = int16 DI.'''
    freq_bits = struct.unpack('<I', struct.pack('<f', float(freq_hz)))[0]
    return _pack_command_raw(CMD_TARGET_SINE, freq_u32=freq_bits, mag_s16=int(amplitude_s16))


def _pack_target_value(value: int) -> bytes:
    '''TARGET_VALUE (0x10): mag = int16 step value (does not reset mark count).'''
    return _pack_command_raw(CMD_TARGET_VALUE, mag_s16=int(value))


def _pack_target_value_begin(value: int) -> bytes:
    '''TARGET_VALUE_BEGIN (0x11): mag = int16 step value; resets mark count.'''
    return _pack_command_raw(CMD_TARGET_VALUE_BEGIN, mag_s16=int(value))


def _parse_stream2_block(data: bytes) -> Optional[dict]:
    '''Parse a 574-byte STREAM2 block (CRC must be validated by the caller).

    Layout (little-endian):
      0 u8 0xDD | 1 u8 0xAD | 2 u8 version(=2) | 3 u8 flags
      4 u32 block_counter | 8 u32 first_index | 12 u16 fs | 14 u16 reserved
     16 u32 t_start_cyc | 20 u32 t_cb_cyc | 24 u32 mark_cyc
     28 u32 dropped_blocks | 32 u8 target_type | 33 u8[3] reserved
     36 f32 target_a | 40 f32 target_b | 44 u32 target_t0_cyc
     48 f32 target_first | 52 f32 target_last | 56 u32 hclk_hz
     60 u16[256] samples | 572 u16 crc16
    '''
    if len(data) < STREAM2_BLOCK_LEN:
        return None
    if data[0] != STREAM2_SYNC[0] or data[1] != STREAM2_SYNC[1]:
        return None
    version = data[2]
    if version != STREAM2_VERSION:
        logger.warning(f'STREAM2 block version {version} != {STREAM2_VERSION}, ignoring')
        return None
    try:
        (flags, block_counter, first_index, fs, reserved,
         t_start_cyc, t_cb_cyc, mark_cyc, dropped_blocks,
         target_type, _res3, target_a, target_b, target_t0_cyc,
         target_first, target_last, hclk_hz) = struct.unpack(
            '<BIIHHIIIIB3sffIffI', data[3:60])
    except struct.error as e:
        logger.error(f'STREAM2 block header parse failed: {e} (raw: {_hex(data[:60])})')
        return None
    try:
        samples = list(struct.unpack('<256H', data[60:572]))
    except struct.error as e:
        logger.error(f'STREAM2 block samples parse failed: {e}')
        return None
    return {
        'version': version,
        'flags': flags,
        'block_counter': block_counter,
        'first_index': first_index,
        'fs': fs,
        'reserved': reserved,
        't_start_cyc': t_start_cyc,
        't_cb_cyc': t_cb_cyc,
        'mark_cyc': mark_cyc,
        'dropped_blocks': dropped_blocks,
        'target_type': target_type,
        'target_a': target_a,
        'target_b': target_b,
        'target_t0_cyc': target_t0_cyc,
        'target_first': target_first,
        'target_last': target_last,
        'hclk_hz': hclk_hz,
        'samples': samples,
    }


def _parse_response(data: bytes) -> Optional[dict]:
    '''Parse MCU response packet and return Bode measurement data.

    Format: [0xBB][cmd_echo:1B][Mag:4B float dB][Ph_D:4B float deg]
    Struct: <BBff (10 bytes)
    '''
    if len(data) < 10:
        return None

    if data[0] != RESPONSE_BYTE:
        logger.debug(f'Response ignored: first byte 0x{data[0]:02X} != 0x{RESPONSE_BYTE:02X} '
                     f'(raw: {_hex(data)})')
        return None

    try:
        # Format: [0xBB][cmd_echo:1B][Mag:4B float dB][Ph_D:4B float deg]
        # '<BBff' unpacks FOUR values: start_byte, cmd_echo, mag_db, phase_deg
        start_byte, cmd_echo, mag_db, phase_deg = struct.unpack('<BBff', data[:10])
    except struct.error as e:
        logger.error(f'Response parse failed: {e} (raw: {_hex(data)})')
        return None

    # Reject non-finite measurements (e.g. NaN from atan2(0, 0) on a dead signal)
    # so a single bogus point cannot scatter the Bode plot.
    if not (math.isfinite(mag_db) and math.isfinite(phase_deg)):
        logger.warning(f'Response rejected (non-finite): mag={mag_db} phase={phase_deg} '
                       f'(raw: {_hex(data[:10])})')
        return None

    logger.debug(f'Response parsed: start=0x{start_byte:02X} cmd=0x{cmd_echo:02X} '
                 f'mag={mag_db:.4f}dB phase={phase_deg:.4f}deg (raw: {_hex(data[:10])})')

    return {
        'cmd': cmd_echo,
        'mag_db': round(float(mag_db), 4),
        'phase_deg': round(float(phase_deg), 4),
    }


def _parse_stream_record(data: bytes):
    '''Parse an MCU debug stream record.

    Format: [0xCC][adc:2B LE][theta:4B float rad][t_us:4B uint32 LE][t_marker:4B uint32 LE]
    '''
    if len(data) < STREAM_RECORD_LEN:
        return None
    if data[0] != STREAM_SYNC:
        return None
    try:
        adc = struct.unpack('<H', data[1:3])[0]
        theta = struct.unpack('<f', data[3:7])[0]
        t_us = struct.unpack('<I', data[7:11])[0]
        t_marker = struct.unpack('<I', data[11:15])[0]
    except struct.error as e:
        logger.error(f'Stream record parse failed: {e} (raw: {_hex(data)})')
        return None
    return {'adc': adc, 'theta': theta, 't_us': t_us, 't_marker': t_marker}


class SerialManager:
    def __init__(self):
        self._port: Optional[serial.Serial] = None
        self._read_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._on_response: Optional[Callable[[dict], None]] = None
        self._on_stream_record: Optional[Callable[[dict], None]] = None
        self._buffer = bytearray()

        # STREAM2 (DMA block capture) state
        self._stream2_mode = False
        self._stream2_drain_until = 0.0
        self._stream2_buffer = bytearray()
        self._on_stream2_block: Optional[Callable[[dict], None]] = None
        self._stream2_crc_errors = 0
        self._stream2_block_gaps = 0
        self._stream2_dropped_blocks = 0
        self._stream2_last_block_counter = None

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def port_name(self) -> Optional[str]:
        return self._port.port if self._port else None

    def set_response_callback(self, callback: Callable[[dict], None]):
        '''Set callback for incoming MCU response data.'''
        self._on_response = callback

    def set_stream_callback(self, callback: Optional[Callable[[dict], None]]):
        '''Set callback for incoming debug stream records (0xCC).'''
        self._on_stream_record = callback

    def set_stream2_callback(self, callback: Optional[Callable[[dict], None]]):
        '''Set callback for incoming STREAM2 blocks (0xDD 0xAD).'''
        self._on_stream2_block = callback

    def connect(self, port: str, baud: int = 115200) -> bool:
        '''Open serial connection to the specified port.'''
        self.disconnect()
        try:
            self._port = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1,
                write_timeout=1.0,
            )
            self._running = True
            self._buffer.clear()
            self._stream2_buffer.clear()
            self._stream2_mode = False
            self._stream2_drain_until = 0.0
            self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._read_thread.start()
            logger.info(f'Serial connected to {port} @ {baud} baud')
            return True
        except Exception as e:
            logger.error(f'Serial connect failed: {e}')
            self._port = None
            return False

    def disconnect(self):
        '''Close serial connection.'''
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=1.0)
            self._read_thread = None
        with self._lock:
            if self._port and self._port.is_open:
                try:
                    self._port.close()
                except Exception:
                    pass
            self._port = None
        self._buffer.clear()
        self._stream2_buffer.clear()
        self._stream2_mode = False
        self._stream2_drain_until = 0.0
        logger.info('Serial disconnected')

    def _write_packet(self, packet: Optional[bytes]) -> bool:
        '''Write a packed command to the serial port under lock.'''
        if packet is None:
            return False
        with self._lock:
            try:
                self._port.write(packet)
                return True
            except Exception as e:
                logger.error(f'Serial write failed: {e}')
                return False

    def send_effect(self, effect_type: str, freq_hz: float, magnitude: int,
                    settle_periods: int = 2, ramp_periods: int = 1,
                    max_torque_nm: float = 25.0) -> bool:
        '''Send a sweep-point effect command (SINE/CONSTANT/SELFTEST).

        Returns True on successful write, False otherwise.
        '''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot send effect')
            return False

        packet = _pack_command(effect_type, freq_hz, magnitude,
                               settle_periods, ramp_periods, max_torque_nm)
        if packet is None:
            return False

        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type={effect_type} freq={freq_hz} mag={magnitude} '
                        f'settle={settle_periods} ramp={ramp_periods} '
                        f'maxTorque={max_torque_nm:.1f}Nm '
                        f'(raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type={effect_type} freq={freq_hz} mag={magnitude} '
                         f'(raw: {_hex(packet)})')
        return ok

    def _send_named(self, name: str, packet: Optional[bytes],
                    freq_hz: float = 0.0, magnitude: int = 0,
                    settle_periods: int = 0, ramp_periods: int = 0,
                    max_torque_nm: float = 0.0) -> bool:
        '''Write a packet and log the name, parameters, and raw bytes.

        Logs consistently with send_effect so every send shows the same detail.
        '''
        if packet is None:
            return False
        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type={name} freq={freq_hz} mag={magnitude} '
                        f'settle={settle_periods} ramp={ramp_periods} '
                        f'maxTorque={max_torque_nm:.1f}Nm '
                        f'(raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type={name} freq={freq_hz} mag={magnitude} '
                         f'(raw: {_hex(packet)})')
        return ok

    def send_stop(self) -> bool:
        '''Send stop command (Bode_Stop). No response expected.'''
        if not self.is_connected:
            return False
        return self._send_named('stop', _pack_command('stop'))

    def send_center(self) -> bool:
        '''Send center command (Bode_Center). No response expected.'''
        if not self.is_connected:
            return False
        return self._send_named('center', _pack_command('center'))

    def send_scale(self, torque_nm: float = 0.0) -> bool:
        '''Send scale calibration command (Bode_ScaleCal) with a known applied torque.

        The firmware computes known_torque = (mag/32767) * maxTorque_cN, so send
        mag=32767 (100%) and put the full torque in the maxTorque field (cN.m).
        '''
        if not self.is_connected:
            return False
        magnitude = 32767
        return self._send_named('scale', _pack_command('scale', 0.0, magnitude, 0, 0, torque_nm),
                                0.0, magnitude, 0, 0, torque_nm)

    def send_selftest(self, freq_hz: float = 0.0, magnitude: int = 0,
                      settle_periods: int = 0, ramp_periods: int = 0,
                      max_torque_nm: float = 0.0) -> bool:
        '''Send selftest command (synthetic cosine sweep). Expects 0xBB response.'''
        if not self.is_connected:
            return False
        return self._send_named('selftest', _pack_command('selftest', freq_hz, magnitude,
                                                          settle_periods, ramp_periods,
                                                          max_torque_nm),
                                freq_hz, magnitude, settle_periods, ramp_periods,
                                max_torque_nm)

    def send_stream_start(self, freq_hz: float):
        '''Start the raw ADC debug stream at freq_hz (capture only, no sweep).'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot start stream')
            return False
        return self._send_named('stream_start', _pack_command('stream_start', freq_hz),
                                freq_hz)

    def send_stream_stop(self):
        '''Stop the raw ADC debug stream.'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot stop stream')
            return False
        return self._send_named('stream_stop', _pack_command('stream_stop'))

    def send_stream2_start(self, fs_hz: int) -> bool:
        '''Start STREAM2 block capture at fs_hz samples/second.'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot start STREAM2')
            return False
        packet = _pack_stream2_start(fs_hz)
        ok = self._write_packet(packet)
        if ok:
            self._stream2_mode = True
            self._stream2_buffer.clear()
            logger.info(f'Serial sent: type=stream2_start fs={fs_hz} (raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=stream2_start (raw: {_hex(packet)})')
        return ok

    def send_stream2_stop(self) -> bool:
        '''Stop STREAM2 block capture.'''
        if not self.is_connected:
            return False
        packet = _pack_stream2_stop()
        ok = self._write_packet(packet)
        if ok:
            self._stream2_mode = False
            self._stream2_drain_until = time.monotonic() + STREAM2_DRAIN_SECONDS
            self._stream2_buffer.clear()
            logger.info(f'Serial sent: type=stream2_stop (raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=stream2_stop (raw: {_hex(packet)})')
        return ok

    def send_target_sine(self, freq_hz: float, amplitude_s16: int) -> bool:
        '''Set the firmware target reference (TARGET_SINE 0x0F).'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot send TARGET_SINE')
            return False
        packet = _pack_target_sine(freq_hz, amplitude_s16)
        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type=target_sine freq={freq_hz} amp={amplitude_s16} '
                        f'(raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=target_sine (raw: {_hex(packet)})')
        return ok

    def send_target_value(self, value: int) -> bool:
        '''Set the firmware step target value (TARGET_VALUE 0x10).'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot send TARGET_VALUE')
            return False
        packet = _pack_target_value(value)
        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type=target_value value={value} (raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=target_value (raw: {_hex(packet)})')
        return ok

    def send_target_value_begin(self, value: int) -> bool:
        '''Set the firmware step target value and reset mark count (TARGET_VALUE_BEGIN 0x11).'''
        if not self.is_connected:
            logger.warning('Serial not connected, cannot send TARGET_VALUE_BEGIN')
            return False
        packet = _pack_target_value_begin(value)
        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type=target_value_begin value={value} (raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=target_value_begin (raw: {_hex(packet)})')
        return ok

    def reset_stream2_stats(self):
        '''Clear STREAM2 integrity counters (per capture run).'''
        self._stream2_crc_errors = 0
        self._stream2_block_gaps = 0
        self._stream2_dropped_blocks = 0
        self._stream2_last_block_counter = None

    def get_stream2_stats(self) -> dict:
        '''Return STREAM2 integrity counters for the current capture run.'''
        return {
            'crc_errors': int(self._stream2_crc_errors),
            'block_gaps': int(self._stream2_block_gaps),
            'dropped_blocks': int(self._stream2_dropped_blocks),
        }

    def send_mark(self) -> bool:
        # Send MARK (0x0C): latch current timestamp into t_marker. No
        # response is expected; writes immediately with no sleeps.
        # Fixed packet: AA 0C 00 00 00 00 00 00 00 00 00 00 A6.
        if not self.is_connected:
            return False
        packet = bytes([START_BYTE, CMD_MARK] + [0x00] * 10 + [0xA6])
        ok = self._write_packet(packet)
        if ok:
            logger.info(f'Serial sent: type=mark (raw: {_hex(packet)})')
        else:
            logger.error(f'Serial send FAILED: type=mark (raw: {_hex(packet)})')
        return ok

    def _read_loop(self):
        '''Background thread: read bytes from serial port.'''
        while self._running:
            try:
                if self._port and self._port.is_open and self._port.in_waiting > 0:
                    data = self._port.read(self._port.in_waiting)
                    if self._stream2_mode:
                        self._stream2_buffer.extend(data)
                        self._process_stream2_buffer()
                    elif time.monotonic() < self._stream2_drain_until:
                        # Drain window: discard leftover STREAM2 bytes arriving just
                        # after stop so the legacy 0xBB/0xCC parser never sees them
                        # (ADC sample data can contain 0xBB/0xCC byte values).
                        pass
                    else:
                        self._buffer.extend(data)
                        self._process_buffer()
                else:
                    time.sleep(0.005)
            except Exception as e:
                if self._running:
                    logger.error(f'Serial read error: {e}')
                    time.sleep(0.1)

    def _process_buffer(self):
        '''Extract and process complete packets: 10-byte 0xBB responses and
        15-byte 0xCC debug stream records (whichever sync byte comes first).'''
        while True:
            idx_bb = self._buffer.find(bytes([RESPONSE_BYTE]))
            idx_cc = self._buffer.find(bytes([STREAM_SYNC]))

            if idx_bb == -1 and idx_cc == -1:
                self._buffer.clear()
                break

            if idx_cc == -1 or (idx_bb != -1 and idx_bb <= idx_cc):
                start_idx, sync, pkt_len = idx_bb, RESPONSE_BYTE, 10
            else:
                start_idx, sync, pkt_len = idx_cc, STREAM_SYNC, STREAM_RECORD_LEN

            if start_idx > 0:
                self._buffer = self._buffer[start_idx:]

            if len(self._buffer) < pkt_len:
                break

            packet = bytes(self._buffer[:pkt_len])
            if sync == RESPONSE_BYTE:
                self._dispatch_response(packet)
            else:
                self._dispatch_stream_record(packet)

            self._buffer = self._buffer[pkt_len:]

    def _dispatch_response(self, packet: bytes):
        parsed = _parse_response(packet)
        if parsed:
            cmd = parsed.get('cmd', 0)
            cmd_name = CMD_NAME_MAP.get(cmd, f'0x{cmd:02X}')
            logger.info(f'Serial received: type={cmd_name} '
                        f'mag={parsed.get("mag_db", 0.0):.4f}dB '
                        f'phase={parsed.get("phase_deg", 0.0):.4f}deg '
                        f'(raw: {_hex(packet)})')
        else:
            logger.info(f'Serial received: unparsed (raw: {_hex(packet)})')
        if parsed and self._on_response:
            try:
                self._on_response(parsed)
            except Exception as e:
                logger.error(f'Response callback error: {e} (raw: {_hex(packet)})')

    def _dispatch_stream_record(self, packet: bytes):
        rec = _parse_stream_record(packet)
        if rec is None:
            logger.warning(f'Serial received: bad stream record (raw: {_hex(packet)})')
            return
        if self._on_stream_record:
            try:
                self._on_stream_record(rec)
            except Exception as e:
                logger.error(f'Stream callback error: {e} (raw: {_hex(packet)})')
        else:
            logger.debug(f'Stream record: adc={rec["adc"]} theta={rec["theta"]:.4f} '
                         f't_us={rec["t_us"]}')

    def _process_stream2_buffer(self):
        '''Extract complete 574-byte STREAM2 blocks, resyncing on 0xDD 0xAD.'''
        while True:
            idx = self._stream2_buffer.find(STREAM2_SYNC)
            if idx == -1:
                if len(self._stream2_buffer) > 1:
                    self._stream2_buffer = self._stream2_buffer[-1:]
                break
            if idx > 0:
                del self._stream2_buffer[:idx]
            if len(self._stream2_buffer) < STREAM2_BLOCK_LEN:
                break
            block = bytes(self._stream2_buffer[:STREAM2_BLOCK_LEN])
            crc_calc = _crc16_ccitt_false(block[:STREAM2_BLOCK_LEN - 2])
            crc_recv = struct.unpack('<H', block[STREAM2_BLOCK_LEN - 2:STREAM2_BLOCK_LEN])[0]
            if crc_calc != crc_recv:
                self._stream2_crc_errors += 1
                logger.warning(f'STREAM2: bad CRC (calc=0x{crc_calc:04X} recv=0x{crc_recv:04X}), resyncing')
                del self._stream2_buffer[:1]
                continue
            parsed = _parse_stream2_block(block)
            if parsed is None:
                del self._stream2_buffer[:1]
                continue
            self._track_stream2_block(parsed)
            self._dispatch_stream2_block(parsed)
            del self._stream2_buffer[:STREAM2_BLOCK_LEN]

    def _track_stream2_block(self, block: dict):
        '''Update integrity counters: block-counter gaps and dropped blocks.'''
        bc = int(block.get('block_counter', 0))
        if self._stream2_last_block_counter is not None:
            expected = (self._stream2_last_block_counter + 1) & 0xFFFFFFFF
            if bc != expected:
                self._stream2_block_gaps += 1
        self._stream2_last_block_counter = bc
        dropped = int(block.get('dropped_blocks', 0))
        if dropped > self._stream2_dropped_blocks:
            self._stream2_dropped_blocks = dropped

    def _dispatch_stream2_block(self, block: dict):
        if self._on_stream2_block:
            try:
                self._on_stream2_block(block)
            except Exception as e:
                logger.error(f'STREAM2 block callback error: {e}')
        else:
            logger.debug(f'STREAM2 block: counter={block["block_counter"]} '
                         f'first_index={block["first_index"]} t0={block["target_t0_cyc"]}')


# Global singleton instance
serial_manager = SerialManager()