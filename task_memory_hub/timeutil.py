from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re


UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def iso_now() -> str:
    return to_iso(now_utc())


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return to_iso(parsed)


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)


def parse_duration(value: str) -> timedelta:
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError("Duration must look like 15m, 2h, 3d, or 1w.")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def duration_until_iso(value: str) -> str:
    return to_iso(now_utc() + parse_duration(value))


def seconds_until_iso(seconds: int) -> str:
    return to_iso(now_utc() + timedelta(seconds=seconds))
