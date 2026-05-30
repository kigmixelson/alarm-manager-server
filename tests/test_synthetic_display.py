import pytest

from alarm_manager_server.models.incident import Incident, SyntheticGroupSeed
from alarm_manager_server.services.owner_display import build_group_display_title
from alarm_manager_server.saymon.object_store import ObjectStore


@pytest.mark.asyncio
async def test_synthetic_group_title_resolves_entity_id():
    class FakeClient:
        async def get_object(self, obj_id: str):
            return {
                "id": obj_id,
                "name": "PSU.#1@R2.saymon",
                "class_id": 20,
                "properties": [],
            }

        async def get_object_paths(self, obj_id: str):
            return None

    store = ObjectStore(FakeClient())
    inc = Incident(
        id="__synth__67cb1f06120ab073c5adb78c",
        title="67cb1f06120ab073c5adb78c",
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        is_synthetic=True,
        entity_id="67cb1f06120ab073c5adb78c",
    )
    title = await build_group_display_title(inc, store)
    assert title == "PSU.#1@R2.saymon"


@pytest.mark.asyncio
async def test_synthetic_seed_name_enrichment_pattern():
    class FakeClient:
        async def get_object(self, obj_id: str):
            return {"id": obj_id, "name": "Host-A", "properties": [], "class_id": 1}

        async def get_object_paths(self, obj_id: str):
            return None

    store = ObjectStore(FakeClient())
    seed = SyntheticGroupSeed(
        entity_id="67cb1f06120ab073c5adb78c",
        name="67cb1f06120ab073c5adb78c",
        child_ids=["a", "b"],
    )
    if not seed.name or seed.name == seed.entity_id:
        seed.name = await store.resolve_object_name(seed.entity_id)
    assert seed.name == "Host-A"
