#!/usr/bin/env python3
"""Run the full ECHO analysis (all RQs) and generate figures + tables."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo.eval import experiments, figures


def main():
    results, profiles, series = experiments.main()
    figures.make_all(results)
    print("\n=== analysis complete ===")
    print("results/echo_results.json, results/figures/*, results/tables/*")


if __name__ == "__main__":
    main()
