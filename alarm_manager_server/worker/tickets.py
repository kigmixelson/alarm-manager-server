"""Persistent tickets: create on new groups, update on changes, close when cleared or gone."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alarm_manager_server.worker.formatter import (
    IncidentGroup,
    TrackedIncidentGroup,
    _incident_text,
    _object_name,
    format_groups,
    is_all_cleared_group,
)

logger = logging.getLogger(__name__)

CLOSE_ALL_CLEARED = "all_cleared"
CLOSE_REMOVED = "removed"
CLOSE_GROUP_CHANGED = "group_changed"


@dataclass(frozen=True)
class TicketEvent:
    action: str  # created | updated | closed
    ticket_id: str
    group: TrackedIncidentGroup | None
    changes: list[str]
    close_reason: str | None = None
    title: str = ""


@dataclass
class TicketSyncResult:
    events: list[TicketEvent]
    open_count: int
    created: int
    updated: int
    closed: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _member_snapshot(inc: Any) -> dict[str, Any]:
    from alarm_manager_server.models.incident import ProcessedIncident

    assert isinstance(inc, ProcessedIncident)
    return {
        "status": str(inc.status),
        "status_label": inc.status_label or "",
        "started_at": inc.started_at or "",
        "resolved_at": inc.resolved_at or "",
        "text": _incident_text(inc),
        "object": _object_name(inc),
        "avaria_owner": (inc.avaria_owner or "").strip(),
    }


def group_snapshot(group: TrackedIncidentGroup) -> dict[str, Any]:
    return {
        "group_key": group.group_key,
        "head_id": group.head_id,
        "title": group.display.title,
        "member_ids": list(group.member_ids),
        "members": {mid: _member_snapshot(m) for mid, m in zip(group.member_ids, group.members, strict=True)},
        "responsible_line": group.display.responsible_line or "",
        "stats_line": group.display.stats_line,
    }


def _overlap_score(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    old_ids = set(old.get("member_ids") or [])
    new_ids = set(new.get("member_ids") or [])
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    if added:
        changes.append(f"+{len(added)} авария" + ("и" if len(added) > 1 else "") + (f" ({', '.join(added[:3])}{'…' if len(added) > 3 else ''})" if len(added) <= 5 else ""))
    if removed:
        changes.append(f"−{len(removed)} авария" + ("и" if len(removed) > 1 else ""))

    old_members = old.get("members") or {}
    new_members = new.get("members") or {}
    for mid in sorted(old_ids & new_ids):
        o = old_members.get(mid) or {}
        n = new_members.get(mid) or {}
        if o == n:
            continue
        parts: list[str] = []
        if o.get("status_label") != n.get("status_label") or o.get("status") != n.get("status"):
            parts.append(f"состояние: {o.get('status_label') or o.get('status')} → {n.get('status_label') or n.get('status')}")
        if o.get("resolved_at") != n.get("resolved_at"):
            parts.append("время закрытия")
        if o.get("text") != n.get("text"):
            parts.append("текст")
        if o.get("object") != n.get("object"):
            parts.append("объект")
        if o.get("avaria_owner") != n.get("avaria_owner"):
            parts.append("ответственный")
        if parts:
            changes.append(f"{mid}: {', '.join(parts)}")

    if old.get("title") != new.get("title"):
        changes.append("заголовок группы")
    if old.get("responsible_line") != new.get("responsible_line"):
        changes.append("ответственный по группе")
    if old.get("stats_line") != new.get("stats_line") and not added and not removed:
        changes.append("статистика")
    return changes


class TicketStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"next_seq": 1, "tickets": {}, "open_by_group_key": {}}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("tickets load failed: %s", exc)
            return
        if isinstance(raw, dict):
            self._data = raw
            self._data.setdefault("next_seq", 1)
            self._data.setdefault("tickets", {})
            self._data.setdefault("open_by_group_key", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _alloc_id(self) -> str:
        seq = int(self._data.get("next_seq") or 1)
        self._data["next_seq"] = seq + 1
        return f"T-{seq:06d}"

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = (self._data.get("tickets") or {}).get(ticket_id)
        return ticket if isinstance(ticket, dict) else None

    def open_tickets(self) -> list[dict[str, Any]]:
        tickets = self._data.get("tickets") or {}
        return [t for t in tickets.values() if t.get("status") == "open"]

    def get_open_by_key(self, group_key: str) -> dict[str, Any] | None:
        ticket_id = (self._data.get("open_by_group_key") or {}).get(group_key)
        if not ticket_id:
            return None
        ticket = (self._data.get("tickets") or {}).get(ticket_id)
        if ticket and ticket.get("status") == "open":
            return ticket
        return None

    def _find_open_by_overlap(self, member_ids: set[str]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for ticket in self.open_tickets():
            prev = set((ticket.get("snapshot") or {}).get("member_ids") or [])
            score = _overlap_score(member_ids, prev)
            if score > best_score:
                best_score = score
                best = ticket
        if best is not None and best_score >= 0.5:
            return best
        return None

    def create(self, group: TrackedIncidentGroup) -> str:
        ticket_id = self._alloc_id()
        snap = group_snapshot(group)
        now = _utc_now_iso()
        ticket = {
            "ticket_id": ticket_id,
            "group_key": group.group_key,
            "status": "open",
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "close_reason": None,
            "snapshot": snap,
        }
        self._data["tickets"][ticket_id] = ticket
        self._data["open_by_group_key"][group.group_key] = ticket_id
        return ticket_id

    def update(self, ticket: dict[str, Any], group: TrackedIncidentGroup) -> None:
        old_key = ticket.get("group_key")
        new_key = group.group_key
        ticket_id = ticket["ticket_id"]
        ticket["group_key"] = new_key
        ticket["updated_at"] = _utc_now_iso()
        ticket["snapshot"] = group_snapshot(group)
        open_map = self._data.setdefault("open_by_group_key", {})
        if old_key and old_key != new_key:
            if open_map.get(old_key) == ticket_id:
                open_map.pop(old_key, None)
        open_map[new_key] = ticket_id

    def close(self, ticket: dict[str, Any], *, reason: str) -> None:
        ticket_id = ticket["ticket_id"]
        ticket["status"] = "closed"
        ticket["closed_at"] = _utc_now_iso()
        ticket["close_reason"] = reason
        open_map = self._data.get("open_by_group_key") or {}
        gk = ticket.get("group_key")
        if gk and open_map.get(gk) == ticket_id:
            open_map.pop(gk, None)


def _close_reason(
    ticket: dict[str, Any],
    *,
    incidents_by_id: dict[str, Any],
) -> str:
    member_ids = list((ticket.get("snapshot") or {}).get("member_ids") or [])
    if not member_ids:
        return CLOSE_GROUP_CHANGED
    known = [mid for mid in member_ids if mid in incidents_by_id]
    if not known:
        return CLOSE_REMOVED
    members = [incidents_by_id[mid] for mid in known]
    if is_all_cleared_group(members) and len(known) == len(member_ids):
        return CLOSE_ALL_CLEARED
    if len(known) < len(member_ids):
        return CLOSE_REMOVED
    return CLOSE_GROUP_CHANGED


def sync_tickets(
    store: TicketStore,
    all_groups: list[TrackedIncidentGroup],
    *,
    incidents_by_id: dict[str, Any],
) -> TicketSyncResult:
    """Match current groups to open tickets; emit create/update/close events."""
    events: list[TicketEvent] = []
    matched_ticket_ids: set[str] = set()

    for group in all_groups:
        if is_all_cleared_group(group.members):
            ticket = store.get_open_by_key(group.group_key)
            if ticket is None:
                ticket = store._find_open_by_overlap(set(group.member_ids))
            if ticket is not None:
                ticket_id = ticket["ticket_id"]
                matched_ticket_ids.add(ticket_id)
                store.close(ticket, reason=CLOSE_ALL_CLEARED)
                snap = ticket.get("snapshot") or {}
                events.append(
                    TicketEvent(
                        action="closed",
                        ticket_id=ticket_id,
                        group=None,
                        changes=[],
                        close_reason=CLOSE_ALL_CLEARED,
                        title=str(snap.get("title") or group.display.title),
                    )
                )
            continue

        ticket = store.get_open_by_key(group.group_key)
        if ticket is None:
            ticket = store._find_open_by_overlap(set(group.member_ids))
        snap = group_snapshot(group)

        if ticket is None:
            ticket_id = store.create(group)
            events.append(
                TicketEvent(
                    action="created",
                    ticket_id=ticket_id,
                    group=group,
                    changes=[],
                    title=group.display.title,
                )
            )
            matched_ticket_ids.add(ticket_id)
            continue

        ticket_id = ticket["ticket_id"]
        matched_ticket_ids.add(ticket_id)
        old_snap = ticket.get("snapshot") or {}
        changes = diff_snapshots(old_snap, snap)
        if changes or ticket.get("group_key") != group.group_key:
            store.update(ticket, group)
            events.append(
                TicketEvent(
                    action="updated",
                    ticket_id=ticket_id,
                    group=group,
                    changes=changes or ["состав группы"],
                    title=group.display.title,
                )
            )

    for ticket in store.open_tickets():
        tid = ticket["ticket_id"]
        if tid in matched_ticket_ids:
            continue
        reason = _close_reason(ticket, incidents_by_id=incidents_by_id)
        store.close(ticket, reason=reason)
        snap = ticket.get("snapshot") or {}
        events.append(
            TicketEvent(
                action="closed",
                ticket_id=tid,
                group=None,
                changes=[],
                close_reason=reason,
                title=str(snap.get("title") or ""),
            )
        )

    store.save()
    created = sum(1 for e in events if e.action == "created")
    updated = sum(1 for e in events if e.action == "updated")
    closed = sum(1 for e in events if e.action == "closed")
    return TicketSyncResult(
        events=events,
        open_count=len(store.open_tickets()),
        created=created,
        updated=updated,
        closed=closed,
    )


def _format_group_block(group: IncidentGroup) -> str:
    return format_groups([group])


def format_ticket_events(
    events: list[TicketEvent],
    *,
    print_unchanged: bool = False,
) -> str:
    blocks: list[str] = []
    for event in events:
        if event.action == "closed":
            reason_labels = {
                CLOSE_ALL_CLEARED: "все аварии Cleared",
                CLOSE_REMOVED: "аварии отсутствуют в выборке",
                CLOSE_GROUP_CHANGED: "группа расформирована или изменила состав",
            }
            label = reason_labels.get(event.close_reason or "", event.close_reason or "")
            blocks.append(f"[CLOSE {event.ticket_id}] {event.title}\n  причина: {label}")
            continue
        if event.group is None:
            continue
        header = f"[{event.action.upper()} {event.ticket_id}]"
        if event.changes:
            header += f" изменения: {'; '.join(event.changes)}"
        body = _format_group_block(event.group.display)
        blocks.append(f"{header}\n{body}")
    if not blocks and not print_unchanged:
        return ""
    return "\n\n".join(blocks)


def close_reason_label(reason: str | None) -> str:
    return {
        CLOSE_ALL_CLEARED: "все Cleared",
        CLOSE_REMOVED: "удалено из API",
        CLOSE_GROUP_CHANGED: "изменение группировки",
    }.get(reason or "", reason or "")
