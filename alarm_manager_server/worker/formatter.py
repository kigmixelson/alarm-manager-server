"""Build console output groups from /process API response."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import (
    GroupingResult,
    ProcessedIncident,
    get_opened_at_ms,
    incident_object_id,
    is_placeholder_object_name,
)

CLEARED_STATE_ID = 3


@dataclass(frozen=True)
class IncidentGroup:
    title: str
    stats_line: str
    rows: list[str]
    responsible_line: str | None = None


@dataclass(frozen=True)
class GroupSpec:
    """One console group before formatting (head + non-synthetic members)."""

    head: ProcessedIncident
    members: list[ProcessedIncident]


@dataclass(frozen=True)
class TrackedIncidentGroup:
    """Group with stable key for ticket tracking across worker runs."""

    group_key: str
    head_id: str
    member_ids: tuple[str, ...]
    head: ProcessedIncident
    members: list[ProcessedIncident]
    display: IncidentGroup


def compute_group_key(head: ProcessedIncident, members: list[ProcessedIncident]) -> str:
    """Stable identity: synthetic container, owner object, or single incident."""
    if head.is_synthetic:
        return f"synth:{head.id}"
    if len(members) > 1:
        owner_id = incident_object_id(head)
        if owner_id:
            return f"owner:{owner_id}"
    return f"inc:{head.id}"


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
    if inc.object_display_name.strip() and not is_placeholder_object_name(inc.object_display_name, inc):
        return inc.object_display_name.strip()
    if inc.owner and inc.owner.name.strip() and not is_placeholder_object_name(inc.owner.name, inc):
        return inc.owner.name.strip()
    if inc.title.strip() and not is_placeholder_object_name(inc.title, inc):
        return inc.title.strip()
    return "—"


def _row_signature(inc: ProcessedIncident) -> tuple[str, str, str]:
    """Same state + object + text → repeated alarm in a group."""
    return (
        inc.status_label or str(inc.status),
        _object_name(inc),
        _incident_text(inc),
    )


def _collapse_repeated_member_rows(
    members: list[ProcessedIncident],
    *,
    closed_width: int,
    show_responsible: bool,
    show_row_responsible: bool,
) -> list[str]:
    """For 3+ identical alarms show newest, '...', oldest; keep full count in stats."""
    buckets: dict[tuple[str, str, str], list[ProcessedIncident]] = {}
    bucket_order: list[tuple[str, str, str]] = []
    for member in members:
        sig = _row_signature(member)
        if sig not in buckets:
            buckets[sig] = []
            bucket_order.append(sig)
        buckets[sig].append(member)

    rows: list[str] = []
    for sig in bucket_order:
        group = buckets[sig]
        if len(group) < 3:
            for inc in group:
                rows.append(
                    _format_incident_row(
                        inc,
                        closed_width=closed_width,
                        show_responsible=show_responsible,
                        show_row_responsible=show_row_responsible,
                    )
                )
            continue

        ordered = sorted(group, key=get_opened_at_ms)
        first = ordered[0]
        last = ordered[-1]
        rows.append(
            _format_incident_row(
                last,
                closed_width=closed_width,
                show_responsible=show_responsible,
                show_row_responsible=show_row_responsible,
            )
        )
        rows.append("...")
        rows.append(
            _format_incident_row(
                first,
                closed_width=closed_width,
                show_responsible=show_responsible,
                show_row_responsible=show_row_responsible,
            )
        )
    return rows


def _format_incident_row(
    inc: ProcessedIncident,
    *,
    closed_width: int,
    show_responsible: bool,
    show_row_responsible: bool,
) -> str:
    state = inc.status_label or str(inc.status)
    opened = _format_display_time(inc.started_at)
    if inc.resolved_at:
        closed = _format_display_time(inc.resolved_at)
    else:
        closed = " " * closed_width
    text = _incident_text(inc)
    responsible = (inc.avaria_owner or "").strip() if show_row_responsible else ""

    parts = [state, _object_name(inc), opened, closed, text]
    if show_responsible:
        parts.append(responsible)
    return "\t".join(parts)


def _group_responsible_line(
    members: list[ProcessedIncident],
    *,
    show_responsible: bool,
) -> str | None:
    if not show_responsible:
        return None
    owners = sorted({(inc.avaria_owner or "").strip() for inc in members if (inc.avaria_owner or "").strip()})
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


def _make_incident_group(
    head: ProcessedIncident,
    members: list[ProcessedIncident],
    *,
    show_responsible: bool,
) -> IncidentGroup | None:
    if not members:
        return None

    members = _sort_members(members)
    closed_values = [_format_display_time(inc.resolved_at) for inc in members]
    closed_width = max((len(v) for v in closed_values), default=0)
    show_row_responsible = show_responsible and len(members) == 1
    rows = _collapse_repeated_member_rows(
        members,
        closed_width=closed_width,
        show_responsible=show_responsible,
        show_row_responsible=show_row_responsible,
    )
    return IncidentGroup(
        title=_group_title(head),
        stats_line=_group_stats_line(members),
        rows=rows,
        responsible_line=_group_responsible_line(members, show_responsible=show_responsible),
    )


def iter_group_specs(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
) -> list[GroupSpec]:
    """Collect group heads and members (no active-only filter)."""
    order: list[str] = []
    seen_order: set[str] = set()
    for inc in incidents:
        if inc.id not in seen_order:
            seen_order.add(inc.id)
            order.append(inc.id)

    by_id = {inc.id: inc for inc in incidents}
    specs: list[GroupSpec] = []
    shown_ids: set[str] = set()

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
        if not members:
            continue
        for member in members:
            shown_ids.add(member.id)
        specs.append(GroupSpec(head=inc, members=members))

    for inc_id in order:
        inc = by_id.get(inc_id)
        if inc is None or inc.is_synthetic or inc.id in shown_ids:
            continue
        specs.append(GroupSpec(head=inc, members=[inc]))
        shown_ids.add(inc.id)

    return specs


def build_tracked_groups(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
    cfg: Settings,
    *,
    active_only: bool = False,
    show_responsible: bool = False,
) -> list[TrackedIncidentGroup]:
    del cfg
    tracked: list[TrackedIncidentGroup] = []
    for spec in iter_group_specs(incidents, grouping):
        if active_only and is_all_cleared_group(spec.members):
            continue
        display = _make_incident_group(
            spec.head,
            spec.members,
            show_responsible=show_responsible,
        )
        if display is None:
            continue
        member_ids = tuple(m.id for m in spec.members)
        tracked.append(
            TrackedIncidentGroup(
                group_key=compute_group_key(spec.head, spec.members),
                head_id=spec.head.id,
                member_ids=member_ids,
                head=spec.head,
                members=spec.members,
                display=display,
            )
        )
    return tracked


def build_groups(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
    cfg: Settings,
    *,
    active_only: bool = False,
    show_responsible: bool = False,
) -> list[IncidentGroup]:
    """Top-level rows only; children are folded into their parent group."""
    return [
        g.display
        for g in build_tracked_groups(
            incidents,
            grouping,
            cfg,
            active_only=active_only,
            show_responsible=show_responsible,
        )
    ]


def format_groups(groups: list[IncidentGroup]) -> str:
    blocks: list[str] = []
    for group in groups:
        lines = [group.title, group.stats_line]
        if group.responsible_line:
            lines.append(group.responsible_line)
        lines.extend(group.rows)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
