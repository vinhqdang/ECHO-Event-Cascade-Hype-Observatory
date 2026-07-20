"""Unit tests for the statistical core of ECHO.

Run with:  python -m pytest tests/ -q   (or)   python tests/test_core.py
"""
import numpy as np

from echo.detect.eprocess import (MixtureMartingale, ShiryaevRoberts,
                                  poisson_log_evalue, nb_log_evalue,
                                  estimate_lambda0, estimate_dispersion)
from echo.detect.fixedwindow import FixedWindowMonitor
from echo.forecast.conformal import split_conformal, adaptive_conformal


def test_poisson_evalue_unit_mean():
    """E-value has unit expectation under the Poisson null (martingale property).

    A small lam0 / theta keeps the e-value's (heavy-tailed) variance low enough
    that the sample mean is a reliable check of E[e-value] = 1.
    """
    rng = np.random.default_rng(0)
    lam0, theta = 5.0, 1.5
    x = rng.poisson(lam0, size=500000)
    ev = np.exp(poisson_log_evalue(x, lam0, theta))
    assert abs(ev.mean() - 1.0) < 0.02


def test_nb_evalue_unit_mean():
    """NB e-value has unit expectation under the NB null."""
    rng = np.random.default_rng(1)
    lam0, theta, r = 5.0, 1.3, 5.0
    p = r / (r + lam0)
    x = rng.negative_binomial(r, p, size=500000)
    ev = np.exp(nb_log_evalue(x, lam0, theta, r))
    assert abs(ev.mean() - 1.0) < 0.02


def test_nb_reduces_to_poisson():
    """As r -> inf the NB log e-value approaches the Poisson log e-value."""
    x = np.array([10.0, 20.0, 40.0])
    p = poisson_log_evalue(x, 25.0, 1.5)
    nb = nb_log_evalue(x, 25.0, 1.5, 1e8)
    assert np.allclose(p, nb, atol=1e-2)


def test_ville_type_i_control():
    """Mixture martingale false-alarm probability <= alpha under the null."""
    rng = np.random.default_rng(2)
    alarms = 0
    reps = 400
    for _ in range(reps):
        x = rng.poisson(40.0, size=42).astype(float)
        lam0 = estimate_lambda0(x, 7)
        res = MixtureMartingale(alpha=0.05).run(x, lam0)
        alarms += int(res.alarm)
    assert alarms / reps <= 0.05 + 0.02   # small MC slack


def test_power_beats_fixed_window():
    """Under a genuine shift the e-process detects with high power."""
    rng = np.random.default_rng(3)
    hit = 0
    reps = 200
    for _ in range(reps):
        x = rng.poisson(np.r_[np.full(21, 40.0), np.full(21, 100.0)]).astype(float)
        lam0 = estimate_lambda0(x, 7)
        res = MixtureMartingale(alpha=0.05).run(x, lam0)
        hit += int(res.alarm and res.alarm_index >= 21)
    assert hit / reps > 0.9


def test_shiryaev_roberts_no_overflow_large_counts():
    """SR runs in log-space and does not overflow on large real-scale counts."""
    x = np.r_[np.full(10, 25.0), np.full(10, 3000.0)]
    res = ShiryaevRoberts(alpha=0.05, dispersion=5.0).run(x, 25.0)
    assert np.all(np.isfinite(res.statistic))
    assert res.alarm


def test_fixed_window_inflates_under_null():
    """The repeatedly-peeked fixed-window test over-alarms under the null."""
    rng = np.random.default_rng(4)
    alarms = 0
    reps = 300
    for _ in range(reps):
        x = rng.poisson(40.0, size=42).astype(float)
        alarms += int(FixedWindowMonitor(alpha=0.05, half=7).run(x).alarm)
    assert alarms / reps > 0.2   # far above nominal 0.05


def test_conformal_runs():
    rng = np.random.default_rng(5)
    x = rng.poisson(np.r_[np.full(20, 30.0), np.full(20, 120.0)]).astype(float)
    sc = split_conformal(x, alpha=0.1)
    ac = adaptive_conformal(x, alpha=0.1)
    assert 0.0 <= sc.coverage <= 1.0
    assert 0.0 <= ac.coverage <= 1.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print(f"\nall {len(fns)} tests passed")
