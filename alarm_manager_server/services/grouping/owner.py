"""Owner-based grouping — mirrors useOwnerGrouping.ts."""

from __future__ import annotations

from alarm_manager_server.models.incident import (
    GroupingResult,
    Incident,
    get_opened_at_ms,
    is_active,
)


def _owner_key(incident: Incident) -> str | None:
    if incident.owner and incident.owner.id:
        return f"id:{incident.owner.id}"
    if incident.entity_id:
        return f"eid:{incident.entity_id}"
    if incident.owner and incident.owner.name:
        return f"name:{incident.owner.name}"
    return None


def group_by_owner(incidents: list[Incident], *, enabled: bool = True) -> GroupingResult:
    if not enabled or not incidents:
        return GroupingResult()

    buckets: dict[str, list[Incident]] = {}
    for inc in incidents:
        key = _owner_key(inc)
        if key is None:
            continue
        buckets.setdefault(key, []).append(inc)

    children_of: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}

    for group in buckets.values():
        if len(group) < 2:
            continue

        sorted_group = sorted(group, key=get_opened_at_ms, reverse=True)
        parent = next((x for x in sorted_group if is_active(x)), sorted_group[0])
        parent_is_active = is_active(parent)

        children = [
            x.id
            for x in sorted_group
            if x.id != parent.id and (parent_is_active or not is_active(x))
        ]
        if not children:
            continue

        children_of[parent.id] = children
        for cid in children:
            parent_of[cid] = parent.id

    return GroupingResult(children_of=children_of, parent_of=parent_of)
