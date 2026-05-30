"""Build console output groups from /process API response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident, get_opened_at_ms


@dataclass(frozen=True)
class IncidentGroup:
    title: str
    stats_line: str
    rows: list[str]


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


def _format_incident_row(inc: ProcessedIncident, *, closed_width: int) -> str:
    state = inc.status_label or str(inc.status)
    opened = _format_display_time(inc.started_at)
    if inc.resolved_at:
        closed = _format_display_time(inc.resolved_at)
    else:
        closed = " " * closed_width
    text = _incident_text(inc)
    return "\t".join([state, opened, closed, text, inc.id])


def _group_stats_line(members: list[ProcessedIncident]) -> str:
    if not members:
        return "первая: — | последняя: — | аварий: 0"
    ordered = sorted(members, key=get_opened_at_ms)
    first = _format_display_time(ordered[0].started_at)
    last = _format_display_time(ordered[-1].started_at)
    return f"первая: {first} | последняя: {last} | аварий: {len(members)}"


def _sort_members(members: list[ProcessedIncident]) -> list[ProcessedIncident]:
    return sorted(members, key=get_opened_at_ms, reverse=True)


def build_groups(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
    cfg: Settings,
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
        members = _sort_members(members)

        closed_values = [_format_display_time(inc.resolved_at) for inc in members]
        closed_width = max((len(v) for v in closed_values), default=0)

        rows = [_format_incident_row(inc, closed_width=closed_width) for inc in members]
        groups.append(
            IncidentGroup(
                title=_group_title(inc),
                stats_line=_group_stats_line(members),
                rows=rows,
            )
        )

    return groups


def format_groups(groups: list[IncidentGroup]) -> str:
    blocks: list[str] = []
    for group in groups:
        lines = [group.title, group.stats_line, *group.rows]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
