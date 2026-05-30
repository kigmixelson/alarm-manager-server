"""Display title for incident owner including direct parent object(s)."""

from __future__ import annotations

from alarm_manager_server.models.incident import Incident
from alarm_manager_server.saymon.object_store import ObjectStore


async def build_owner_display_title(incident: Incident, store: ObjectStore) -> str:
    """Owner object name plus parent_id suffix for console/API output."""
    base = incident.title
    owner = incident.owner
    if owner is None:
        return base

    parent_ids = [pid for pid in owner.parent_id if pid]
    if not parent_ids:
        return base

    if len(parent_ids) == 1:
        parent_name = await store.resolve_object_name(parent_ids[0])
        return f"{base} ({parent_name})"

    count = len(parent_ids)
    return f"{base} (влияет на {count} родительских объектов)"
