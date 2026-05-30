"""Build console output groups from /process API response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident, get_opened_at_ms

CLEARED_STATE_ID = 3


@dataclass(frozen=True)
class IncidentGroup:
    title: str
    stats_line: str
    rows: list[str]
    responsible_line: str | None = None


def _group_title(inc: ProcessedIncident) -> str:
    if inc.owner_display_title:
        return inc.owner_display_title
    return inc.title


def _format_display_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


def _incident_text(inc: ProcessedIncident) -> str:
    text = (inc.text or inc.description or "").strip()
    return " ".join(text.split())


def _object_name(inc: ProcessedIncident) -> str:
    if inc.object_display_name.strip():
        return inc.object_display_name.strip()
    if inc.owner and inc.owner.name.strip():
        return inc.owner.name.strip()
    if inc.title.strip() and inc.title.strip() != inc.entity_id:
        return inc.title.strip()
    return inc.entity_id or inc.id


def _format_incident_row(
    inc: ProcessedIncident,
    *,
    closed_width: int,
    show_responsible: bool,
    is_child: bool,
) -> str:
    state = inc.status_label or str(inc.status)
    opened = _format_display_time(inc.started_at)
    if inc.resolved_at:
        closed = _format_display_time(inc.resolved_at)
    else:
        closed = " " * closed_width
    text = _incident_text(inc)
    show_row_responsible = show_responsible and not is_cleared_incident(inc)
    responsible = (inc.avaria_owner or "").strip() if show_row_responsible else ""

    if is_child:
        parts = [state, _object_name(inc), opened, closed, text]
    else:
        parts = [state, opened, closed, text, inc.id]

    if show_responsible:
        parts.append(responsible if show_row_responsible else "")
    return "\t".join(parts)


def _group_responsible_line(
    members: list[ProcessedIncident],
    *,
    show_responsible: bool,
) -> str | None:
    if not show_responsible:
        return None
    active = [inc for inc in members if not is_cleared_incident(inc)]
    owners = sorted({(inc.avaria_owner or "").strip() for inc in active if (inc.avaria_owner or "").strip()})
    if not owners:
        return None
    if len(owners) == 1:
        return f"ответственный: {owners[0]}"
    return f"ответственные: {', '.join(owners)}"


def _group_stats_line(members: list[ProcessedIncident]) -> str:
    if not members:
        return "первая: — | последняя: — | аварий: 0"
    ordered = sorted(members, key=get_opened_at_ms)
    first = _format_display_time(ordered[0].started_at)
    last = _format_display_time(ordered[-1].started_at)
    return f"первая: {first} | последняя: {last} | аварий: {len(members)}"


def _sort_members(members: list[ProcessedIncident]) -> list[ProcessedIncident]:
    return sorted(members, key=get_opened_at_ms, reverse=True)


def is_cleared_incident(inc: ProcessedIncident) -> bool:
    if str(inc.status) == str(CLEARED_STATE_ID):
        return True
    label = (inc.status_label or "").casefold()
    return label in {"cleared", "закрыта", "закрыто"}


def is_all_cleared_group(members: list[ProcessedIncident]) -> bool:
    return bool(members) and all(is_cleared_incident(inc) for inc in members)


def build_groups(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
    cfg: Settings,
    *,
    active_only: bool = False,
    show_responsible: bool = False,
) -> list[IncidentGroup]:
    """Top-level rows only; children are folded into their parent group."""
    del cfg  # reserved for future output options
    order = [inc.id for inc in incidents]
    by_id = {inc.id: inc for inc in incidents}
    groups: list[IncidentGroup] = []

    for inc_id in order:
        inc = by_id.get(inc_id)
        if inc is None or inc.id in grouping.parent_of:
            continue

        child_ids = grouping.children_of.get(inc.id, [])
        if inc.is_synthetic and not child_ids:
            continue

        if child_ids:
            member_ids = list(child_ids) if inc.is_synthetic else [inc.id, *child_ids]
        else:
            member_ids = [inc.id]

        members = [by_id[mid] for mid in member_ids if mid in by_id and not by_id[mid].is_synthetic]
        if active_only and is_all_cleared_group(members):
            continue

        members = _sort_members(members)

        closed_values = [_format_display_time(inc.resolved_at) for inc in members]
        closed_width = max((len(v) for v in closed_values), default=0)

        compact_rows = bool(child_ids)
        rows = [
            _format_incident_row(
                member,
                closed_width=closed_width,
                show_responsible=show_responsible,
                is_child=compact_rows,
            )
            for member in members
        ]
        responsible_line = _group_responsible_line(members, show_responsible=show_responsible)
        groups.append(
            IncidentGroup(
                title=_group_title(inc),
                stats_line=_group_stats_line(members),
                rows=rows,
                responsible_line=responsible_line,
            )
        )

    return groups


def format_groups(groups: list[IncidentGroup]) -> str:
    blocks: list[str] = []
    for group in groups:
        lines = [group.title, group.stats_line]
        if group.responsible_line:
            lines.append(group.responsible_line)
        lines.extend(group.rows)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
