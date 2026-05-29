"""Tests for class-ancestor grouping."""

from alarm_manager_server.models.incident import Incident, IncidentOwner
from alarm_manager_server.services.grouping.class_ancestor import group_by_class
from alarm_manager_server.saymon.object_store import ObjectStore


def _inc(id_: str, entity_id: str, *, is_history: bool = False) -> Incident:
    return Incident(
        id=id_,
        title=f"obj-{entity_id}",
        severity=1,
        status=1,
        started_at="2024-01-01T00:00:00+00:00",
        entity_id=entity_id,
        is_history=is_history,
        owner=IncidentOwner(_id=entity_id, name=f"obj-{entity_id}"),
        raw={"entityId": entity_id, "__isHistory": is_history},
    )


def test_class_grouping_links_to_parent_incident():
    store = ObjectStore()
    store.upsert_object("child-entity", class_id=5, parent_ids=["host-entity"], name="Child")
    store.upsert_object("host-entity", class_id=30, name="Host-1")

    child = _inc("inc-child", "child-entity")
    parent = _inc("inc-host", "host-entity")

    grouping, seeds = group_by_class([child, parent], store, {"30"}, depth=4)

    assert grouping.parent_of["inc-child"] == "inc-host"
    assert grouping.parent_title_of["inc-child"] == "Host-1"
    assert seeds == []


def test_class_grouping_creates_synthetic_seed():
    store = ObjectStore()
    store.upsert_object("svc1", class_id=5, parent_ids=["host-entity"])
    store.upsert_object("svc2", class_id=5, parent_ids=["host-entity"])
    store.upsert_object("host-entity", class_id=30, name="Host-1")

    inc1 = _inc("inc1", "svc1")
    inc2 = _inc("inc2", "svc2")

    grouping, seeds = group_by_class([inc1, inc2], store, {"30"}, depth=4)

    assert grouping.parent_of == {}
    assert len(seeds) == 1
    assert seeds[0].entity_id == "host-entity"
    assert set(seeds[0].child_ids) == {"inc1", "inc2"}


def test_class_grouping_skips_active_under_historical():
    store = ObjectStore()
    store.upsert_object("child", class_id=5, parent_ids=["host"])
    store.upsert_object("host", class_id=30, name="Host")

    child = _inc("active-child", "child")
    parent = _inc("hist-parent", "host", is_history=True)

    grouping, _ = group_by_class([child, parent], store, {"30"}, depth=4)
    assert "active-child" not in grouping.parent_of
