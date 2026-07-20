"""Publication figures and tables from the results JSON + in-memory objects.

Figures are saved as .tif (Emerald-acceptable) at 300 dpi; tables as .csv for
transfer into the separate tables document.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from echo.config import RESULTS_DIR

FIG = RESULTS_DIR / "figures"
TAB = RESULTS_DIR / "tables"
plt.rcParams.update({"font.size": 10, "figure.dpi": 300,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "font.family": "DejaVu Sans"})
EVENT_LABEL = {"odyssey": "The Odyssey", "worldcup": "World Cup 2026"}
C = {"odyssey": "#2A6F97", "worldcup": "#BC4749"}


def _save(fig, name):
    fig.savefig(FIG / f"{name}.png", bbox_inches="tight", dpi=300)
    # LZW-compressed TIFF at 300 dpi (Emerald-acceptable, lossless, compact)
    fig.savefig(FIG / f"{name}.tif", bbox_inches="tight", dpi=300,
                pil_kwargs={"compression": "tiff_lzw"})
    plt.close(fig)


# --- Figure 1: engagement velocity + detection markers ---------------------
def fig_velocity(results):
    real = results["rq2_detection"]["real"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), sharey=False)
    for ax, ev in zip(axes, real):
        d = real[ev]
        dates = pd.to_datetime(d["dates"])
        counts = np.array(d["counts"])
        ax.plot(dates, counts, color=C[ev], lw=1.8)
        ax.fill_between(dates, counts, color=C[ev], alpha=0.12)
        markers = (("mixture_nb_alarm_day", "-", "k", "e-process (NB)"),
                   ("fixed_window_alarm_day", "--", "grey", "fixed-window"),
                   ("anchor_day_index", ":", "#888", "event anchor"))
        for key, style, col, lab in markers:
            idx = d.get(key)
            if idx is not None and idx < len(dates):
                ax.axvline(dates[idx], color=col, ls=style, lw=1.2, label=lab)
        ax.set_title(EVENT_LABEL[ev]); ax.set_ylabel("comments / day")
        ax.tick_params(axis="x", rotation=45, labelsize=7)
        ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    _save(fig, "fig1_velocity")


# --- Figure 2: Monte-Carlo error control -----------------------------------
def fig_error_control(results):
    mc = results["rq2_detection"]["monte_carlo"]["scenarios"]
    methods = ["mixture_poisson", "mixture_nb", "shiryaev_roberts", "fixed_window"]
    labels = {"mixture_poisson": "e-process (Poisson)",
              "mixture_nb": "e-process (NB-robust)",
              "shiryaev_roberts": "e-detector (SR)",
              "fixed_window": "fixed-window"}
    null_scen = [s for s in mc if s.startswith("null")]
    alt_scen = [s for s in mc if s.startswith("alt")]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8))
    w = 0.2
    # panel A: false alarm under nulls
    x = np.arange(len(null_scen))
    for i, m in enumerate(methods):
        vals = [mc[s][m]["alarm_rate"] for s in null_scen]
        axes[0].bar(x + i * w, vals, w, label=labels[m])
    axes[0].axhline(0.05, color="k", ls=":", lw=1, label="nominal 0.05")
    axes[0].set_xticks(x + 1.5 * w); axes[0].set_xticklabels(
        [s.replace("null_", "") for s in null_scen], fontsize=8)
    axes[0].set_ylabel("false-alarm probability")
    axes[0].set_title("(a) Type-I error under continuous monitoring")
    axes[0].legend(fontsize=6.5, frameon=False)
    # panel B: power under alternatives
    x = np.arange(len(alt_scen))
    for i, m in enumerate(methods):
        vals = [mc[s][m]["detection_power"] for s in alt_scen]
        axes[1].bar(x + i * w, vals, w, label=labels[m])
    axes[1].set_xticks(x + 1.5 * w); axes[1].set_xticklabels(
        [s.replace("alt_", "") for s in alt_scen], fontsize=8)
    axes[1].set_ylabel("detection power"); axes[1].set_ylim(0, 1.05)
    axes[1].set_title("(b) Power under genuine shifts")
    axes[1].legend(fontsize=7, frameon=False)
    fig.tight_layout()
    _save(fig, "fig2_error_control")


# --- Figure 3: structural profiles radar-ish bars --------------------------
def fig_structure(results):
    prof = results["rq1_structure"]["profiles"]
    metrics = ["density", "modularity", "assortativity", "avg_clustering",
               "core_periphery", "bridge_prevalence", "largest_cc_frac"]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    x = np.arange(len(metrics)); w = 0.38
    for i, ev in enumerate(prof):
        vals = [prof[ev][m] for m in metrics]
        ax.bar(x + i * w, vals, w, label=EVENT_LABEL[ev], color=C[ev])
    ax.set_xticks(x + w / 2)
    ax.set_xticklabels(metrics, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("value"); ax.set_title("Network structural comparison")
    ax.legend(frameon=False)
    fig.tight_layout()
    _save(fig, "fig3_structure")


# --- Figure 4: conformal coverage / PSM effects ----------------------------
def fig_causal_conformal(results):
    eff = results["rq3_causal"]["effects"]
    rob = results["robustness"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    outs = list(eff.keys())
    att = [eff[o]["att"] for o in outs]
    lo = [eff[o]["att"] - eff[o]["ci_low"] for o in outs]
    hi = [eff[o]["ci_high"] - eff[o]["att"] for o in outs]
    axes[0].bar(outs, att, color="#5A8F69",
                yerr=[np.abs(lo), np.abs(hi)], capsize=4)
    axes[0].axhline(0, color="k", lw=0.8)
    axes[0].set_title("(a) Format-innovation ATT (PSM)")
    axes[0].set_ylabel("ATT (treated - matched control)")
    axes[0].tick_params(axis="x", rotation=20)
    evs = list(rob.keys())
    x = np.arange(len(evs)); w = 0.35
    axes[1].bar(x, [rob[e]["split_coverage"] for e in evs], w,
                label="split", color="#9AA5B1")
    axes[1].bar(x + w, [rob[e]["adaptive_coverage"] for e in evs], w,
                label="adaptive (ACI)", color="#3D5A80")
    axes[1].axhline(rob[evs[0]]["target"], color="k", ls=":", label="target 0.90")
    axes[1].set_xticks(x + w / 2); axes[1].set_xticklabels([EVENT_LABEL[e] for e in evs])
    axes[1].set_ylim(0, 1.05); axes[1].set_title("(b) Conformal coverage")
    axes[1].set_ylabel("empirical coverage"); axes[1].legend(fontsize=8, frameon=False)
    fig.tight_layout()
    _save(fig, "fig4_causal_conformal")


# --- Tables ----------------------------------------------------------------
def tables(results):
    # Table I: structural profiles
    prof = results["rq1_structure"]["profiles"]
    pd.DataFrame(prof).T.to_csv(TAB / "table1_structure.csv")
    # Table II: MC error control
    mc = results["rq2_detection"]["monte_carlo"]["scenarios"]
    rows = []
    for scen, methods in mc.items():
        for m, o in methods.items():
            rows.append({"scenario": scen, "method": m,
                         "alarm_rate": o["alarm_rate"],
                         "detection_power": o["detection_power"],
                         "mean_latency": o["mean_latency"],
                         "median_latency": o["median_latency"]})
    pd.DataFrame(rows).to_csv(TAB / "table2_detection.csv", index=False)
    # Table III: PSM effects
    eff = results["rq3_causal"]["effects"]
    pd.DataFrame(eff).T.to_csv(TAB / "table3_psm.csv")


def make_all(results):
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    fig_velocity(results)
    fig_error_control(results)
    fig_structure(results)
    fig_causal_conformal(results)
    tables(results)
