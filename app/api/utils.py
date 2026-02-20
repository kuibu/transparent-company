from __future__ import annotations

from datetime import datetime, timezone


def parse_period(period: str) -> tuple[datetime, datetime]:
    if "/" not in period:
        raise ValueError("period must be start/end")
    start_text, end_text = period.split("/", 1)
    start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    if not end > start:
        raise ValueError("period end must be greater than start")
    return start, end


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
