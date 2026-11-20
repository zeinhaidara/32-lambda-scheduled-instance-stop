from datetime import datetime, time

import pytest

from scheduler.schedule import (
    OVERRIDE_TAG,
    SCHEDULE_TAG,
    TagError,
    decide,
    parse_override,
    parse_schedule,
)

LONDON = "mon-fri 08:00-18:00 Europe/London"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text + "+00:00")


# --- parsing -----------------------------------------------------------------


def test_parse_range_and_list():
    s = parse_schedule("Mon-Wed,Fri 07:30-19:00 America/New_York")
    assert s.days == frozenset({0, 1, 2, 4})
    assert (s.start, s.end) == (time(7, 30), time(19, 0))
    assert s.tz.key == "America/New_York"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "always",
        "mon-fri 08:00-18:00",  # no timezone
        "mon-fri 18:00-08:00 Europe/London",  # overnight not supported
        "mon-fri 08:00-08:00 Europe/London",  # empty window
        "fri-mon 08:00-18:00 Europe/London",  # backwards range
        "mon-fry 08:00-18:00 Europe/London",
        "mon-fri 25:00-26:00 Europe/London",
        "mon-fri 08:00-18:00 Mars/Olympus_Mons",
    ],
)
def test_bad_schedules_are_rejected(value):
    with pytest.raises(TagError):
        parse_schedule(value)


def test_override_needs_offset():
    with pytest.raises(TagError, match="offset"):
        parse_override("2026-11-20T22:00:00")
    with pytest.raises(TagError):
        parse_override("next tuesday")
    assert parse_override("2026-11-20T22:00:00Z").tzinfo is not None


# --- business hours and DST --------------------------------------------------


@pytest.mark.parametrize(
    ("now", "inside"),
    [
        ("2026-11-20T12:00:00", True),  # Friday midday, GMT
        ("2026-11-20T19:00:00", False),  # Friday evening
        ("2026-11-20T08:00:00", True),  # start is inclusive
        ("2026-11-20T18:00:00", False),  # end is exclusive
        ("2026-11-21T12:00:00", False),  # Saturday
    ],
)
def test_business_hours_winter(now, inside):
    assert parse_schedule(LONDON).is_business_hours(utc(now)) is inside


def test_london_spring_forward_moves_the_window_in_utc():
    s = parse_schedule(LONDON)
    # Clocks go forward 2026-03-29. 07:30 UTC is 07:30 GMT before, 08:30 BST after.
    assert s.is_business_hours(utc("2026-03-27T07:30:00")) is False
    assert s.is_business_hours(utc("2026-03-30T07:30:00")) is True


def test_london_fall_back_moves_the_window_in_utc():
    s = parse_schedule(LONDON)
    # Clocks go back 2026-10-25. 17:30 UTC is 18:30 BST before, 17:30 GMT after.
    assert s.is_business_hours(utc("2026-10-23T17:30:00")) is False
    assert s.is_business_hours(utc("2026-10-26T17:30:00")) is True


def test_new_york_spring_forward():
    s = parse_schedule("mon-fri 08:00-18:00 America/New_York")
    # US clocks go forward 2026-03-08. 12:30 UTC: 07:30 EST before, 08:30 EDT after.
    assert s.is_business_hours(utc("2026-03-06T12:30:00")) is False
    assert s.is_business_hours(utc("2026-03-09T12:30:00")) is True


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        parse_schedule(LONDON).is_business_hours(datetime(2026, 11, 20, 12))


# --- decide ------------------------------------------------------------------

EVENING = utc("2026-11-20T19:00:00")


def test_untagged_is_never_stopped():
    assert decide({}, EVENING).stop is False
    assert decide({"Name": "web"}, EVENING).reason == "not opted in"


@pytest.mark.parametrize("env", ["prod", "Production", " PROD "])
def test_production_is_never_stopped(env):
    d = decide({SCHEDULE_TAG: LONDON, "environment": env}, EVENING)
    assert d == d.__class__(False, "production resource")


def test_production_wins_even_with_a_broken_schedule():
    # Never even parse tags on prod; nothing to fail on.
    assert decide({SCHEDULE_TAG: "garbage", "environment": "prod"}, EVENING).stop is False


def test_outside_hours_stops():
    assert decide({SCHEDULE_TAG: LONDON, "environment": "dev"}, EVENING).stop is True


def test_inside_hours_keeps_running():
    d = decide({SCHEDULE_TAG: LONDON}, utc("2026-11-20T10:00:00"))
    assert (d.stop, d.reason) == (False, "inside business hours")


def test_active_override_blocks_stop():
    tags = {SCHEDULE_TAG: LONDON, OVERRIDE_TAG: "2026-11-20T22:00:00+00:00"}
    d = decide(tags, EVENING)
    assert d.stop is False
    assert d.reason.startswith("override active until")


def test_expired_override_is_ignored():
    tags = {SCHEDULE_TAG: LONDON, OVERRIDE_TAG: "2026-11-20T18:59:59+00:00"}
    assert decide(tags, EVENING).stop is True


def test_override_in_other_offset_is_compared_correctly():
    # 20:30 in New York (UTC-5) is 01:30 UTC next day, so still active at 19:00 UTC.
    tags = {SCHEDULE_TAG: LONDON, OVERRIDE_TAG: "2026-11-20T20:30:00-05:00"}
    assert decide(tags, EVENING).stop is False


def test_bad_override_raises_instead_of_stopping():
    tags = {SCHEDULE_TAG: LONDON, OVERRIDE_TAG: "tomorrow"}
    with pytest.raises(TagError):
        decide(tags, EVENING)
