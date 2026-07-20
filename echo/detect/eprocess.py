r"""Anytime-valid sequential change-point detection for engagement velocity.

Daily comment counts are the monitored signal. Two families of e-values are
supported:

* **Poisson** — likelihood ratio of ``Poisson(theta*l0)`` vs ``Poisson(l0)``,
  ``exp(-l0*(theta-1)) * theta**x``; unit null mean.
* **Negative-binomial (overdispersion-robust)** — likelihood ratio of
  ``NB(theta*l0, r)`` vs ``NB(l0, r)`` where the dispersion ``r`` is estimated
  from the burn-in. Social-media counts are heavily overdispersed, which
  *invalidates* the Poisson construction (its false-alarm rate inflates well
  above alpha, see the Monte-Carlo study). The NB e-value has unit expectation
  under an NB null and reduces to the Poisson e-value as ``r -> inf``, restoring
  anytime-validity under overdispersion.

Both are combined with two monitoring rules:

* :class:`MixtureMartingale` — method-of-mixtures test martingale over a grid of
  effect sizes ``theta``. By Ville's inequality ``P(sup_t E_t >= 1/alpha) <=
  alpha`` for any stopping rule (the anytime-valid Type-I guarantee).
* :class:`ShiryaevRoberts` — Shiryaev-Roberts e-detector, computed in log-space
  for numerical stability, giving fast detection with ARL control.

All statistics are reported on a log10 scale; an alarm is raised the first time
the statistic crosses ``log10(1/alpha)``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import gammaln, logsumexp


# ---------------------------------------------------------------------------
# per-observation log e-values
# ---------------------------------------------------------------------------
def poisson_log_evalue(x: np.ndarray, lam0: float, theta: float) -> np.ndarray:
    lam0 = max(lam0, 1e-9)
    return -lam0 * (theta - 1.0) + np.asarray(x, float) * np.log(theta)


def nb_log_evalue(x: np.ndarray, lam0: float, theta: float, r: float) -> np.ndarray:
    """log LR of NB(mean=theta*lam0, size=r) vs NB(mean=lam0, size=r).

    NB(mu, r): the size (dispersion) r is shared; variance = mu + mu^2/r.
    log LR = r*log((r+mu0)/(r+mu1)) + x*log( mu1(r+mu0) / (mu0(r+mu1)) ).
    """
    lam0 = max(lam0, 1e-9)
    mu0 = lam0
    mu1 = theta * lam0
    x = np.asarray(x, float)
    term_const = r * (np.log(r + mu0) - np.log(r + mu1))
    term_x = x * (np.log(mu1) + np.log(r + mu0) - np.log(mu0) - np.log(r + mu1))
    return term_const + term_x


def estimate_lambda0(counts: np.ndarray, burn_in: int) -> float:
    burn = np.asarray(counts, float)[:max(burn_in, 1)]
    return float(np.mean(burn)) if burn.size else 1.0


def estimate_dispersion(counts: np.ndarray, burn_in: int, shrink: float = 1.0,
                        r_max: float = 1e6) -> float:
    """Method-of-moments NB size ``r`` from burn-in (var = mu + mu^2/r).

    Returns a large r (~Poisson) when the burn-in is not overdispersed. A short
    burn-in yields a noisy, upward-biased ``r`` (i.e. dispersion is
    *under*-estimated), which inflates the false-alarm rate. ``shrink`` < 1
    multiplies ``r`` downward, conservatively assuming *more* dispersion than the
    point estimate; this restores near-nominal Type-I control under
    overdispersion at a small power cost (see Monte-Carlo study).
    """
    burn = np.asarray(counts, float)[:max(burn_in, 2)]
    mu = burn.mean()
    var = burn.var(ddof=1) if burn.size > 1 else mu
    if var <= mu or mu <= 0:
        return r_max
    r = shrink * mu * mu / (var - mu)
    return float(min(max(r, 1e-3), r_max))


# ---------------------------------------------------------------------------
@dataclass
class DetectionResult:
    alarm: bool
    alarm_index: int | None
    statistic: np.ndarray              # log10 statistic path
    threshold: float                   # log10(1/alpha)
    lam0: float
    dispersion: float | None = None

    @property
    def latency(self) -> int | None:
        return self.alarm_index


def _log_evalue_matrix(counts, lam0, thetas, dispersion):
    """(T, K) matrix of per-day log e-values for each theta."""
    cols = []
    for th in thetas:
        if dispersion is None:
            cols.append(poisson_log_evalue(counts, lam0, th))
        else:
            cols.append(nb_log_evalue(counts, lam0, th, dispersion))
    return np.column_stack(cols)


@dataclass
class MixtureMartingale:
    alpha: float = 0.05
    thetas: tuple[float, ...] = (1.25, 1.5, 2.0, 3.0)
    dispersion: float | None = None    # None => Poisson; else NB size r

    def run(self, counts: np.ndarray, lam0: float,
            dispersion: float | None = "inherit") -> DetectionResult:
        counts = np.asarray(counts, float)
        thetas = np.asarray(self.thetas, float)
        disp = self.dispersion if dispersion == "inherit" else dispersion
        log_ev = _log_evalue_matrix(counts, lam0, thetas, disp)
        log_cum = np.cumsum(log_ev, axis=0)                 # per-theta martingale
        logw = np.log(np.ones(len(thetas)) / len(thetas))
        log_E = logsumexp(log_cum + logw[None, :], axis=1)  # mixture
        log_thresh = np.log(1.0 / self.alpha)
        crossings = np.where(log_E >= log_thresh)[0]
        alarm_idx = int(crossings[0]) if crossings.size else None
        return DetectionResult(alarm_idx is not None, alarm_idx,
                               log_E / np.log(10), log_thresh / np.log(10),
                               lam0, disp)


@dataclass
class ShiryaevRoberts:
    alpha: float = 0.05
    thetas: tuple[float, ...] = (1.25, 1.5, 2.0, 3.0)
    dispersion: float | None = None

    def run(self, counts: np.ndarray, lam0: float,
            dispersion: float | None = "inherit") -> DetectionResult:
        counts = np.asarray(counts, float)
        thetas = np.asarray(self.thetas, float)
        disp = self.dispersion if dispersion == "inherit" else dispersion
        log_ev = _log_evalue_matrix(counts, lam0, thetas, disp)  # (T,K)
        logw = np.log(np.ones(len(thetas)) / len(thetas))
        log_lambda = logsumexp(log_ev + logw[None, :], axis=1)   # per-day log e-value
        log_thresh = np.log(1.0 / self.alpha)
        # log R_t = log(1 + R_{t-1}) + log_lambda_t, stable via logaddexp
        logR = -np.inf
        path = np.empty(len(counts))
        alarm_idx = None
        for t, ll in enumerate(log_lambda):
            log1p_R = np.logaddexp(0.0, logR)   # log(1 + R_{t-1})
            logR = log1p_R + ll
            path[t] = logR
            if alarm_idx is None and logR >= log_thresh:
                alarm_idx = t
        return DetectionResult(alarm_idx is not None, alarm_idx,
                               path / np.log(10), log_thresh / np.log(10),
                               lam0, disp)
