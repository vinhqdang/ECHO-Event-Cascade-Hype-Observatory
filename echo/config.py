"""Configuration loading and run-wide constants for ECHO.

A single entry point (:func:`load_config`) reads ``config/settings.yaml`` and
``config/seeds.yaml`` so that every stage of the pipeline shares one, version
controlled definition of the sampling frame and analysis parameters. This is
what makes the reported methods reproducible from the repository alone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"


@dataclass
class Config:
    settings: dict[str, Any]
    seeds: dict[str, Any]

    # convenience accessors ------------------------------------------------
    @property
    def seed(self) -> int:
        return int(self.settings.get("seed", 0))

    def window(self, event: str) -> dict[str, Any]:
        return self.settings["windows"][event]

    def events(self) -> list[str]:
        return list(self.settings["windows"].keys())


def load_config(settings_path: str | os.PathLike | None = None,
                seeds_path: str | os.PathLike | None = None) -> Config:
    settings_path = Path(settings_path) if settings_path else CONFIG_DIR / "settings.yaml"
    seeds_path = Path(seeds_path) if seeds_path else CONFIG_DIR / "seeds.yaml"
    with open(settings_path) as fh:
        settings = yaml.safe_load(fh)
    with open(seeds_path) as fh:
        seeds = yaml.safe_load(fh)
    return Config(settings=settings, seeds=seeds)


def ensure_dirs() -> None:
    for sub in ("raw", "processed"):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("figures", "tables"):
        (RESULTS_DIR / sub).mkdir(parents=True, exist_ok=True)
