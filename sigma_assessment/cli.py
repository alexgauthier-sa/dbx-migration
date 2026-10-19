"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .client import SigmaClient
from .collector import Collector, write_assessment


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Assess Sigma workbook/data-model relationships and export CSV reports.")
    p.add_argument("--output-dir", type=Path, default=Path("output"), help="CSV destination (default: output)")
    p.add_argument("--base-url", default=os.getenv("SIGMA_BASE_URL", "https://api.sigmacomputing.com"))
    p.add_argument("--workers", type=int, default=8, help="Concurrent document requests (default: 8)")
    p.add_argument("--timeout", type=float, default=30, help="HTTP timeout in seconds")
    p.add_argument("--no-lineage", action="store_true", help="Skip expensive per-element lineage calls")
    p.add_argument("--all-org-documents", action="store_true", help="Use admin-only skipPermissionCheck")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    client = SigmaClient(
        base_url=args.base_url,
        client_id=os.getenv("SIGMA_CLIENT_ID"),
        client_secret=os.getenv("SIGMA_CLIENT_SECRET"),
        access_token=os.getenv("SIGMA_ACCESS_TOKEN"),
        timeout=args.timeout,
    )
    result = Collector(client, workers=args.workers, skip_permission_check=args.all_org_documents).collect(include_lineage=not args.no_lineage)
    write_assessment(result, args.output_dir)
    logging.info(
        "Wrote assessment to %s: %d workbooks, %d data models, %d relations, %d dependency edges, %d errors",
        args.output_dir, len(result.workbooks), len(result.data_models), len(result.relations), len(result.dependencies), len(result.errors),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

