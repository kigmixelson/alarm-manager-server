import pytest

from alarm_manager_server.services.macros.parser import parse_macro
from alarm_manager_server.services.macros.resolver import MacroResolver
from alarm_manager_server.saymon.object_store import ObjectStore


@pytest.mark.asyncio
async def test_macro_resolver_uses_owner_id_when_entity_id_missing():
    macro = parse_macro("{{parent[class.id=30].properties[17. Ответственный]}}")
    assert macro is not None

    class FakeClient:
        async def get_object(self, obj_id: str):
            if obj_id == "child":
                return {"id": "child", "name": "Child", "class_id": 20, "parent_id": ["host"]}
            if obj_id == "host":
                return {
                    "id": "host",
                    "name": "Host-1",
                    "class_id": 30,
                    "parent_id": [],
                    "properties": [{"name": "17. Ответственный", "value": "Ivanov"}],
                }
            return None

        async def get_object_paths(self, obj_id: str):
            return {
                "paths": [["child", "host"]],
                "objects": [
                    {"id": "child", "name": "Child", "class_id": 20, "parent_id": ["host"]},
                    {
                        "id": "host",
                        "name": "Host-1",
                        "class_id": 30,
                        "parent_id": [],
                        "properties": [{"name": "17. Ответственный", "value": "Ivanov"}],
                    },
                ],
            }

    store = ObjectStore(FakeClient())
    store.upsert_parents("child", ["host"])

    class Owner:
        id = "child"

    class Inc:
        id = "inc1"
        entity_id = ""
        owner = Owner()
        is_synthetic = False

    resolver = MacroResolver(store, depth=8)
    out = await resolver.resolve_for_incidents([Inc()], [macro])
    assert out["inc1"] == "Ivanov"


@pytest.mark.asyncio
async def test_macro_resolver_fetches_paths_and_resolves_property():
    macro = parse_macro("{{parent[class.id=30].properties[17. Ответственный]}}")
    assert macro is not None

    class FakeClient:
        async def get_object(self, obj_id: str):
            if obj_id == "child":
                return {"id": "child", "name": "Child", "class_id": 20, "parent_id": ["host"]}
            if obj_id == "host":
                return {
                    "id": "host",
                    "name": "Host-1",
                    "class_id": 30,
                    "parent_id": [],
                    "properties": [{"name": "17. Ответственный", "value": "Ivanov"}],
                }
            return None

        async def get_object_paths(self, obj_id: str):
            return {
                "paths": [["child", "host"]],
                "objects": [
                    {"id": "child", "name": "Child", "class_id": 20, "parent_id": ["host"]},
                    {
                        "id": "host",
                        "name": "Host-1",
                        "class_id": 30,
                        "parent_id": [],
                        "properties": [{"name": "17. Ответственный", "value": "Ivanov"}],
                    },
                ],
            }

    store = ObjectStore(FakeClient())
    store.upsert_parents("child", ["host"])

    class Inc:
        id = "inc1"
        entity_id = "child"
        is_synthetic = False

    resolver = MacroResolver(store, depth=8)
    out = await resolver.resolve_for_incidents([Inc()], [macro])
    assert out["inc1"] == "Ivanov"


@pytest.mark.asyncio
async def test_macro_resolver_does_not_cache_failed_lookup():
    macro = parse_macro("{{parent[class.id=30].properties[17. Ответственный]}}")
    assert macro is not None

    class FakeClient:
        calls = 0

        async def get_object_paths(self, obj_id: str):
            return None

        async def get_object(self, obj_id: str):
            return {"id": obj_id, "name": obj_id, "parent_id": []}

    store = ObjectStore(FakeClient())
    resolver = MacroResolver(store)

    class Inc:
        id = "inc1"
        entity_id = "e1"
        is_synthetic = False

    first = await resolver.resolve_for_incidents([Inc()], [macro])
    second = await resolver.resolve_for_incidents([Inc()], [macro])
    assert first["inc1"] is None
    assert second["inc1"] is None
