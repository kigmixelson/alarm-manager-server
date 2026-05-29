"""Synthetic group wrappers — mirrors Index.tsx syntheticIncidents builder."""

from __future__ import annotations

from alarm_manager_server.models.incident import Incident, SyntheticGroupSeed
from alarm_manager_server.services.grouping.merge import SYNTH_PREFIX

CLEARED_STATE_ID = 3


def _work_status(inc: Incident) -> str:
    if str(inc.status) == str(CLEARED_STATE_ID):
        return "closed"
    if inc.assignee:
        return "in_work"
    return "open"


_WORK_RANK = {"open": 0, "in_work": 1, "closed": 2}


def build_synthetic_incidents(
    seeds: list[SyntheticGroupSeed],
    incidents_by_id: dict[str, Incident],
) -> list[Incident]:
    out: list[Incident] = []

    for seed in seeds:
        kids = [incidents_by_id[cid] for cid in seed.child_ids if cid in incidents_by_id]
        if len(kids) < 2:
            continue

        earliest = min(kids, key=lambda k: k.started_at).started_at
        best_status = "closed"
        best_status_id = kids[0].status
        best_severity = kids[0].severity

        for k in kids:
            ws = _work_status(k)
            if _WORK_RANK[ws] < _WORK_RANK[best_status]:
                best_status = ws
                best_status_id = k.status
            try:
                if float(k.severity) > float(best_severity):
                    best_severity = k.severity
            except (TypeError, ValueError):
                if str(k.severity) > str(best_severity):
                    best_severity = k.severity

        status_id = CLEARED_STATE_ID if best_status == "closed" else best_status_id

        resolved_at = None
        if best_status == "closed":
            resolved = [k.resolved_at for k in kids if k.resolved_at]
            resolved_at = max(resolved) if resolved else None

        assignee = ""
        if best_status == "in_work":
            assignee = next((k.assignee for k in kids if k.assignee), "•")

        group_text = f"Группа из {len(kids)}"

        out.append(
            Incident(
                id=f"{SYNTH_PREFIX}{seed.entity_id}",
                title=seed.name,
                severity=best_severity,
                status=status_id,
                service=seed.name,
                started_at=earliest,
                resolved_at=resolved_at,
                assignee=assignee,
                description=group_text,
                text=group_text,
                entity_id=seed.entity_id,
                is_synthetic=True,
                synthetic_child_ids=[k.id for k in kids],
                raw={
                    "__isSynthetic": True,
                    "__syntheticChildIds": [k.id for k in kids],
                    "__syntheticCount": len(kids),
                    "entityId": seed.entity_id,
                },
            )
        )

    return out
