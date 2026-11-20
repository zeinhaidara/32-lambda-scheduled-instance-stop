"""Parse schedule tags and decide whether a resource should be stopped now."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEDULE_TAG = "auto-stop-schedule"
OVERRIDE_TAG = "auto-stop-override-until"
ENV_TAG = "environment"
PROTECTED_ENVS = {"prod", "production"}

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_SCHEDULE_RE = re.compile(
    r"^(?P<days>[a-z,\-]+)\s+(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})\s+(?P<tz>\S+)$"
)


class TagError(ValueError):
    """A tag value we refuse to guess about."""


@dataclass(frozen=True)
class Schedule:
    """Business hours: the resource should be running inside this window."""

    days: frozenset[int]  # 0 = Monday
    start: time
    end: time
    tz: ZoneInfo

    def is_business_hours(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ValueError("now must be timezone aware")
        # Compare wall-clock time in the resource's own zone, so DST is handled.
        local = now.astimezone(self.tz)
        return local.weekday() in self.days and self.start <= local.time() < self.end


def parse_schedule(value: str) -> Schedule:
    """Parse e.g. "mon-fri 08:00-18:00 Europe/London"."""
    m = _SCHEDULE_RE.match(value.strip().lower() if value else "")
    if not m:
        raise TagError(f"bad schedule {value!r}, expected like 'mon-fri 08:00-18:00 Europe/London'")
    days = _parse_days(m["days"])
    start, end = _parse_time(m["start"]), _parse_time(m["end"])
    if start >= end:
        raise TagError(f"schedule window must start before it ends: {value!r}")
    # Zone names are case-sensitive, so take it from the original string.
    tz_name = value.split()[-1]
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TagError(f"unknown timezone {tz_name!r}") from exc
    return Schedule(days=days, start=start, end=end, tz=tz)


def _parse_days(text: str) -> frozenset[int]:
    out: set[int] = set()
    for part in text.split(","):
        if "-" in part:
            first, _, last = part.partition("-")
            a, b = _day(first), _day(last)
            if a > b:
                raise TagError(f"day range runs backwards: {part!r}")
            out.update(range(a, b + 1))
        else:
            out.add(_day(part))
    return frozenset(out)


def _day(name: str) -> int:
    if name not in DAYS:
        raise TagError(f"unknown day {name!r}")
    return DAYS.index(name)


def _parse_time(text: str) -> time:
    hours, minutes = (int(x) for x in text.split(":"))
    if hours > 23 or minutes > 59:
        raise TagError(f"bad time {text!r}")
    return time(hours, minutes)


def parse_override(value: str) -> datetime:
    """Override expiry must carry an offset; a naive time is ambiguous."""
    try:
        until = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise TagError(f"bad override timestamp {value!r}") from exc
    if until.tzinfo is None:
        raise TagError(f"override timestamp needs a UTC offset: {value!r}")
    return until


@dataclass(frozen=True)
class Decision:
    stop: bool
    reason: str


def decide(tags: dict[str, str], now: datetime) -> Decision:
    """Pure decision for one resource. Raises TagError on tags it can't trust."""
    if SCHEDULE_TAG not in tags:
        return Decision(False, "not opted in")
    if tags.get(ENV_TAG, "").strip().lower() in PROTECTED_ENVS:
        return Decision(False, "production resource")

    schedule = parse_schedule(tags[SCHEDULE_TAG])

    if OVERRIDE_TAG in tags:
        until = parse_override(tags[OVERRIDE_TAG])
        if now < until:
            return Decision(False, f"override active until {until.isoformat()}")

    if schedule.is_business_hours(now):
        return Decision(False, "inside business hours")
    return Decision(True, "outside business hours")
