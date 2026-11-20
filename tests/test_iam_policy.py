import json
from pathlib import Path

POLICY = json.loads(
    (Path(__file__).parent.parent / "iam" / "policy.json").read_text(encoding="utf-8")
)
STOP_ACTIONS = {"ec2:StopInstances", "rds:StopDBInstance"}


def statements(effect):
    return [s for s in POLICY["Statement"] if s["Effect"] == effect]


def actions(stmt):
    a = stmt["Action"]
    return {a} if isinstance(a, str) else set(a)


def test_every_stop_allow_requires_the_opt_in_tag():
    allows = [s for s in statements("Allow") if actions(s) & STOP_ACTIONS]
    assert len(allows) == 2
    for s in allows:
        assert s["Condition"] == {"Null": {"aws:ResourceTag/auto-stop-schedule": "false"}}
        assert "*" not in s["Resource"].split(":")[4]  # account is pinned


def test_production_is_explicitly_denied():
    denies = [s for s in statements("Deny") if actions(s) == STOP_ACTIONS]
    assert len(denies) == 1
    cond = denies[0]["Condition"]["StringEqualsIgnoreCase"]["aws:ResourceTag/environment"]
    assert set(cond) == {"prod", "production"}


def test_no_start_terminate_or_wildcard_actions():
    for s in POLICY["Statement"]:
        for action in actions(s):
            assert "*" not in action
            assert not action.lower().startswith(("ec2:start", "ec2:terminate", "rds:delete"))
