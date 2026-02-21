from __future__ import annotations

import json
from pathlib import Path


def test_demo_exports_written(client):
    resp = client.post("/demo/seed")
    assert resp.status_code == 200
    payload = resp.json()
    exports = payload["data_exports"]

    repo_root = Path(__file__).resolve().parents[1]
    events_json = repo_root / exports["events_json"]
    events_csv = repo_root / exports["events_csv"]
    bank_csv = repo_root / exports["bank_transactions_csv"]
    template_json = repo_root / exports["superset_template_json"]

    assert events_json.exists()
    assert events_csv.exists()
    assert bank_csv.exists()
    assert template_json.exists()

    event_rows = json.loads(events_json.read_text(encoding="utf-8"))
    assert any(row["event_type"] == "CustomerConflictReported" for row in event_rows)
    assert any(row["event_type"] == "CompanyCompensationIssued" for row in event_rows)

    template = json.loads(template_json.read_text(encoding="utf-8"))
    chart_names = [item["name"] for item in template.get("charts", [])]
    assert "Daily Revenue Trend (CNY)" in chart_names
    assert "Supplier Payment Term Structure (CNY)" in chart_names
