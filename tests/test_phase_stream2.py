"""Unit tests for the STREAM2 phase acquisition path.

The core guarantee is that the legacy (0xCC) and STREAM2 analysers report the
SAME phase for the same physical signal, so legacy and STREAM2 numbers compare
directly.  We verify this with a synthetic 20 Hz sine delayed by a known 7.0 ms.

Run with:
    python tests/test_phase_stream2.py
or:
    python -m unittest tests.test_phase_stream2
"""
import math
import os
import sys
import unittest

import numpy as np

# Make the repo root importable so we can `import native_app` (which itself pulls
# in dearpygui / SDL3 / pyserial from .dependencies).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from native_app import MotorBusterNativeApp  # noqa: E402

App = MotorBusterNativeApp

F = 20.0           # Hz
DELAY_S = 0.007    # known delay: the wheel response lags the target by 7 ms
DT = 1e-4          # 10 kHz sample spacing
MARK2_S = 0.5      # MARK2 (phase reference) absolute time


def _build_synthetic_data():
    """Return (legacy_records, tau_s, adc) for the same delayed sine.

    The ADC is A*sin(2*pi*F*(T - DELAY_S)); MARK2 falls at T = 0.5 s in both
    representations, so both analysers must report -degrees(2*pi*F*DELAY_S).
    """
    T = np.arange(0.0, 1.5 + DT, DT)
    adc = 1000.0 * np.sin(2 * math.pi * F * (T - DELAY_S))

    records = []
    for Ti, a in zip(T, adc):
        t_us = int(round(Ti * 1e6))
        if Ti < 0.2:
            tm = 0          # before MARK1
        elif Ti < MARK2_S:
            tm = 200000     # after MARK1, before MARK2
        else:
            tm = 500000     # after MARK2
        records.append({'t_us': t_us, 'adc': float(a), 'theta': 0.0, 't_marker': tm})

    tau = T - MARK2_S       # STREAM2 time axis, seconds relative to MARK2
    return records, tau, adc


class TestPhaseConvention(unittest.TestCase):
    def test_legacy_and_stream2_phase_identical(self):
        records, tau, adc = _build_synthetic_data()
        legacy = App._phase_repeat_analyse(records, F)
        stream2 = App._phase_repeat_analyse_stream2(tau, adc, F)

        self.assertIsNotNone(legacy['phase_deg_m2'], 'legacy fit should be valid')
        self.assertIsNotNone(stream2['phase_deg_m2'], 'STREAM2 fit should be valid')

        diff = (legacy['phase_deg_m2'] - stream2['phase_deg_m2'] + 180.0) % 360.0 - 180.0
        self.assertLess(
            abs(diff), 1e-6,
            f'phase mismatch: legacy={legacy["phase_deg_m2"]} stream2={stream2["phase_deg_m2"]}'
        )

    def test_phase_matches_known_delay(self):
        records, tau, adc = _build_synthetic_data()
        legacy = App._phase_repeat_analyse(records, F)
        stream2 = App._phase_repeat_analyse_stream2(tau, adc, F)

        expected = -math.degrees(2 * math.pi * F * DELAY_S) % 360.0
        if expected > 180.0:
            expected -= 360.0

        for phase in (legacy['phase_deg_m2'], stream2['phase_deg_m2']):
            d = (phase - expected + 180.0) % 360.0 - 180.0
            self.assertLess(abs(d), 1e-6, f'phase {phase} != expected {expected}')


class TestStream2BuildSamples(unittest.TestCase):
    def test_tau_reconstruction_and_signed_wrap(self):
        """Verify the uint32 timing math in _stream2_build_samples.

        Place MARK2 so that a block straddles the 2**32 cycle counter wrap and
        check that tau_s is the correctly signed value for every sample.
        """
        hclk = 168000000
        fs = 10000
        cps = hclk // fs                       # exact integer cycles per sample
        t0_cyc = (1 << 32) - 3 * cps           # MARK2 just before the wrap
        first_index = 0
        t_start = (t0_cyc - 2 * cps) & 0xFFFFFFFF  # block starts just before MARK2
        ref_a, ref_b = 0.5, -0.2
        n = 256

        blk = {
            'target_t0_cyc': t0_cyc,
            'target_a': ref_a,
            'target_b': ref_b,
            'hclk_hz': hclk,
            'fs': fs,
            't_start_cyc': t_start,
            'first_index': first_index,
            'samples': list(range(n)),
            'target_first': 0.0,
            'target_last': 0.0,
        }

        app = object.__new__(App)  # no GUI/__init__ needed for this pure helper
        built = app._stream2_build_samples([blk], F)
        self.assertIsNotNone(built)

        tau = built['tau']
        target = built['target']
        self.assertEqual(len(tau), n)

        for k in range(n):
            t_cyc = (t_start + (first_index + k) * cps) & 0xFFFFFFFF
            d = ((t_cyc - t0_cyc + 0x80000000) % 0x100000000) - 0x80000000
            expected_tau = d / hclk
            expected_target = ref_a * math.sin(2 * math.pi * F * expected_tau) \
                + ref_b * math.cos(2 * math.pi * F * expected_tau)
            self.assertAlmostEqual(tau[k], expected_tau, places=12)
            self.assertAlmostEqual(target[k], expected_target, places=12)

        # Sanity: the block genuinely crosses the 32-bit wrap.
        self.assertGreaterEqual(t_start, (1 << 32) - 10 * cps)

    def test_target_sign_convention(self):
        """The target column must be A*sin(2*pi*f*tau), not A*cos(...).

        Phase 0 at MARK2 means target(tau=0) ~= 0 and rising, matching the
        firmware's sinf reference and the legacy/STREAM2 phase convention.
        """
        hclk = 168000000
        fs = 10000
        cps = hclk // fs
        A = 1000.0
        ref_a, ref_b = A, 0.0      # pure sine, phase 0 at MARK2
        t0_cyc = 5 * cps           # MARK2 lands exactly on sample index 5
        n = 256
        blk = {
            'target_t0_cyc': t0_cyc,
            'target_a': ref_a,
            'target_b': ref_b,
            'hclk_hz': hclk,
            'fs': fs,
            't_start_cyc': 0,
            'first_index': 0,
            'samples': list(range(n)),
            'target_first': 0.0,
            'target_last': 0.0,
        }

        app = object.__new__(App)
        built = app._stream2_build_samples([blk], F)
        tau = built['tau']
        target = built['target']

        self.assertAlmostEqual(tau[5], 0.0, places=12)    # MARK2 sample
        self.assertAlmostEqual(target[5], 0.0, places=9)  # ~0 at tau = 0
        self.assertGreater(target[6], 0.0)                # rising just after MARK2
        self.assertLess(target[4], 0.0)                   # negative just before MARK2

        for k in range(n):
            expected = A * math.sin(2 * math.pi * F * tau[k])
            self.assertAlmostEqual(target[k], expected, places=9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
