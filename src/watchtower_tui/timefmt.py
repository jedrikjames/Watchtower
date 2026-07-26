"""Short human time strings. Deliberately terse - these go in a card, not a report."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compact_duration(seconds: float) -> str:
    """90 -> '1m', 5400 -> '1h 30m', 172800 -> '2d'."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, _seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def ago(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "never"
    now = now or utcnow()
    delta = (now - value).total_seconds()
    if delta < 5:
        return "just now"
    return f"{compact_duration(delta)} ago"


def until(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return ""
    now = now or utcnow()
    delta = (value - now).total_seconds()
    if delta <= 0:
        return "now"
    return compact_duration(delta)
