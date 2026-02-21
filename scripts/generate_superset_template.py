#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.demo.default_scenario import _build_superset_template


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Superset dashboard import template for default story")
    parser.add_argument(
        "--output",
        default="app/demo/exports/david_transparent_supermarket_superset_dashboard_template.json",
        help="Output JSON file path",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_build_superset_template(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.as_posix())


if __name__ == "__main__":
    main()
