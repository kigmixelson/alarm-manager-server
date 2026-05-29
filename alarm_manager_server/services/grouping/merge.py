"""Merge owner, class, and synthetic groupings — mirrors Index.tsx merge logic."""

from __future__ import annotations

from alarm_manager_server.models.incident import GroupingResult, Incident


SYNTH_PREFIX = "__synth__"


def merge_groupings(
    owner: GroupingResult,
    class_grouping: GroupingResult,
    synthetic_incidents: list[Incident],
) -> GroupingResult:
    children_of: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}

    for pid, kids in owner.children_of.items():
        children_of[pid] = list(kids)
        for k in kids:
            parent_of[k] = pid

    for pid, kids in class_grouping.children_of.items():
        arr = children_of.get(pid, [])
        added = False
        for k in kids:
            if k in parent_of:
                continue
            if k in children_of:
                continue
            arr.append(k)
            parent_of[k] = pid
            added = True
        if added or pid in children_of:
            children_of[pid] = arr

    for synth in synthetic_incidents:
        child_ids = synth.synthetic_child_ids
        kids: list[str] = []
        for k in child_ids:
            if k in parent_of:
                continue
            if k in children_of:
                continue
            kids.append(k)
            parent_of[k] = synth.id

        if len(kids) >= 2:
            children_of[synth.id] = kids
        else:
            for k in kids:
                parent_of.pop(k, None)

    return GroupingResult(
        children_of=children_of,
        parent_of=parent_of,
        parent_title_of=class_grouping.parent_title_of,
    )
