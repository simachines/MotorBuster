"""Unit tests for the tap-test analysis helpers.

The core guarantee is that _tap_analyse_ringdown recovers the ringing frequency
and damping ratio of a synthetic decaying sine with known parameters, so the
tap-test numbers are meaningful on the bench.

Run with:
    python tests/test_tap_analysis.py
or:
    python -m unittest tests.test_tap_analysis
"""
import math
import os
import sys
import unittest

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from native_app import MotorBusterNativeApp  # noqa: E402

App = MotorBusterNativeApp


def _build_ringdown(f=200.0, zeta=0.05, fs=10000.0, dur=0.5, amp=1000.0):
    wn = 2.0 * math.pi * f
    wd = wn * math.sqrt(1.0 - zeta * zeta)
    t = np.arange(0.0, dur, 1.0 / fs)
    adc = amp * np.exp(-zeta * wn * t) * np.sin(wd * t)
    return t, adc


class TestTapRingdown(unittest.TestCase):
    def test_ringdown_freq_and_damping(self):
        """A 200 Hz decaying sine (zeta=0.05) must give 200 +- 2 Hz and
        damping within +-20% of the true value."""
        t, adc = _build_ringdown(f=200.0, zeta=0.05, fs=10000.0, dur=0.5)
        m = App._tap_analyse_ringdown(t, adc, 10000.0)

        self.assertIsNotNone(m['freq_hz'], 'ringing frequency should be recovered')
        self.assertAlmostEqual(m['freq_hz'], 200.0, delta=2.0,
                               msg=f'freq={m["freq_hz"]}')

        self.assertIsNotNone(m['damping_ratio'], 'damping ratio should be recovered')
        self.assertLess(abs(m['damping_ratio'] - 0.05), 0.05 * 0.20,
                        msg=f'zeta={m["damping_ratio"]}')


class TestPhaseFitDcTerm(unittest.TestCase):
    def test_stream2_fit_reports_dc_term(self):
        """The sine fit's DC term (constant column) must equal the applied DC
        offset, so dc_counts can confirm the wheelbase driver applied lOffset."""
        f = 20.0
        dt = 1e-4
        tau = np.arange(-0.5, 1.5, dt)
        dc = 500.0
        adc = dc + 1000.0 * np.sin(2.0 * math.pi * f * tau)
        m = App._phase_repeat_analyse_stream2(tau, adc, f)

        self.assertIsNotNone(m.get('dc_counts'))
        self.assertAlmostEqual(m['dc_counts'], dc, delta=1.0)


class TestTapBuildSamples(unittest.TestCase):
    def test_build_samples_time_axis(self):
        """_tap_build_samples must concatenate blocks on a contiguous time axis
        using the per-capture first_index, ignoring the u32 DWT clock."""
        fs = 10000
        blocks = [
            {'first_index': 0, 'fs': fs, 'samples': [10, 11, 12]},
            {'first_index': 3, 'fs': fs, 'samples': [13, 14, 15]},
        ]
        built = App._tap_build_samples(blocks)
        self.assertIsNotNone(built)
        self.assertEqual(built['fs'], fs)
        self.assertEqual(built['adc'], [10, 11, 12, 13, 14, 15])
        expected_t = [i / float(fs) for i in range(6)]
        for got, want in zip(built['t'], expected_t):
            self.assertAlmostEqual(got, want, places=9)

    def test_build_samples_sorts_by_first_index(self):
        """Blocks arriving out of order must still yield a monotonic axis."""
        fs = 10000
        blocks = [
            {'first_index': 2, 'fs': fs, 'samples': [20, 21]},
            {'first_index': 0, 'fs': fs, 'samples': [0, 1]},
        ]
        built = App._tap_build_samples(blocks)
        self.assertEqual(built['adc'], [0, 1, 20, 21])
        self.assertEqual(built['t'][0], 0.0)
        self.assertEqual(built['t'][-1], 3.0 / fs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
