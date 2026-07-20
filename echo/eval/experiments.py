"""End-to-end analysis: runs all research questions and writes results.

Outputs (under ``results/``):
* ``echo_results.json`` — machine-readable summary of every RQ;
* ``tables/*.csv`` — publication tables;
* ``figures/*.png`` — publication figures.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from echo.config import load_config, ensure_dirs, DATA_DIR, RESULTS_DIR
from echo.build import network as net
from echo.embed import structure as emb
from echo.detect.eprocess import (MixtureMartingale, ShiryaevRoberts,
                                  estimate_lambda0, estimate_dispersion)
from echo.detect.fixedwindow import FixedWindowMonitor
from echo.causal.psm import PropensityMatcher
from echo.forecast.conformal import split_conformal, adaptive_conformal
from echo.eval.cascades import video_covariates_outcomes, add_days_from_anchor
from echo.eval.simulate import SimConfig, run_monte_carlo

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("echo.experiments")


class NumpyEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def _db(cfg, db_path):
    return db_path or str(DATA_DIR / "raw" / "echo.sqlite")


def _anchor_index(dates, anchor_date):
    """Index of the snapshot on/after the event anchor date."""
    anchor = pd.Timestamp(anchor_date, tz="UTC")
    for i, d in enumerate(dates):
        if pd.Timestamp(d) >= anchor:
            return i
    return None


# ---------------------------------------------------------------------------
def rq1_structure(cfg, db_path):
    """Structural comparison of the two event networks."""
    profiles = {}
    graphs = {}
    nconf = cfg.settings["network"]
    for ev in cfg.events():
        win = cfg.window(ev)
        df = net.load_comments(db_path, ev)
        df = net.slice_window(df, win["collect_start"], win["collect_end"])
        G = net.build_cocomment_graph(
            df, window_hours=nconf["cocomment_window_hours"],
            min_shared_videos=nconf["min_shared_videos"],
            min_degree=nconf["min_node_degree"])
        prof = emb.profile_graph(G, seed=cfg.seed)
        profiles[ev] = prof
        graphs[ev] = G
        log.info("[RQ1] %s: %s", ev, prof.as_row())
    # embeddings + distances
    events = cfg.events()
    embs = {ev: emb.gcn_embedding(graphs[ev], seed=cfg.seed) for ev in events}
    result = {
        "profiles": {ev: profiles[ev].as_row() for ev in events},
        "spectral_distance": emb.spectral_distance(profiles[events[0]],
                                                   profiles[events[1]]),
        "embedding_distance": float(np.linalg.norm(
            embs[events[0]] - embs[events[1]])),
    }
    return result, profiles


# ---------------------------------------------------------------------------
def rq2_detection(cfg, db_path):
    """Sequential detection on real velocity + Monte-Carlo error control."""
    dconf = cfg.settings["detection"]
    alpha = dconf["alpha"]
    thetas = tuple(dconf["bet_grid"])
    burn = dconf["burn_in_snapshots"]
    real = {}
    lam0s = {}
    series_store = {}
    for ev in cfg.events():
        win = cfg.window(ev)
        df = net.load_comments(db_path, ev)
        df = net.slice_window(df, win["collect_start"], win["collect_end"])
        gs = net.build_graph_series(
            df, granularity=cfg.settings["network"]["snapshot_granularity"],
            window_hours=cfg.settings["network"]["cocomment_window_hours"],
            seed=cfg.seed)
        counts = gs.comment_rate
        series_store[ev] = gs
        lam0 = estimate_lambda0(counts, burn)
        shrink = dconf.get("dispersion_shrink", 0.33)
        r_hat = estimate_dispersion(counts, burn, shrink=shrink)
        lam0s[ev] = lam0
        mm_p = MixtureMartingale(alpha, thetas, dispersion=None).run(counts, lam0)
        mm_nb = MixtureMartingale(alpha, thetas, dispersion=r_hat).run(counts, lam0)
        sr = ShiryaevRoberts(alpha, thetas, dispersion=r_hat).run(counts, lam0)
        fw = FixedWindowMonitor(alpha, dconf["fixed_window_half"], "poisson").run(counts)
        anchor_day = _anchor_index(gs.dates, win["anchor_date"])
        real[ev] = {
            "n_days": len(counts), "lambda0": lam0, "dispersion_r": r_hat,
            "peak_rate": float(np.max(counts)),
            "anchor_day_index": anchor_day,
            "mixture_poisson_alarm_day": mm_p.alarm_index,
            "mixture_nb_alarm_day": mm_nb.alarm_index,
            "shiryaev_alarm_day": sr.alarm_index,
            "fixed_window_alarm_day": fw.alarm_index,
            "dates": [str(d.date()) for d in gs.dates],
            "counts": counts.tolist(),
            "new_nodes": gs.new_nodes.tolist(),
            "modularity": gs.modularity.tolist(),
        }
        log.info("[RQ2] %s: lam0=%.1f r=%.1f poisson@%s nb@%s sr@%s fixed@%s anchor@%s",
                 ev, lam0, r_hat, mm_p.alarm_index, mm_nb.alarm_index,
                 sr.alarm_index, fw.alarm_index, anchor_day)

    # Monte-Carlo validation calibrated to observed baseline rate
    base_lambda = float(np.mean(list(lam0s.values())))
    mc = {"lambda0": base_lambda, "n_reps": 2000, "alpha": alpha,
          "scenarios": {}}
    scenarios = {
        "null_poisson": SimConfig(change_point=None, lambda0=base_lambda,
                                  overdispersion=0.0, seed=101),
        "null_overdispersed": SimConfig(change_point=None, lambda0=base_lambda,
                                        overdispersion=0.3, weekly_amp=0.2, seed=102),
        "alt_1p5x": SimConfig(change_point=21, effect=1.5, lambda0=base_lambda, seed=103),
        "alt_2p5x": SimConfig(change_point=21, effect=2.5, lambda0=base_lambda, seed=104),
        "alt_overdispersed_2x": SimConfig(change_point=21, effect=2.0,
                                          lambda0=base_lambda, overdispersion=0.3,
                                          weekly_amp=0.2, seed=105),
    }
    for name, sc in scenarios.items():
        out = run_monte_carlo(sc, n_reps=mc["n_reps"], alpha=alpha,
                              thetas=thetas, fixed_half=dconf["fixed_window_half"],
                              dispersion_shrink=dconf.get("dispersion_shrink", 0.33))
        mc["scenarios"][name] = {m: o.__dict__ for m, o in out.items()}
        log.info("[RQ2-MC] %s: %s", name,
                 {m: round(o.alarm_rate, 3) for m, o in out.items()})
    return {"real": real, "monte_carlo": mc}, series_store


# ---------------------------------------------------------------------------
def rq3_causal(cfg, db_path):
    """Format-innovation effect on diffusion via PSM (Odyssey only)."""
    cconf = cfg.settings["causal"]
    ev = "odyssey"
    win = cfg.window(ev)
    df = video_covariates_outcomes(db_path, ev)
    df = add_days_from_anchor(df, win["anchor_date"])
    n_treat = int(df["is_innovation"].sum())
    log.info("[RQ3] %d videos, %d innovation-framed", len(df), n_treat)
    matcher = PropensityMatcher(cconf["covariates"], cconf["caliper_sd"], cfg.seed)
    results = {}
    # NB: reply-chain depth on YouTube is structurally capped at 2 (flat reply
    # model), so it carries no variation and is excluded as an outcome.
    for outcome in ("breadth", "volume", "reply_ratio"):
        res = matcher.estimate(df, "is_innovation", outcome)
        results[outcome] = res.as_row()
        log.info("[RQ3] %s ATT=%.3f CI=[%.3f,%.3f] (naive %.3f, matched n=%d, maxSMD=%.3f)",
                 outcome, res.att, res.ci_low, res.ci_high, res.naive_diff,
                 res.n_matched, res.as_row()["max_smd_after"])
    return {"n_videos": len(df), "n_innovation": n_treat, "effects": results,
            "innovation_share": n_treat / max(len(df), 1)}


# ---------------------------------------------------------------------------
def rq_robustness(cfg, series_store):
    """Conformal coverage of diffusion forecasts."""
    fconf = cfg.settings["forecast"]
    out = {}
    for ev, gs in series_store.items():
        sc = split_conformal(gs.comment_rate, alpha=fconf["conformal_alpha"])
        ac = adaptive_conformal(gs.comment_rate, alpha=fconf["conformal_alpha"],
                                train_frac=0.25, gamma=0.1)
        out[ev] = {"target": 1 - fconf["conformal_alpha"],
                   "split_coverage": sc.coverage, "split_width": sc.mean_width,
                   "adaptive_coverage": ac.coverage, "adaptive_width": ac.mean_width}
        log.info("[ROB] %s split cov=%.3f (w=%.0f) | adaptive cov=%.3f (w=%.0f) target=%.2f",
                 ev, sc.coverage, sc.mean_width, ac.coverage, ac.mean_width,
                 1 - fconf["conformal_alpha"])
    return out


# ---------------------------------------------------------------------------
def main(db_path: str | None = None):
    ensure_dirs()
    cfg = load_config()
    db_path = _db(cfg, db_path)
    log.info("using db=%s", db_path)

    r1, profiles = rq1_structure(cfg, db_path)
    r2, series_store = rq2_detection(cfg, db_path)
    r3 = rq3_causal(cfg, db_path)
    rob = rq_robustness(cfg, series_store)

    results = {"rq1_structure": r1, "rq2_detection": r2,
               "rq3_causal": r3, "robustness": rob,
               "config": {"windows": cfg.settings["windows"],
                          "seed": cfg.seed}}
    out_path = RESULTS_DIR / "echo_results.json"
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2, cls=NumpyEncoder)
    log.info("wrote %s", out_path)
    return results, profiles, series_store


if __name__ == "__main__":
    main()
