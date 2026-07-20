"""Fixed-window baseline detectors (the naive practice ECHO argues against).

Analysts commonly monitor a live series by, at each new day, comparing a recent
window against a preceding baseline window with a two-sample test and reacting
the first time ``p < alpha``. Under continuous monitoring this "peeking" inflates
the false-alarm probability far above the nominal ``alpha`` because the test is
repeated at every time step without correction. :class:`FixedWindowMonitor`
reproduces exactly this procedure so its realised error can be contrasted with
the anytime-valid e-process (RQ2).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass
class FixedWindowResult:
    alarm: bool
    alarm_index: int | None
    pvalues: np.ndarray


@dataclass
class FixedWindowMonitor:
    alpha: float = 0.05
    half: int = 7                      # window size (days) on each side
    test: str = "poisson"             # "poisson" (E-test approx) or "t"

    def _pval(self, base: np.ndarray, recent: np.ndarray) -> float:
        if self.test == "t":
            if base.size < 2 or recent.size < 2:
                return 1.0
            _, p = stats.ttest_ind(recent, base, equal_var=False,
                                   alternative="greater")
            return float(p) if np.isfinite(p) else 1.0
        # Poisson rate comparison via conditional binomial test (one-sided: rate up)
        c1, n1 = float(recent.sum()), recent.size
        c2, n2 = float(base.sum()), base.size
        if c1 + c2 == 0 or n1 == 0 or n2 == 0:
            return 1.0
        p_null = n1 / (n1 + n2)
        # P(Binom(c1+c2, p_null) >= c1)
        return float(stats.binom.sf(c1 - 1, int(c1 + c2), p_null))

    def run(self, counts: np.ndarray) -> FixedWindowResult:
        counts = np.asarray(counts, float)
        T = len(counts)
        pvals = np.ones(T)
        alarm_idx = None
        for t in range(self.half, T):
            base = counts[max(0, t - 2 * self.half):t - self.half]
            recent = counts[t - self.half:t]
            if base.size == 0 or recent.size == 0:
                continue
            p = self._pval(base, recent)
            pvals[t] = p
            if alarm_idx is None and p < self.alpha:
                alarm_idx = t
        return FixedWindowResult(alarm_idx is not None, alarm_idx, pvals)
