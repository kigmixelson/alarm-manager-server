"""Main processing pipeline: fetch → group → resolve responsible parties."""

from __future__ import annotations

from alarm_manager_server.config import Settings, settings
from alarm_manager_server.models.incident import (
    GroupingResult,
    Incident,
    ProcessedIncident,
    SyntheticGroupSeed,
    get_opened_at_ms,
    incident_object_id,
)
from alarm_manager_server.services.grouping import (
    build_synthetic_incidents,
    group_by_class,
    group_by_owner,
    merge_groupings,
)
from alarm_manager_server.services.macros import MacroResolver, parse_macro
from alarm_manager_server.services.owner_display import (
    _incident_owner_base_name,
    build_group_display_title,
)
from alarm_manager_server.saymon.client import SaymonClient
from alarm_manager_server.saymon.object_store import ObjectStore


def merge_active_and_history_incidents(
    active: list[Incident],
    history: list[Incident],
) -> list[Incident]:
    """Deduplicate by incident id; active records win over history for the same id."""
    by_id: dict[str, Incident] = {}
    for inc in history:
        by_id[inc.id] = inc
    for inc in active:
        by_id[inc.id] = inc
    return sorted(by_id.values(), key=get_opened_at_ms, reverse=True)


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
        self._state_labels: dict[str, str] | None = None

    async def get_state_labels(self) -> dict[str, str]:
        if self._state_labels is not None:
            return self._state_labels
        labels: dict[str, str] = {}
        try:
            levels = await self.client.get_incident_levels()
            for level in levels:
                level_id = level.get("id")
                name = level.get("name")
                if level_id is not None and name:
                    labels[str(level_id)] = str(name)
        except Exception:
            pass
        self._state_labels = labels
        return labels

    def status_label_for(self, status: int | str, labels: dict[str, str]) -> str:
        key = str(status)
        return labels.get(key, key)

    async def fetch_incidents(self) -> list[Incident]:
        active_raw = await self.client.get_incidents(
            limit=self.cfg.fetch_limit,
            page_size=self.cfg.fetch_page_size,
        )
        history_raw = await self.client.get_incident_history(
            limit=self.cfg.history_limit,
            page_size=self.cfg.fetch_page_size,
        )

        active: list[Incident] = []
        for raw in active_raw:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            inc = Incident.from_api(raw, is_history=False)
            self.store.seed_from_incident_owner(
                incident_object_id(inc),
                raw.get("owner") if isinstance(raw.get("owner"), dict) else None,
            )
            active.append(inc)

        history: list[Incident] = []
        for raw in history_raw:
            if not isinstance(raw, dict) or raw.get("id") is None:
                continue
            inc = Incident.from_api(raw, is_history=True)
            self.store.seed_from_incident_owner(
                incident_object_id(inc),
                raw.get("owner") if isinstance(raw.get("owner"), dict) else None,
            )
            history.append(inc)

        return merge_active_and_history_incidents(active, history)

    async def compute_grouping(self, incidents: list[Incident]) -> GroupingResult:
        class_ids = await self.client.resolve_class_ids_by_names(self.cfg.group_by_class_names)

        owner_grouping = group_by_owner(incidents, enabled=True)
        class_grouping, synthetic_seeds = group_by_class(
            incidents,
            self.store,
            class_ids,
            self.cfg.group_by_depth,
        )
        await self._enrich_synthetic_seed_names(synthetic_seeds)

        incidents_by_id = {inc.id: inc for inc in incidents}
        synthetic_incidents = build_synthetic_incidents(synthetic_seeds, incidents_by_id)

        return merge_groupings(owner_grouping, class_grouping, synthetic_incidents)

    async def resolve_responsible_parties(
        self,
        incidents: list[Incident],
    ) -> dict[str, str | None]:
        parsed = [p for m in self.cfg.macros if (p := parse_macro(m))]
        if not parsed:
            return {inc.id: None for inc in incidents}
        targets = [inc for inc in incidents if not inc.is_synthetic and incident_object_id(inc)]
        await self.store.prefetch_ancestor_chains({incident_object_id(inc) for inc in targets})
        return await self.macro_resolver.resolve_for_incidents(targets, parsed)

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
        macro_targets = [i for i in all_incidents if not i.is_synthetic]
        all_responsible = await self.resolve_responsible_parties(macro_targets)

        await self._prefetch_display_names(all_incidents)
        state_labels = await self.get_state_labels()

        result: list[ProcessedIncident] = []
        for inc in all_incidents:
            result.append(
                await self._to_processed_incident(
                    inc,
                    grouping,
                    all_responsible.get(inc.id) if not inc.is_synthetic else None,
                    state_labels,
                )
            )

        return result

    async def _enrich_synthetic_seed_names(self, seeds: list[SyntheticGroupSeed]) -> None:
        if not seeds:
            return
        await self.store.prefetch_object_names({seed.entity_id for seed in seeds})
        for seed in seeds:
            if not seed.name or seed.name == seed.entity_id:
                seed.name = await self.store.resolve_object_name(seed.entity_id)

    async def _prefetch_display_names(self, incidents: list[Incident]) -> None:
        object_ids: set[str] = set()
        for inc in incidents:
            if inc.is_synthetic:
                if inc.entity_id:
                    object_ids.add(inc.entity_id)
                continue
            if not inc.is_synthetic:
                object_id = incident_object_id(inc)
                if object_id:
                    object_ids.add(object_id)
            if inc.owner and len(inc.owner.parent_id) == 1 and inc.owner.parent_id[0]:
                object_ids.add(inc.owner.parent_id[0])
        await self.store.prefetch_object_names(object_ids)

    async def _to_processed_incident(
        self,
        inc: Incident,
        grouping: GroupingResult,
        avaria_owner: str | None,
        state_labels: dict[str, str],
    ) -> ProcessedIncident:
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
        owner_display_title = await build_group_display_title(inc, self.store)
        object_display_name = await _incident_owner_base_name(inc, self.store)

        return ProcessedIncident(
            **inc.model_dump(),
            avaria_owner=avaria_owner,
            parent_title=parent_title,
            parent_id=parent_id,
            child_ids=child_ids,
            display_title=display_title,
            owner_display_title=owner_display_title,
            object_display_name=object_display_name,
            status_label=self.status_label_for(inc.status, state_labels)
            if not inc.is_synthetic
            else "",
        )

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
