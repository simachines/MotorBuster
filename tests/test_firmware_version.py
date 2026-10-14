"""Unit tests for the GET_VERSION (0x12) firmware version reply.

The firmware replies to 0x12 with two 10-byte 0xBB frames:
    BB 12: major, minor, patch, board_id, yy, mm, dd, stream2_header_ver
    BB 13: 4-byte git hash, dirty flag, 3 reserved bytes
These must be parsed as version replies (NOT mag/phase measurements) and
combined into a human-readable version string.

Run with:
    python tests/test_firmware_version.py
or:
    python -m unittest tests.test_firmware_version
"""
import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from server.serial_manager import (  # noqa: E402
    RESPONSE_BYTE,
    CMD_GET_VERSION,
    CMD_VERSION_GIT,
    _pack_get_version,
    _parse_version_frame,
    _format_version,
)


class TestVersionFrameParsing(unittest.TestCase):
    def test_parse_version_info_frame(self):
        data = bytes([RESPONSE_BYTE, CMD_GET_VERSION, 1, 0, 0, 1, 26, 10, 14, 2])
        f = _parse_version_frame(data)
        self.assertIsNotNone(f)
        self.assertEqual(f['cmd'], CMD_GET_VERSION)
        self.assertEqual(f['major'], 1)
        self.assertEqual(f['minor'], 0)
        self.assertEqual(f['patch'], 0)
        self.assertEqual(f['board_id'], 1)
        self.assertEqual(f['build_yy'], 26)
        self.assertEqual(f['build_mm'], 10)
        self.assertEqual(f['build_dd'], 14)
        self.assertEqual(f['stream2_header_ver'], 2)

    def test_parse_version_git_frame(self):
        data = bytes([RESPONSE_BYTE, CMD_VERSION_GIT, 0xF1, 0xAA, 0x21, 0x32, 0x00, 0, 0, 0])
        f = _parse_version_frame(data)
        self.assertIsNotNone(f)
        self.assertEqual(f['cmd'], CMD_VERSION_GIT)
        self.assertEqual(f['git_raw'], bytes([0xF1, 0xAA, 0x21, 0x32]))
        self.assertEqual(f['dirty'], 0)

    def test_non_version_frame_not_parsed_as_version(self):
        # A normal 0xBB 0x01 mag/phase response must not parse as a version frame.
        data = bytes([RESPONSE_BYTE, 0x01]) + b'\x00' * 8
        self.assertIsNone(_parse_version_frame(data))


class TestVersionFormatting(unittest.TestCase):
    def test_format_version_clean_f411(self):
        part1 = {'major': 1, 'minor': 0, 'patch': 0, 'board_id': 1,
                 'build_yy': 26, 'build_mm': 10, 'build_dd': 14, 'stream2_header_ver': 2}
        part2 = {'git_raw': bytes([0xF1, 0xAA, 0x21, 0x32]), 'dirty': 0}
        info = _format_version(part1, part2)
        self.assertEqual(info['version'], '1.0.0')
        self.assertEqual(info['board'], 'F411')
        self.assertEqual(info['git_hash'], 'F1AA2132')
        self.assertEqual(info['dirty_str'], 'clean')
        self.assertEqual(info['build_date'], '2026-10-14')
        self.assertEqual(info['display'],
                         '1.0.0 · F411 · F1AA2132 · clean · built 2026-10-14')

    def test_format_version_dirty_f407(self):
        part1 = {'major': 2, 'minor': 3, 'patch': 1, 'board_id': 2,
                 'build_yy': 25, 'build_mm': 1, 'build_dd': 5, 'stream2_header_ver': 2}
        part2 = {'git_raw': bytes([0xAB, 0xCD, 0xEF, 0x12]), 'dirty': 1}
        info = _format_version(part1, part2)
        self.assertEqual(info['board'], 'F407')
        self.assertEqual(info['git_hash'], 'ABCDEF12')
        self.assertEqual(info['dirty_str'], 'dirty')
        self.assertEqual(info['display'],
                         '2.3.1 · F407 · ABCDEF12 · dirty · built 2025-01-05')

    def test_format_version_unknown_dirty(self):
        part1 = {'major': 1, 'minor': 0, 'patch': 0, 'board_id': 0,
                 'build_yy': 26, 'build_mm': 10, 'build_dd': 14, 'stream2_header_ver': 2}
        part2 = {'git_raw': bytes([0x00, 0x00, 0x00, 0x00]), 'dirty': 0xFF}
        info = _format_version(part1, part2)
        self.assertEqual(info['board'], 'unknown')
        self.assertEqual(info['dirty_str'], 'unknown')


class TestPackGetVersion(unittest.TestCase):
    def test_pack_get_version(self):
        pkt = _pack_get_version()
        self.assertEqual(len(pkt), 13)
        self.assertEqual(pkt[0], 0xAA)
        self.assertEqual(pkt[1], CMD_GET_VERSION)


if __name__ == '__main__':
    unittest.main(verbosity=2)
