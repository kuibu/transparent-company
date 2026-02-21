#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed default story and export demo assets")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    seeded = requests.post(f"{args.base_url}/demo/seed", timeout=120)
    seeded.raise_for_status()
    payload = seeded.json()

    print(json.dumps({
        "scenario_id": payload.get("scenario_id"),
        "seeded_now": payload.get("seeded_now"),
        "data_exports": payload.get("data_exports", {}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
