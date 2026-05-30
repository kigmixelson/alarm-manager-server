"""Class-ancestor grouping — mirrors useIncidentGrouping.ts."""

from __future__ import annotations

from alarm_manager_server.models.incident import (
    GroupingResult,
    Incident,
    SyntheticGroupSeed,
    incident_object_id,
    is_active,
)
from alarm_manager_server.saymon.object_store import ObjectStore, ResolvedNode


def _build_incident_by_entity(incidents: list[Incident]) -> dict[str, str]:
    by_id = {a.id: a for a in incidents}
    result: dict[str, str] = {}
    for inc in incidents:
        object_id = incident_object_id(inc)
        if not object_id:
            continue
        existing_id = result.get(object_id)
        if not existing_id:
            result[object_id] = inc.id
            continue
        existing = by_id.get(existing_id)
        if existing and not is_active(existing) and is_active(inc):
            result[object_id] = inc.id
    return result


def group_by_class(
    incidents: list[Incident],
    store: ObjectStore,
    group_by_class_ids: set[str],
    depth: int,
) -> tuple[GroupingResult, list[SyntheticGroupSeed]]:
    if not incidents or not group_by_class_ids or depth <= 0:
        return GroupingResult(), []

    children_of: dict[str, list[str]] = {}
    parent_of: dict[str, str] = {}
    parent_title_of: dict[str, str] = {}
    synthetic_by_entity: dict[str, dict] = {}

    incident_by_entity = _build_incident_by_entity(incidents)
    by_id = {x.id: x for x in incidents}

    for inc in incidents:
        entity = incident_object_id(inc)
        if not entity:
            continue

        frontier = [entity]
        visited: set[str] = {entity}
        found = False

        for _gen in range(1, depth + 1):
            if not frontier or found:
                break

            next_level: list[str] = []
            for node_id in frontier:
                node = store.peek_node(node_id)
                if node is None:
                    continue
                for parent_id in node.parents:
                    if parent_id not in visited:
                        visited.add(parent_id)
                        next_level.append(parent_id)

            for cand in next_level:
                cand_node = store.peek_node(cand)
                if cand_node is None:
                    continue
                if not cand_node.class_id or cand_node.class_id not in group_by_class_ids:
                    continue

                if cand_node.name:
                    parent_title_of[inc.id] = cand_node.name

                cand_incident_id = incident_by_entity.get(cand)
                if cand_incident_id and cand_incident_id != inc.id:
                    parent_avaria = by_id.get(cand_incident_id)
                    child_active = is_active(inc)
                    parent_active = is_active(parent_avaria) if parent_avaria else True
                    if not (child_active and not parent_active):
                        parent_of[inc.id] = cand_incident_id
                        children_of.setdefault(cand_incident_id, []).append(inc.id)
                elif not cand_incident_id:
                    seed = synthetic_by_entity.get(cand)
                    if seed:
                        seed["child_ids"].add(inc.id)
                    else:
                        synthetic_by_entity[cand] = {
                            "name": cand_node.name or cand,
                            "child_ids": {inc.id},
                        }
                found = True
                break

            frontier = next_level

    synthetic_seeds: list[SyntheticGroupSeed] = []
    for entity_id, data in synthetic_by_entity.items():
        child_ids = list(data["child_ids"])
        if len(child_ids) < 2:
            continue
        synthetic_seeds.append(
            SyntheticGroupSeed(
                entity_id=entity_id,
                name=data["name"],
                child_ids=child_ids,
            )
        )

    return (
        GroupingResult(
            children_of=children_of,
            parent_of=parent_of,
            parent_title_of=parent_title_of,
        ),
        synthetic_seeds,
    )
