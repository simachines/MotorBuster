"""Unit tests for the step response analysis.

Verifies that dead time (MARK2 -> 10% of step) and the 10-90% rise time are
recovered within tolerance from a synthetic first-order step with noise,
including a low-SNR (small-step) case.
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
FS = 10000.0
DT = 1.0 / FS


def _first_order_step(tau, baseline, step, dead_time, tau_rc, noise_rms, seed=0):
    """Synthetic step: baseline, then first-order rise to baseline+step after
    a pure delay of dead_time, plus Gaussian noise."""
    rng = np.random.default_rng(seed)
    y = np.full_like(tau, float(baseline))
    t = tau - dead_time
    mask = t >= 0.0
    y[mask] = baseline + step * (1.0 - np.exp(-t[mask] / tau_rc))
    y += rng.normal(0.0, noise_rms, size=tau.shape)
    return y


class TestStepAnalysis(unittest.TestCase):
    DEAD_TIME = 0.008    # 8 ms pure delay
    TAU_RC = 0.010       # 10 ms first-order time constant

    def _expected(self):
        # 10% crossing time = dead_time + tau*ln(10/9); rise time = tau*ln(9).
        t10 = self.DEAD_TIME + self.TAU_RC * math.log(10.0 / 9.0)
        rise = self.TAU_RC * math.log(9.0)
        return t10, rise

    def test_dead_time_and_rise_time_clean(self):
        tau = np.arange(-0.15, 0.50, DT)
        adc = _first_order_step(tau, 2000.0, 500.0, self.DEAD_TIME, self.TAU_RC, 1.0)
        m = App._step_analyse(tau, adc, int(FS), hold_s=0.5)
        self.assertIsNotNone(m['dead_time_ms'])
        self.assertIsNotNone(m['rise_time_10_90_ms'])
        t10, rise = self._expected()
        self.assertAlmostEqual(m['dead_time_ms'], t10 * 1e3, delta=0.1)
        self.assertAlmostEqual(m['rise_time_10_90_ms'], rise * 1e3, delta=0.1)

    def test_small_step_robustness(self):
        # step ~ 7x noise RMS. At this SNR the 10% threshold sits near the noise
        # floor and the first-order 90% crossing has very low slope, so the
        # threshold crossings are inherently noisier than the clean case; the
        # tolerances below reflect that (~1 sigma, fixed noise seed).
        tau = np.arange(-0.15, 0.50, DT)
        noise_rms = 20.0
        step = 7.0 * noise_rms
        adc = _first_order_step(tau, 2000.0, step, self.DEAD_TIME, self.TAU_RC, noise_rms, seed=4)
        m = App._step_analyse(tau, adc, int(FS), hold_s=0.5)
        self.assertIsNotNone(m['dead_time_ms'])
        self.assertIsNotNone(m['rise_time_10_90_ms'])
        self.assertAlmostEqual(m['baseline_noise_rms'], noise_rms, delta=5.0)
        t10, rise = self._expected()
        self.assertAlmostEqual(m['dead_time_ms'], t10 * 1e3, delta=0.6)
        self.assertAlmostEqual(m['rise_time_10_90_ms'], rise * 1e3, delta=3.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
