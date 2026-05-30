"""Main processing pipeline: fetch → group → resolve responsible parties."""

from __future__ import annotations

from alarm_manager_server.config import Settings, settings
from alarm_manager_server.models.incident import GroupingResult, Incident, ProcessedIncident
from alarm_manager_server.services.grouping import (
    build_synthetic_incidents,
    group_by_class,
    group_by_owner,
    merge_groupings,
)
from alarm_manager_server.services.macros import MacroResolver, parse_macro
from alarm_manager_server.saymon.client import SaymonClient
from alarm_manager_server.saymon.object_store import ObjectStore


class AlarmProcessor:
    def __init__(
        self,
        client: SaymonClient | None = None,
        store: ObjectStore | None = None,
        cfg: Settings | None = None,
    ) -> None:
        self.cfg = cfg or settings
        self.client = client or SaymonClient.from_settings(self.cfg)
        self.store = store or ObjectStore(self.client)
        self.macro_resolver = MacroResolver(self.store, depth=self.cfg.macro_depth)

    async def fetch_incidents(self) -> list[Incident]:
        active_raw = await self.client.get_incidents(limit=self.cfg.fetch_limit)
        history_raw = await self.client.get_incident_history(limit=self.cfg.history_limit)

        incidents: list[Incident] = []
        for raw in active_raw:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            inc = Incident.from_api(raw, is_history=False)
            self.store.seed_from_incident_owner(
                inc.entity_id,
                raw.get("owner") if isinstance(raw.get("owner"), dict) else None,
            )
            incidents.append(inc)

        for raw in history_raw:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            inc = Incident.from_api(raw, is_history=True)
            self.store.seed_from_incident_owner(
                inc.entity_id,
                raw.get("owner") if isinstance(raw.get("owner"), dict) else None,
            )
            incidents.append(inc)

        return incidents

    async def compute_grouping(self, incidents: list[Incident]) -> GroupingResult:
        class_ids = await self.client.resolve_class_ids_by_names(self.cfg.group_by_class_names)

        owner_grouping = group_by_owner(incidents, enabled=True)
        class_grouping, synthetic_seeds = group_by_class(
            incidents,
            self.store,
            class_ids,
            self.cfg.group_by_depth,
        )

        incidents_by_id = {inc.id: inc for inc in incidents}
        synthetic_incidents = build_synthetic_incidents(synthetic_seeds, incidents_by_id)

        return merge_groupings(owner_grouping, class_grouping, synthetic_incidents)

    async def resolve_responsible_parties(
        self,
        incidents: list[Incident],
    ) -> dict[str, str | None]:
        parsed = [p for m in self.cfg.macros if (p := parse_macro(m))]
        return await self.macro_resolver.resolve_for_incidents(incidents, parsed)

    async def process(self, incidents: list[Incident] | None = None) -> list[ProcessedIncident]:
        if incidents is None:
            incidents = await self.fetch_incidents()

        grouping = await self.compute_grouping(incidents)

        class_ids = await self.client.resolve_class_ids_by_names(self.cfg.group_by_class_names)
        _, synthetic_seeds = group_by_class(
            incidents, self.store, class_ids, self.cfg.group_by_depth
        )
        incidents_by_id = {inc.id: inc for inc in incidents}
        synthetic_incidents = build_synthetic_incidents(synthetic_seeds, incidents_by_id)

        all_incidents = list(synthetic_incidents) + list(incidents)
        all_responsible = await self.resolve_responsible_parties(
            [i for i in all_incidents if not i.is_synthetic]
        )

        result: list[ProcessedIncident] = []
        for inc in all_incidents:
            parent_title = grouping.parent_title_of.get(inc.id)
            parent_id = grouping.parent_of.get(inc.id)
            child_ids = grouping.children_of.get(inc.id, [])
            has_parent_incident = inc.id in grouping.parent_of
            show_suffix = (
                not inc.is_synthetic
                and not has_parent_incident
                and parent_title
                and parent_title != inc.title
            )
            display_title = f"{inc.title} ({parent_title})" if show_suffix else inc.title

            result.append(
                ProcessedIncident(
                    **inc.model_dump(),
                    avaria_owner=all_responsible.get(inc.id) if not inc.is_synthetic else None,
                    parent_title=parent_title,
                    parent_id=parent_id,
                    child_ids=child_ids,
                    display_title=display_title,
                )
            )

        return result

    def visible_rows(
        self,
        processed: list[ProcessedIncident],
        grouping: GroupingResult,
        expanded: set[str] | None = None,
    ) -> list[tuple[ProcessedIncident, bool]]:
        """Return (incident, is_child) in display order, like Index.tsx visibleRows."""
        expanded = expanded or set()
        by_id = {p.id: p for p in processed}
        out: list[tuple[ProcessedIncident, bool]] = []

        for inc in processed:
            if inc.id in grouping.parent_of:
                continue
            out.append((inc, False))
            children = grouping.children_of.get(inc.id, [])
            if inc.id in expanded:
                for child_id in children:
                    child = by_id.get(child_id)
                    if child:
                        out.append((child, True))

        return out
