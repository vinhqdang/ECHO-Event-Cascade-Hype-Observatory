# ECHO — Event Cascade & Hype Observatory

ECHO is a reproducible pipeline for studying how collective attention around a
technology-mediated cultural event diffuses and reorganises itself across
YouTube's commenter network, and for detecting **when** that attention
accelerates using **anytime-valid** sequential methods rather than post-hoc
fixed-window comparisons.

It accompanies the manuscript *"When audiences turn: how publics collectively
mobilise around technology-mediated events, and detecting the turn as it
happens"* (prepared for *Internet Research*).

## What it does

Two events are contrasted over a six-week window each:

* **The Odyssey** (Christopher Nolan; IMAX/70mm format innovation) — a
  technology-mediated cultural event (theatrical release 17 July 2026).
* **FIFA World Cup 2026** — a non-technology-anchored sporting mega-event
  (opening 11 June 2026).

The pipeline answers three research questions:

| RQ | Question | Method | Module |
|----|----------|--------|--------|
| RQ1 | Does network structure differ between a film-release and a sporting mega-event? | Co-comment network + community detection + spectral / graph-conv embeddings | `echo/embed/structure.py` |
| RQ2 | Can a sequential e-process detect hype acceleration faster and with valid Type-I control where fixed-window tests cannot? | Mixture test martingale (Poisson & overdispersion-robust NB) + Shiryaev–Roberts e-detector vs. repeatedly-peeked fixed-window | `echo/detect/` |
| RQ3 | Does the format-innovation narrative causally amplify diffusion? | Propensity-score matching on channel/video covariates | `echo/causal/psm.py` |
| Robustness | Are diffusion forecasts calibrated? | Split & adaptive conformal prediction | `echo/forecast/conformal.py` |

## Reproducing

```bash
pip install -r requirements.txt

# 1. Collect (needs a YouTube Data API v3 key; never committed)
export YOUTUBE_API_KEY=...          # or put YOUTUBE_API_KEY=... in .env
python scripts/run_collection.py    # seed-driven, quota-aware

# 2. Analyse (all RQs) + figures + tables
python scripts/run_analysis.py
```

Outputs land in `results/` (`echo_results.json`, `figures/`, `tables/`).
All parameters that define the sampling frame and the analysis live in
`config/settings.yaml` and `config/seeds.yaml`.

## Data & ethics

Commenter identities are **anonymised at write time** with a salted BLAKE2b
digest (salt is ephemeral and never stored), so the on-disk dataset contains only
anonymised network nodes. No verbatim comments are stored or reported. Collection
is metered against the documented per-endpoint quota and stops explicitly rather
than truncating silently. See the manuscript's data statement for full details.

## Layout

```
echo/
  collect/    YouTube Data API client, quota meter, anonymising storage, collector
  build/      temporal network construction + graph-level time series
  detect/     anytime-valid e-process detectors + fixed-window baseline
  embed/      structural profiling + spectral / graph-conv embeddings
  causal/     propensity-score matching
  forecast/   split & adaptive conformal prediction
  eval/       simulator, Monte-Carlo validation, cascades, experiments, figures
config/       settings.yaml, seeds.yaml
scripts/      run_collection.py, run_analysis.py
manuscript/   Emerald manuscript (DOCX) + tables + figures
tests/        unit tests for the statistical core
```
