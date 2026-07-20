"""Conformal prediction intervals for diffusion forecasts.

Provides distribution-free prediction intervals for a one-step forecast of
engagement velocity. A transparent forecaster (AR(1)-on-log) is wrapped so the
methodological point — *calibrated uncertainty*, not point accuracy — is the
contribution.

Two variants:

* :func:`split_conformal` — classic split conformal; valid under exchangeability
  of calibration residuals.
* :func:`adaptive_conformal` — adaptive conformal inference (ACI): the effective
  miscoverage level is updated online, ``alpha_t+1 = alpha_t + gamma*(alpha -
  err_t)``, which guarantees long-run coverage *even under distribution shift*
  (the regime changes that define hype events break exchangeability, so ACI is
  the appropriate tool for these series).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ConformalResult:
    coverage: float
    mean_width: float
    alpha: float
    point: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    y_true: np.ndarray


def ar1_log_forecast(train: np.ndarray) -> tuple[float, float]:
    """Fit AR(1) on log1p counts; return (phi, intercept)."""
    y = np.log1p(np.asarray(train, float))
    if len(y) < 3:
        return 0.0, float(y.mean()) if len(y) else 0.0
    x0, x1 = y[:-1], y[1:]
    A = np.vstack([x0, np.ones_like(x0)]).T
    phi, c = np.linalg.lstsq(A, x1, rcond=None)[0]
    return float(phi), float(c)


def split_conformal(counts: np.ndarray, alpha: float = 0.1,
                    train_frac: float = 0.5, cal_frac: float = 0.25
                    ) -> ConformalResult:
    """Rolling one-step split-conformal intervals on a count series.

    Data are split train / calibration / test in temporal order. The absolute
    calibration residual quantile sets a symmetric interval on the log scale,
    back-transformed to counts.
    """
    counts = np.asarray(counts, float)
    T = len(counts)
    n_tr = max(3, int(T * train_frac))
    n_cal = max(2, int(T * cal_frac))
    phi, c = ar1_log_forecast(counts[:n_tr])

    def pred(prev):
        return phi * np.log1p(prev) + c

    # calibration residuals
    cal_res = []
    for t in range(n_tr, n_tr + n_cal):
        if t == 0:
            continue
        yhat = pred(counts[t - 1])
        cal_res.append(abs(np.log1p(counts[t]) - yhat))
    cal_res = np.array(cal_res)
    q = np.quantile(cal_res, min(1.0, (1 - alpha) * (1 + 1 / max(len(cal_res), 1))))

    lower, upper, point, ytrue = [], [], [], []
    for t in range(n_tr + n_cal, T):
        yhat = pred(counts[t - 1])
        point.append(np.expm1(yhat))
        lower.append(np.expm1(yhat - q))
        upper.append(np.expm1(yhat + q))
        ytrue.append(counts[t])
    point = np.array(point); lower = np.maximum(np.array(lower), 0)
    upper = np.array(upper); ytrue = np.array(ytrue)
    if len(ytrue) == 0:
        return ConformalResult(float("nan"), float("nan"), alpha,
                               point, lower, upper, ytrue)
    covered = (ytrue >= lower) & (ytrue <= upper)
    return ConformalResult(float(np.mean(covered)),
                           float(np.mean(upper - lower)), alpha,
                           point, lower, upper, ytrue)


def adaptive_conformal(counts: np.ndarray, alpha: float = 0.1,
                       train_frac: float = 0.4, gamma: float = 0.05
                       ) -> ConformalResult:
    """Adaptive conformal inference (Gibbs & Candes) for non-exchangeable series.

    After an initial fit, at each step the interval half-width is the empirical
    ``(1 - alpha_t)`` quantile of past absolute residuals, and ``alpha_t`` is
    updated toward maintaining the target coverage. Long-run coverage -> 1-alpha
    regardless of distribution shift.
    """
    counts = np.asarray(counts, float)
    T = len(counts)
    n_tr = max(3, int(T * train_frac))
    phi, c = ar1_log_forecast(counts[:n_tr])

    def pred(prev):
        return phi * np.log1p(prev) + c

    resid_hist = [abs(np.log1p(counts[t]) - pred(counts[t - 1]))
                  for t in range(1, n_tr)]
    alpha_t = alpha
    lower, upper, point, ytrue = [], [], [], []
    for t in range(n_tr, T):
        yhat = pred(counts[t - 1])
        a = min(max(alpha_t, 1e-3), 0.999)
        q = np.quantile(resid_hist, 1 - a) if resid_hist else 0.0
        lo, hi = np.expm1(yhat - q), np.expm1(yhat + q)
        point.append(np.expm1(yhat)); lower.append(max(lo, 0)); upper.append(hi)
        ytrue.append(counts[t])
        err = 0.0 if (counts[t] >= lo and counts[t] <= hi) else 1.0
        alpha_t = alpha_t + gamma * (alpha - err)
        resid_hist.append(abs(np.log1p(counts[t]) - yhat))
    point = np.array(point); lower = np.array(lower)
    upper = np.array(upper); ytrue = np.array(ytrue)
    if len(ytrue) == 0:
        return ConformalResult(float("nan"), float("nan"), alpha,
                               point, lower, upper, ytrue)
    covered = (ytrue >= lower) & (ytrue <= upper)
    return ConformalResult(float(np.mean(covered)),
                           float(np.mean(upper - lower)), alpha,
                           point, lower, upper, ytrue)
