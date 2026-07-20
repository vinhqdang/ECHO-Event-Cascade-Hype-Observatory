"""Monte-Carlo validation of the anytime-valid claim (RQ2).

Real event data give a single realisation, so they cannot by themselves establish
*Type-I error control under continuous monitoring* — that requires many
replications with a known ground truth. This module generates count series from a
piecewise-Poisson model (optionally with overdispersion and weekly seasonality
calibrated to an observed series) and reports, across replications:

* **false-alarm probability** under the null (no change) — should stay <= alpha
  for the e-process but inflate badly for the repeatedly-peeked fixed window; and
* **detection power and latency** under the alternative (a genuine rate shift).

This is the evidence that the e-process detects genuine shifts *faster and more
reliably* than fixed-window comparisons while actually controlling error.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from echo.detect.eprocess import (MixtureMartingale, ShiryaevRoberts,
                                  estimate_lambda0, estimate_dispersion)
from echo.detect.fixedwindow import FixedWindowMonitor


@dataclass
class SimConfig:
    horizon: int = 42                  # days monitored (matches 6-week window)
    burn_in: int = 7
    lambda0: float = 40.0              # baseline comments/day
    change_point: int | None = 21      # None => null (no change)
    effect: float = 2.5               # post-change rate multiplier
    overdispersion: float = 0.0       # negative-binomial dispersion (0 => Poisson)
    weekly_amp: float = 0.0           # relative weekly seasonal amplitude
    seed: int = 0


def simulate_series(cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(cfg.horizon)
    rate = np.full(cfg.horizon, cfg.lambda0, float)
    if cfg.change_point is not None:
        rate[cfg.change_point:] *= cfg.effect
    if cfg.weekly_amp:
        rate *= (1 + cfg.weekly_amp * np.sin(2 * np.pi * t / 7))
    if cfg.overdispersion > 0:
        # NB parameterised by mean=rate, dispersion r; var = mean + mean^2/r
        r = 1.0 / cfg.overdispersion
        p = r / (r + rate)
        return rng.negative_binomial(r, p).astype(float)
    return rng.poisson(rate).astype(float)


@dataclass
class MethodOutcome:
    name: str
    alarm_rate: float                  # P(alarm) over reps
    mean_latency: float                # mean detection delay (alt only, alarms)
    median_latency: float
    detection_power: float             # P(alarm after true change) (alt only)


def _summarise(name, alarms, latencies, powers) -> MethodOutcome:
    lat = np.array([x for x in latencies if x is not None], float)
    return MethodOutcome(
        name=name,
        alarm_rate=float(np.mean(alarms)),
        mean_latency=float(np.mean(lat)) if lat.size else float("nan"),
        median_latency=float(np.median(lat)) if lat.size else float("nan"),
        detection_power=float(np.mean(powers)) if powers else float("nan"),
    )


def run_monte_carlo(cfg: SimConfig, n_reps: int = 1000, alpha: float = 0.05,
                    thetas=(1.25, 1.5, 2.0, 3.0), fixed_half: int = 7,
                    dispersion_shrink: float = 0.33):
    """Return {method: MethodOutcome} for the given scenario.

    Four monitors are compared: the Poisson mixture martingale, the
    overdispersion-robust NB mixture martingale (dispersion estimated per
    replication from the burn-in), the Shiryaev-Roberts e-detector (NB), and the
    naive repeatedly-peeked fixed-window test.
    """
    rng = np.random.default_rng(cfg.seed)
    mm_pois = MixtureMartingale(alpha=alpha, thetas=thetas, dispersion=None)
    fw = FixedWindowMonitor(alpha=alpha, half=fixed_half, test="poisson")

    is_null = cfg.change_point is None
    cp = cfg.change_point if not is_null else None
    methods = ("mixture_poisson", "mixture_nb", "shiryaev_roberts", "fixed_window")
    acc = {m: {"alarm": [], "lat": [], "pow": []} for m in methods}

    for _ in range(n_reps):
        x = simulate_series(cfg, rng)
        lam0 = estimate_lambda0(x, cfg.burn_in)
        r_hat = estimate_dispersion(x, cfg.burn_in, shrink=dispersion_shrink)
        # Poisson mixture
        res = mm_pois.run(x, lam0)
        _record(acc["mixture_poisson"], res.alarm, res.alarm_index, is_null, cp, cfg.burn_in)
        # NB-robust mixture (dispersion estimated from burn-in)
        res = MixtureMartingale(alpha=alpha, thetas=thetas, dispersion=r_hat).run(x, lam0)
        _record(acc["mixture_nb"], res.alarm, res.alarm_index, is_null, cp, cfg.burn_in)
        # Shiryaev-Roberts (NB-robust)
        res = ShiryaevRoberts(alpha=alpha, thetas=thetas, dispersion=r_hat).run(x, lam0)
        _record(acc["shiryaev_roberts"], res.alarm, res.alarm_index, is_null, cp, cfg.burn_in)
        # fixed window
        fres = fw.run(x)
        _record(acc["fixed_window"], fres.alarm, fres.alarm_index, is_null, cp, cfg.burn_in)

    return {name: _summarise(name, d["alarm"], d["lat"], d["pow"])
            for name, d in acc.items()}


def _record(store, alarm, idx, is_null, cp, burn_in):
    if is_null:
        # false alarm = any alarm after burn-in under the null
        fa = bool(alarm and idx is not None and idx >= burn_in)
        store["alarm"].append(fa)
        store["lat"].append(None)
    else:
        # valid detection = alarm at/after the true change point
        detected = bool(alarm and idx is not None and idx >= cp)
        store["pow"].append(detected)
        # also treat pre-change alarms as false alarms contaminating alarm_rate
        store["alarm"].append(bool(alarm and idx is not None and idx < cp))
        store["lat"].append((idx - cp) if detected else None)
