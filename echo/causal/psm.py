"""Propensity-score matching for the format-innovation effect (RQ3).

Question: controlling for channel size, upload timing and reach, does framing a
video around the format-innovation narrative (IMAX/70mm; ``is_innovation``)
causally amplify cross-channel diffusion — operationalised as comment-cascade
breadth (distinct commenters), depth (max reply-chain length) and volume?

Estimator: logistic propensity model on the covariates, 1:1 nearest-neighbour
matching on the logit propensity within a caliper, then the average treatment
effect on the treated (ATT) with a paired bootstrap CI. Covariate balance is
reported as standardised mean differences before and after matching.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


def standardised_mean_diff(t: np.ndarray, c: np.ndarray) -> float:
    pooled = np.sqrt((t.var(ddof=1) + c.var(ddof=1)) / 2) + 1e-12
    return float((t.mean() - c.mean()) / pooled)


@dataclass
class PSMResult:
    outcome: str
    att: float
    ci_low: float
    ci_high: float
    n_treated: int
    n_matched: int
    naive_diff: float
    balance_before: dict
    balance_after: dict

    def as_row(self) -> dict:
        return {"outcome": self.outcome, "att": self.att,
                "ci_low": self.ci_low, "ci_high": self.ci_high,
                "n_treated": self.n_treated, "n_matched": self.n_matched,
                "naive_diff": self.naive_diff,
                "max_smd_after": max(abs(v) for v in self.balance_after.values())
                if self.balance_after else float("nan")}


class PropensityMatcher:
    def __init__(self, covariates: list[str], caliper_sd: float = 0.2,
                 seed: int = 0):
        self.covariates = covariates
        self.caliper_sd = caliper_sd
        self.rng = np.random.default_rng(seed)

    def _propensity(self, df: pd.DataFrame, treat: str):
        X = StandardScaler().fit_transform(df[self.covariates].values)
        y = df[treat].values.astype(int)
        model = LogisticRegression(max_iter=1000, C=1.0)
        model.fit(X, y)
        p = model.predict_proba(X)[:, 1]
        logit = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1 - 1e-6))
        return logit

    def match(self, df: pd.DataFrame, treat: str):
        df = df.dropna(subset=self.covariates + [treat]).reset_index(drop=True)
        logit = self._propensity(df, treat)
        caliper = self.caliper_sd * np.std(logit)
        t_idx = np.where(df[treat].values == 1)[0]
        c_idx = np.where(df[treat].values == 0)[0]
        pairs = []
        used = set()
        for i in t_idx:
            cands = [j for j in c_idx if j not in used]
            if not cands:
                break
            d = np.abs(logit[cands] - logit[i])
            j = cands[int(np.argmin(d))]
            if d.min() <= caliper or not np.isfinite(caliper):
                pairs.append((i, j)); used.add(j)
        return df, pairs

    def estimate(self, df: pd.DataFrame, treat: str, outcome: str,
                 n_boot: int = 2000) -> PSMResult:
        df, pairs = self.match(df, treat)
        t = df.loc[df[treat] == 1, outcome].values
        c = df.loc[df[treat] == 0, outcome].values
        naive = float(np.mean(t) - np.mean(c)) if len(t) and len(c) else float("nan")
        bal_before = {cov: standardised_mean_diff(
            df.loc[df[treat] == 1, cov].values,
            df.loc[df[treat] == 0, cov].values) for cov in self.covariates}
        if not pairs:
            return PSMResult(outcome, float("nan"), float("nan"), float("nan"),
                             int((df[treat] == 1).sum()), 0, naive,
                             bal_before, {})
        ti = [p[0] for p in pairs]; ci = [p[1] for p in pairs]
        diffs = df.loc[ti, outcome].values - df.loc[ci, outcome].values
        att = float(np.mean(diffs))
        # paired bootstrap CI
        boots = np.array([np.mean(self.rng.choice(diffs, len(diffs), replace=True))
                          for _ in range(n_boot)])
        lo, hi = np.percentile(boots, [2.5, 97.5])
        bal_after = {cov: standardised_mean_diff(
            df.loc[ti, cov].values, df.loc[ci, cov].values)
            for cov in self.covariates}
        return PSMResult(outcome, att, float(lo), float(hi),
                         len(ti), len(pairs), naive, bal_before, bal_after)
