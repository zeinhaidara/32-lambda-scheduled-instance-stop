import json
from pathlib import Path

import pytest

from scheduler.handler import RunFailed, dry_run_from_env, event_time, lambda_handler, run
from tests.fakes import FakeEC2, FakeRDS, ec2_instance, rds_instance

LONDON = "mon-fri 08:00-18:00 Europe/London"
EVENT = json.loads(
    (Path(__file__).parent.parent / "events" / "scheduled-event.json").read_text(encoding="utf-8")
)
EVENING = event_time(EVENT)  # Friday 2026-11-20 19:00 UTC


def fleet():
    ec2 = FakeEC2(
        [
            ec2_instance("i-0aaa", auto_stop_schedule=LONDON, environment="dev"),
            ec2_instance("i-0bbb"),  # untagged
            ec2_instance("i-0ccc", auto_stop_schedule=LONDON, environment="prod"),
            ec2_instance("i-0ddd", state="stopped", auto_stop_schedule=LONDON),
            ec2_instance("i-0eee", auto_stop_schedule=LONDON),
        ]
    )
    rds = FakeRDS(
        [
            rds_instance("db-dev", auto_stop_schedule=LONDON, environment="test"),
            rds_instance("db-untagged"),
            rds_instance("db-prod", auto_stop_schedule=LONDON, environment="production"),
            rds_instance("db-aurora", cluster="aurora-1", auto_stop_schedule=LONDON),
            rds_instance("db-busy", status="backing-up", auto_stop_schedule=LONDON),
        ]
    )
    return ec2, rds


def test_sample_event_time_is_parsed():
    assert EVENING.isoformat() == "2026-11-20T19:00:00+00:00"


def test_stops_only_opted_in_non_prod_resources():
    ec2, rds = fleet()
    summary = run(ec2, rds, EVENING, dry_run=False)
    assert ec2.stopped == ["i-0aaa", "i-0eee"]
    assert rds.stopped == ["db-dev"]
    assert summary["stopped"] == ["ec2:i-0aaa", "ec2:i-0eee", "rds:db-dev"]
    assert summary["errors"] == []


def test_ec2_query_filters_on_tag_and_running_state():
    ec2, rds = fleet()
    run(ec2, rds, EVENING, dry_run=True)
    filters = {f["Name"]: f["Values"] for f in ec2.describe_calls[0]["Filters"]}
    assert filters == {"tag-key": ["auto-stop-schedule"], "instance-state-name": ["running"]}


def test_pagination_reads_every_page():
    ec2, rds = fleet()
    run(ec2, rds, EVENING, dry_run=True)
    # 3 matching EC2 instances at page size 2, 5 DB instances at page size 2.
    assert [c["NextToken"] for c in ec2.describe_calls] == [None, "2"]
    assert rds.markers == [None, "2", "4"]


def test_dry_run_touches_nothing():
    ec2, rds = fleet()
    summary = run(ec2, rds, EVENING, dry_run=True)
    assert ec2.stopped == [] and rds.stopped == []
    assert summary["would_stop"] == ["ec2:i-0aaa", "ec2:i-0eee", "rds:db-dev"]


def test_inside_business_hours_stops_nothing():
    ec2, rds = fleet()
    noon = event_time({"time": "2026-11-20T12:00:00Z"})
    summary = run(ec2, rds, noon, dry_run=False)
    assert ec2.stopped == [] and rds.stopped == []
    assert summary["stopped"] == []


def test_stop_failure_does_not_block_others_and_fails_the_run(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    ec2, rds = fleet()
    ec2.fail_ids = {"i-0aaa"}
    with pytest.raises(RunFailed, match="ec2:i-0aaa"):
        lambda_handler(EVENT, None, ec2=ec2, rds=rds)
    assert ec2.stopped == ["i-0eee"]
    assert rds.stopped == ["db-dev"]


def test_bad_tag_is_reported_and_not_stopped(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    ec2 = FakeEC2([ec2_instance("i-0bad", auto_stop_schedule="whenever")])
    rds = FakeRDS([])
    with pytest.raises(RunFailed, match="ec2:i-0bad"):
        lambda_handler(EVENT, None, ec2=ec2, rds=rds)
    assert ec2.stopped == []


def test_handler_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    ec2, rds = fleet()
    summary = lambda_handler(EVENT, None, ec2=ec2, rds=rds)
    assert summary["dry_run"] is True
    assert ec2.stopped == [] and rds.stopped == []


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("TRUE", True), ("0", False)])
def test_dry_run_values(raw, expected):
    assert dry_run_from_env({"DRY_RUN": raw}) is expected


def test_unknown_dry_run_value_fails_loudly():
    with pytest.raises(ValueError):
        dry_run_from_env({"DRY_RUN": "maybe"})
