#!/usr/bin/env python3
"""Run the ECHO seed-driven YouTube collection for both events.

Usage:
    YOUTUBE_API_KEY=... python scripts/run_collection.py [--event odyssey|worldcup]

The API key is read from the environment (or ``.env``); it is never stored in
the repository. Collection is idempotent: re-running upserts metadata and skips
comments already stored (INSERT OR IGNORE).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo.config import load_config, ensure_dirs, DATA_DIR
from echo.collect.storage import Store
from echo.collect.youtube_client import YouTubeClient, QuotaMeter
from echo.collect.collector import Collector

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("echo.run_collection")


def _load_env_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("YOUTUBE_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("YOUTUBE_API_KEY not set (env or .env)")
    return key


def discovery_bounds(window: dict) -> tuple[str, str]:
    """Wide discovery window: 6 months before anchor .. collect_end.

    Videos (trailers, previews) predate the comment-analysis window; we discover
    broadly and slice comments to the analysis window downstream.
    """
    anchor = dt.date.fromisoformat(window["anchor_date"])
    after = (anchor - dt.timedelta(days=182)).isoformat() + "T00:00:00Z"
    before = window["collect_end"] + "T23:59:59Z"
    return after, before


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", choices=["odyssey", "worldcup"], default=None)
    ap.add_argument("--db", default=str(DATA_DIR / "raw" / "echo.sqlite"))
    args = ap.parse_args()

    ensure_dirs()
    cfg = load_config()
    key = _load_env_key()

    meter = QuotaMeter(
        budget=int(cfg.settings["api"]["daily_quota_units"]),
        costs={k: int(v) for k, v in cfg.settings["api"].items()
               if k.startswith("cost_")},
    )
    client = YouTubeClient(key, meter)
    store = Store(args.db)
    collector = Collector(client, store, cfg.settings)

    events = [args.event] if args.event else cfg.events()
    for ev in events:
        win = cfg.window(ev)
        after, before = discovery_bounds(win)
        log.info("=== collecting %s (%s) ===", ev, win["label"])
        res = collector.run_event(ev, cfg.seeds[ev], after, before)
        log.info("RESULT %s: %d videos, %d channels, %d comments | quota=%s",
                 ev, res.n_videos, res.n_channels, res.n_comments, res.quota)

    log.info("final counts: %s", store.counts())
    log.info("final quota: %s", client.meter.report())
    store.close()


if __name__ == "__main__":
    main()
