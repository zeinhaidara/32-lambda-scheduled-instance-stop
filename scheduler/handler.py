"""Lambda entry point, triggered by an EventBridge schedule."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from scheduler.resources import available_rds, running_ec2, stop
from scheduler.schedule import TagError, decide

log = logging.getLogger()
log.setLevel(logging.INFO)

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


class RunFailed(RuntimeError):
    """Raised after the run so the invocation is marked failed."""


def dry_run_from_env(environ=os.environ) -> bool:
    # Safe default: do nothing unless DRY_RUN is explicitly false.
    raw = environ.get("DRY_RUN", "true").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"DRY_RUN must be true or false, got {raw!r}")


def event_time(event: dict) -> datetime:
    """Use the EventBridge `time` so a run is judged at its scheduled moment."""
    raw = (event or {}).get("time")
    if raw is None:
        return datetime.now(UTC)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def run(ec2, rds, now: datetime, dry_run: bool) -> dict:
    summary: dict = {
        "dry_run": dry_run,
        "now": now.isoformat(),
        "stopped": [],
        "would_stop": [],
        "skipped": [],
        "errors": [],
    }

    for source in (running_ec2(ec2), available_rds(rds)):
        for res in source:
            ref = f"{res.kind}:{res.id}"
            try:
                decision = decide(res.tags, now)
            except TagError as exc:
                # Bad tags never lead to a stop; they fail the run at the end.
                summary["errors"].append({"resource": ref, "error": str(exc)})
                log.error("%s: %s", ref, exc)
                continue

            if not decision.stop:
                summary["skipped"].append({"resource": ref, "reason": decision.reason})
                continue
            if dry_run:
                summary["would_stop"].append(ref)
                log.info("dry run, would stop %s (%s)", ref, decision.reason)
                continue
            try:
                stop(res, ec2, rds)
            except Exception as exc:
                # Keep going so one failure doesn't leave the rest running.
                summary["errors"].append({"resource": ref, "error": repr(exc)})
                log.error("failed to stop %s: %r", ref, exc)
                continue
            summary["stopped"].append(ref)
            log.info("stopped %s (%s)", ref, decision.reason)

    return summary


def lambda_handler(event, context, ec2=None, rds=None):
    if ec2 is None or rds is None:
        import boto3

        ec2 = ec2 or boto3.client("ec2")
        rds = rds or boto3.client("rds")

    summary = run(ec2, rds, event_time(event), dry_run_from_env())
    log.info(json.dumps(summary))
    if summary["errors"]:
        names = ", ".join(e["resource"] for e in summary["errors"])
        raise RunFailed(f"{len(summary['errors'])} resource(s) failed: {names}")
    return summary
