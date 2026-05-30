import pytest

from alarm_manager_server.models.incident import Incident, IncidentOwner
from alarm_manager_server.services.owner_display import build_owner_display_title
from alarm_manager_server.saymon.object_store import ObjectStore


def _incident(*, title: str = "Child-svc", parent_ids: list[str] | None = None) -> Incident:
    owner = IncidentOwner(
        _id="owner-1",
        name=title,
        parent_id=parent_ids or [],
    )
    return Incident(
        id="inc-1",
        title=title,
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        owner=owner,
        entity_id="owner-1",
    )


@pytest.mark.asyncio
async def test_stub_owner_name_is_resolved_via_api():
    class FakeClient:
        async def get_object(self, obj_id: str):
            return {"_id": obj_id, "name": "PSU.#1@R2", "properties": [], "class_id": 1}

        async def get_object_paths(self, obj_id: str):
            return None

    store = ObjectStore(FakeClient())
    inc = Incident(
        id="inc-99",
        title="67cb1f06120ab073c5adb78c",
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        entity_id="67cb1f06120ab073c5adb78c",
        owner=IncidentOwner(_id="67cb1f06120ab073c5adb78c", name="67cb1f06120ab073c5adb78c"),
    )
    text = await build_owner_display_title(inc, store)
    assert text == "PSU.#1@R2"
    assert "67cb1f06120ab073c5adb78c" not in text
    assert "inc-99" not in text


@pytest.mark.asyncio
async def test_no_parents_returns_owner_name_only():
    store = ObjectStore()
    assert await build_owner_display_title(_incident(parent_ids=[]), store) == "Child-svc"


@pytest.mark.asyncio
async def test_single_parent_shows_parent_name():
    class FakeClient:
        async def get_object(self, obj_id: str):
            return {"_id": obj_id, "name": "Host-1", "properties": [], "class_id": 1}

        async def get_object_paths(self, obj_id: str):
            return None

    store = ObjectStore(FakeClient())
    text = await build_owner_display_title(_incident(parent_ids=["parent-1"]), store)
    assert text == "Child-svc (Host-1)"


@pytest.mark.asyncio
async def test_multiple_parents_shows_count_phrase():
    store = ObjectStore()
    text = await build_owner_display_title(
        _incident(parent_ids=["p1", "p2", "p3"]),
        store,
    )
    assert "влияет на 3 родительских" in text
