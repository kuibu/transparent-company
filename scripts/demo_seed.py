#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Transparent Company demo scenario")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    resp = requests.post(f"{args.base_url}/demo/seed", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
