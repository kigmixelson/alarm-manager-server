from pathlib import Path

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.worker.formatter import (
    build_tracked_groups,
    compute_group_key,
)
from alarm_manager_server.worker.tickets import (
    CLOSE_ALL_CLEARED,
    TicketStore,
    diff_snapshots,
    group_snapshot,
    sync_tickets,
)


def _inc(
    id: str,
    *,
    status: int | str = 2,
    status_label: str = "warning",
    started_at: str = "2025-01-01T10:00:00+00:00",
    resolved_at: str | None = None,
    title: str = "Host",
    owner_display_title: str = "",
    is_synthetic: bool = False,
    entity_id: str = "",
) -> ProcessedIncident:
    return ProcessedIncident(
        id=id,
        title=title,
        display_title=title,
        owner_display_title=owner_display_title or title,
        object_display_name=title,
        entity_id=entity_id,
        severity=1,
        status=status,
        status_label=status_label,
        started_at=started_at,
        resolved_at=resolved_at,
        text="alarm",
        is_synthetic=is_synthetic,
    )


def test_compute_group_key_synthetic():
    head = _inc("__synth__e1", is_synthetic=True, title="Router")
    assert compute_group_key(head, [_inc("c1")]) == "synth:__synth__e1"


def test_ticket_create_update_close(tmp_path: Path):
    path = tmp_path / "tickets.json"
    store = TicketStore(path)
    cfg = Settings()
    inc = _inc("a1", owner_display_title="G1")
    grouping = GroupingResult()
    groups = build_tracked_groups([inc], grouping, cfg)
    by_id = {inc.id: inc}

    r1 = sync_tickets(store, groups, incidents_by_id=by_id)
    assert r1.created == 1
    assert r1.open_count == 1
    assert path.is_file()

    r2 = sync_tickets(store, groups, incidents_by_id=by_id)
    assert r2.created == 0
    assert r2.updated == 0

    inc2 = _inc("a1", status=3, status_label="cleared", resolved_at="2025-01-01T12:00:00+00:00")
    by_id2 = {inc2.id: inc2}
    groups_cleared = build_tracked_groups([inc2], grouping, cfg)
    r3 = sync_tickets(store, groups_cleared, incidents_by_id=by_id2)
    assert r3.closed == 1
    assert store.open_tickets() == []
    closed = store._data["tickets"][r1.events[0].ticket_id]
    assert closed["close_reason"] == CLOSE_ALL_CLEARED


def test_ticket_new_member_triggers_update(tmp_path: Path):
    path = tmp_path / "tickets.json"
    store = TicketStore(path)
    cfg = Settings()
    parent = _inc("p", owner_display_title="Parent")
    grouping = GroupingResult(children_of={"p": ["c1"]}, parent_of={"c1": "p"})
    g1 = build_tracked_groups([parent], grouping, cfg)
    sync_tickets(store, g1, incidents_by_id={"p": parent})

    child = _inc("c1", title="Child")
    g2 = build_tracked_groups([parent, child], grouping, cfg)
    r = sync_tickets(store, g2, incidents_by_id={"p": parent, "c1": child})
    assert r.updated == 1
    assert any("+1" in ch for e in r.events for ch in e.changes)


def test_diff_detects_status_change():
    cfg = Settings()
    inc = _inc("a", status_label="warning")
    groups = build_tracked_groups([inc], GroupingResult(), cfg)
    old = group_snapshot(groups[0])
    new = dict(old)
    new["members"] = {
        "a": {
            **old["members"]["a"],
            "status_label": "critical",
        }
    }
    changes = diff_snapshots(old, new)
    assert any("состояние" in c for c in changes)
