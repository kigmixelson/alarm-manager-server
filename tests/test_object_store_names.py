import pytest

from alarm_manager_server.saymon.object_store import ObjectStore


@pytest.mark.asyncio
async def test_get_object_refetches_when_cache_is_nameless_stub():
    requested: list[str] = []

    class FakeClient:
        async def get_object(self, obj_id: str):
            requested.append(obj_id)
            return {"_id": obj_id, "name": "Real Parent", "parent_id": []}

    store = ObjectStore(FakeClient())
    store.upsert_parents("parent-1", ["root"])

    obj = await store.get_object("parent-1")
    assert obj is not None
    assert obj.get("name") == "Real Parent"
    assert requested == ["parent-1"]


@pytest.mark.asyncio
async def test_resolve_object_name_uses_api_when_stub_cached():
    class FakeClient:
        async def get_object(self, obj_id: str):
            return {"_id": obj_id, "name": "Router-1", "class_id": 30, "properties": []}

        async def get_object_paths(self, obj_id: str):
            return None

    store = ObjectStore(FakeClient())
    store.upsert_parents("parent-1", [])

    name = await store.resolve_object_name("parent-1")
    assert name == "Router-1"
