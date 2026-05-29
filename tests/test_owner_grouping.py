"""Tests for owner-based grouping."""

from alarm_manager_server.models.incident import Incident, IncidentOwner
from alarm_manager_server.services.grouping.owner import group_by_owner


def _inc(
    id_: str,
    entity_id: str,
    *,
    is_history: bool = False,
    ts: int = 1000,
    owner_id: str | None = None,
) -> Incident:
    owner = IncidentOwner(_id=owner_id or entity_id, name=f"obj-{entity_id}")
    return Incident(
        id=id_,
        title=owner.name,
        severity=1,
        status=1,
        started_at="2024-01-01T00:00:00+00:00",
        entity_id=entity_id,
        is_history=is_history,
        owner=owner,
        raw={"entityId": entity_id, "localTimestamp": ts, "__isHistory": is_history},
    )


def test_owner_grouping_requires_at_least_two():
    result = group_by_owner([_inc("1", "e1")])
    assert result.children_of == {}
    assert result.parent_of == {}


def test_owner_grouping_picks_newest_active_parent():
    incs = [
        _inc("old-active", "e1", ts=1000),
        _inc("new-active", "e1", ts=2000),
        _inc("history", "e1", is_history=True, ts=3000),
    ]
    result = group_by_owner(incs)
    assert result.parent_of["old-active"] == "new-active"
    assert result.parent_of["history"] == "new-active"
    assert result.children_of["new-active"] == ["old-active", "history"]


def test_owner_grouping_skips_active_under_historical_parent():
    incs = [
        _inc("history", "e1", is_history=True, ts=3000),
        _inc("active", "e1", ts=1000),
    ]
    result = group_by_owner(incs)
    assert result.children_of == {}
    assert result.parent_of == {}
