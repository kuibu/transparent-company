#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish one disclosure and run verification script")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--policy-id", default="policy_public_v1")
    parser.add_argument("--period", required=True, help="start/end in ISO")
    parser.add_argument("--group-by", default="channel")
    parser.add_argument("--metric-key", default="revenue_cents")
    args = parser.parse_args()

    publish_resp = requests.post(
        f"{args.base_url}/disclosure/publish",
        json={
            "policy_id": args.policy_id,
            "period": args.period,
            "group_by": [g.strip() for g in args.group_by.split(",") if g.strip()],
        },
        timeout=60,
    )
    publish_resp.raise_for_status()
    payload = publish_resp.json()

    print("Published disclosure:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    verify_public_key = payload.get("signer_public_key") or payload.get("agent_public_key")
    cmd = [
        sys.executable,
        "scripts/verify_disclosure.py",
        "--base-url",
        args.base_url,
        "--disclosure-id",
        payload["disclosure_id"],
        "--metric-key",
        args.metric_key,
    ]
    if verify_public_key:
        cmd.extend(["--public-key", verify_public_key])

    if payload.get("grouped_metrics"):
        first_group = payload["grouped_metrics"][0].get("group", {})
        if first_group:
            cmd.extend(["--group", json.dumps(first_group, ensure_ascii=False)])

    print("\nRunning verification command:")
    print(" ".join(cmd))
    subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
