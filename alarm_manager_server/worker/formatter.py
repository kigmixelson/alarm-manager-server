"""Build console output groups from /process API response."""

from __future__ import annotations

from dataclasses import dataclass

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident


@dataclass(frozen=True)
class IncidentGroup:
    name: str
    links: list[str]


def incident_link(incident_id: str, cfg: Settings) -> str:
    return cfg.incident_link_template.format(
        id=incident_id,
        saymon_base_url=cfg.saymon_base_url.rstrip("/"),
    )


def build_groups(
    incidents: list[ProcessedIncident],
    grouping: GroupingResult,
    cfg: Settings,
) -> list[IncidentGroup]:
    """Top-level rows only; children are folded into their parent group."""
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
        name = inc.display_title or inc.title

        if child_ids:
            member_ids = list(child_ids) if inc.is_synthetic else [inc.id, *child_ids]
        else:
            member_ids = [inc.id]

        links = [incident_link(mid, cfg) for mid in member_ids]
        groups.append(IncidentGroup(name=name, links=links))

    return groups


def format_groups(groups: list[IncidentGroup]) -> str:
    blocks: list[str] = []
    for group in groups:
        lines = [group.name, *group.links]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
