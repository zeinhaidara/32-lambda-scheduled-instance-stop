"""List candidate EC2 and RDS instances and stop them."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from scheduler.schedule import SCHEDULE_TAG


@dataclass(frozen=True)
class Resource:
    kind: str  # "ec2" or "rds"
    id: str
    tags: dict[str, str]


def running_ec2(ec2) -> Iterator[Resource]:
    """Running instances that carry the schedule tag. Follows NextToken."""
    kwargs = {
        "Filters": [
            {"Name": "tag-key", "Values": [SCHEDULE_TAG]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    }
    while True:
        page = ec2.describe_instances(**kwargs)
        for reservation in page.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                yield Resource("ec2", inst["InstanceId"], tags)
        token = page.get("NextToken")
        if not token:
            return
        kwargs["NextToken"] = token


def available_rds(rds) -> Iterator[Resource]:
    """Available standalone DB instances. Follows Marker.

    RDS can't filter on tags server side, so every instance comes back and
    the opt-in check happens in decide(). Aurora members are left out
    because they are stopped per cluster, not per instance.
    """
    kwargs: dict = {}
    while True:
        page = rds.describe_db_instances(**kwargs)
        for db in page.get("DBInstances", []):
            if db.get("DBInstanceStatus") != "available" or db.get("DBClusterIdentifier"):
                continue
            tags = {t["Key"]: t["Value"] for t in db.get("TagList", [])}
            yield Resource("rds", db["DBInstanceIdentifier"], tags)
        marker = page.get("Marker")
        if not marker:
            return
        kwargs["Marker"] = marker


def stop(resource: Resource, ec2, rds) -> None:
    if resource.kind == "ec2":
        ec2.stop_instances(InstanceIds=[resource.id])
    elif resource.kind == "rds":
        rds.stop_db_instance(DBInstanceIdentifier=resource.id)
    else:
        raise ValueError(f"unknown resource kind {resource.kind!r}")
