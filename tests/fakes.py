"""Hand-written fake EC2 and RDS clients. They page results and record calls."""

from __future__ import annotations

from botocore.exceptions import ClientError


def tags(**kv) -> list[dict]:
    # Python names can't hold "-", so accept "auto_stop_schedule" style keys.
    return [{"Key": k.replace("_", "-"), "Value": v} for k, v in kv.items()]


def _error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "fake"}}, op)


class FakeEC2:
    def __init__(self, instances: list[dict], page_size: int = 2, fail_ids=()):
        self.instances = instances
        self.page_size = page_size
        self.fail_ids = set(fail_ids)
        self.describe_calls: list[dict] = []
        self.stopped: list[str] = []

    def describe_instances(self, Filters, NextToken=None):
        self.describe_calls.append({"Filters": Filters, "NextToken": NextToken})
        wanted_key = next(f["Values"][0] for f in Filters if f["Name"] == "tag-key")
        wanted_state = next(f["Values"][0] for f in Filters if f["Name"] == "instance-state-name")
        # Apply the filters the way EC2 would.
        matches = [
            i
            for i in self.instances
            if i["State"]["Name"] == wanted_state
            and any(t["Key"] == wanted_key for t in i.get("Tags", []))
        ]
        start = int(NextToken or 0)
        chunk = matches[start : start + self.page_size]
        page = {"Reservations": [{"Instances": chunk}]}
        if start + self.page_size < len(matches):
            page["NextToken"] = str(start + self.page_size)
        return page

    def stop_instances(self, InstanceIds):
        for iid in InstanceIds:
            if iid in self.fail_ids:
                raise _error("IncorrectInstanceState", "StopInstances")
        self.stopped.extend(InstanceIds)
        return {"StoppingInstances": [{"InstanceId": i} for i in InstanceIds]}


class FakeRDS:
    def __init__(self, dbs: list[dict], page_size: int = 2, fail_ids=()):
        self.dbs = dbs
        self.page_size = page_size
        self.fail_ids = set(fail_ids)
        self.markers: list[str | None] = []
        self.stopped: list[str] = []

    def describe_db_instances(self, Marker=None):
        self.markers.append(Marker)
        start = int(Marker or 0)
        page = {"DBInstances": self.dbs[start : start + self.page_size]}
        if start + self.page_size < len(self.dbs):
            page["Marker"] = str(start + self.page_size)
        return page

    def stop_db_instance(self, DBInstanceIdentifier):
        if DBInstanceIdentifier in self.fail_ids:
            raise _error("InvalidDBInstanceState", "StopDBInstance")
        self.stopped.append(DBInstanceIdentifier)
        return {"DBInstance": {"DBInstanceIdentifier": DBInstanceIdentifier}}


def ec2_instance(iid: str, state: str = "running", **kv) -> dict:
    return {"InstanceId": iid, "State": {"Name": state}, "Tags": tags(**kv)}


def rds_instance(dbid: str, status: str = "available", cluster: str | None = None, **kv) -> dict:
    db = {"DBInstanceIdentifier": dbid, "DBInstanceStatus": status, "TagList": tags(**kv)}
    if cluster:
        db["DBClusterIdentifier"] = cluster
    return db
