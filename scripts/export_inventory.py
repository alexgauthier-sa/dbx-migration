#!/usr/bin/env python3
"""Fast inventory/source report without per-element lineage calls."""
from sigma_assessment.cli import main

raise SystemExit(main(["--no-lineage", *__import__("sys").argv[1:]]))

