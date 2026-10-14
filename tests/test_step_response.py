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


def _damped_step(tau, baseline, step, dead_time, zeta, omega_n, noise_rms, seed=0):
    """Synthetic underdamped second-order step (zeta < 1), plus Gaussian noise."""
    rng = np.random.default_rng(seed)
    y = np.full_like(tau, float(baseline))
    t = tau - dead_time
    mask = t >= 0.0
    wd = omega_n * math.sqrt(1.0 - zeta * zeta)
    y[mask] = baseline + step * (1.0 - np.exp(-zeta * omega_n * t[mask]) *
                                 (np.cos(wd * t[mask]) + (zeta / math.sqrt(1.0 - zeta * zeta)) * np.sin(wd * t[mask])))
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

    def test_metrics_on_synthetic_step(self):
        tau = np.arange(-0.15, 0.50, DT)
        step = 500.0
        adc = _first_order_step(tau, 2000.0, step, self.DEAD_TIME, self.TAU_RC, 1.0, seed=2)
        m = App._step_analyse(tau, adc, int(FS), hold_s=0.5)
        # max slope within +/-10% of the instantaneous max (step / tau)
        expected_slope = step / (self.TAU_RC * 1e3)  # counts/ms
        self.assertIsNotNone(m['max_slope_counts_per_ms'])
        self.assertAlmostEqual(m['max_slope_counts_per_ms'], expected_slope, delta=0.1 * expected_slope)
        # first-order step has no overshoot
        self.assertIsNotNone(m['overshoot_pct'])
        self.assertLess(abs(m['overshoot_pct']), 2.0)
        # settling to within 2% of the step: dead_time + tau * ln(50)
        expected_settle = (self.DEAD_TIME + self.TAU_RC * math.log(50.0)) * 1e3
        self.assertIsNotNone(m['settling_time_2pct_ms'])
        self.assertAlmostEqual(m['settling_time_2pct_ms'], expected_settle, delta=1.0)

    def test_no_overshoot_below_noise_floor(self):
        tau = np.arange(-0.15, 0.50, DT)
        noise_rms = 6.6
        step = 104.0
        adc = _first_order_step(tau, 2000.0, step, self.DEAD_TIME, self.TAU_RC, noise_rms, seed=0)
        m = App._step_analyse(tau, adc, int(FS), hold_s=0.5)
        self.assertIsNotNone(m['overshoot_pct'])
        self.assertIsNotNone(m['overshoot_noise_floor_pct'])
        self.assertLessEqual(m['overshoot_pct'], m['overshoot_noise_floor_pct'])

    def test_damped_step_overshoot(self):
        tau = np.arange(-0.15, 0.50, DT)
        zeta = 0.349          # ~31% peak overshoot
        omega_n = 2.0 * math.pi * 20.0
        adc = _damped_step(tau, 2000.0, 200.0, self.DEAD_TIME, zeta, omega_n, 0.0, seed=0)
        m = App._step_analyse(tau, adc, int(FS), hold_s=0.5)
        self.assertIsNotNone(m['overshoot_pct'])
        self.assertGreaterEqual(m['overshoot_pct'], 25.0)
        self.assertLessEqual(m['overshoot_pct'], 33.0)


class TestStepResultsIO(unittest.TestCase):
    def test_step_result_and_summary_roundtrip(self):
        import tempfile
        app = object.__new__(App)
        app.log = lambda msg: None  # stub UI log for headless IO test
        row = {
            'magnitude_pct': 50.0, 'direction': '0_to_pos',
            'baseline_counts': 2000.0, 'final_counts': 2500.0,
            'baseline_noise_rms': 1.5, 'dead_time_ms': 8.0, 't50_ms': 15.0,
            't90_ms': 25.0, 'rise_time_10_90_ms': 17.0,
            'max_slope_counts_per_ms': 60.0, 'avg_slew_counts_per_ms': 30.0,
            'max_slope_Nm_per_ms': 1.578947, 'avg_slew_Nm_per_ms': 0.789474,
            'overshoot_pct': 2.5, 'settling_time_2pct_ms': 40.0,
            'settling_band_used': '2%', 'crc_errors': 0, 'block_gaps': 0,
            'dropped_blocks': 0, 'host_pulse_ms': 0.1,
        }
        with tempfile.TemporaryDirectory() as d:
            results_path = os.path.join(d, 'results_step.csv')
            summary_path = os.path.join(d, 'results_step_summary.csv')
            with open(results_path, 'w', encoding='utf-8', newline='') as f:
                f.write(','.join(app._STEP_RESULT_COLUMNS) + '\n')
            app._step_append_result(results_path, row)
            app._step_write_summary(summary_path, [row])

            with open(results_path, 'r', encoding='utf-8', newline='') as f:
                lines = f.read().splitlines()
            self.assertEqual(len(lines), 2)
            cols = dict(zip(app._STEP_RESULT_COLUMNS, lines[1].split(',')))
            self.assertEqual(cols['direction'], '0_to_pos')
            self.assertAlmostEqual(float(cols['dead_time_ms']), 8.0)
            self.assertAlmostEqual(float(cols['rise_time_10_90_ms']), 17.0)

            with open(summary_path, 'r', encoding='utf-8', newline='') as f:
                summary = f.read()
            self.assertIn('0_to_pos', summary)
            self.assertIn('dead_time_ms_mean', summary)


if __name__ == '__main__':
    unittest.main(verbosity=2)
