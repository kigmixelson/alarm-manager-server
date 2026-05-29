from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolvedNode:
    parents: list[str] = field(default_factory=list)
    class_id: str | None = None
    name: str | None = None


class ObjectStore:
    """In-memory object graph with optional SAYMON API backfill."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._objects: dict[str, dict[str, Any]] = {}
        self._paths_miss: set[str] = set()

    def seed_from_incident_owner(self, entity_id: str, owner: dict[str, Any] | None) -> None:
        if not owner:
            return

        owner_id = str(owner.get("_id") or entity_id)
        props = owner.get("properties") if isinstance(owner.get("properties"), list) else []
        self.upsert_object(
            owner_id,
            name=owner.get("name"),
            class_id=owner.get("class_id"),
            properties=props,
            parent_ids=_normalize_parent_ids(owner.get("parent_id")),
        )

        parents_paths = owner.get("_parents") or []
        parents_of: dict[str, set[str]] = {}
        for entry in parents_paths:
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, list):
                continue
            for i in range(len(path) - 1):
                child = str(path[i])
                parent = str(path[i + 1])
                if not child or not parent or len(child) <= 1 or len(parent) <= 1:
                    continue
                parents_of.setdefault(child, set()).add(parent)

        for obj_id, parents in parents_of.items():
            self.upsert_parents(obj_id, list(parents))

        parent_ids = _normalize_parent_ids(owner.get("parent_id"))
        if parent_ids:
            chain = [owner_id, *parent_ids]
            for i, obj_id in enumerate(chain):
                next_parent = chain[i + 1] if i + 1 < len(chain) else None
                self.upsert_parents(obj_id, [next_parent] if next_parent else [])

    def upsert_object(
        self,
        obj_id: str,
        *,
        name: Any = None,
        class_id: Any = None,
        class_name: Any = None,
        properties: list[dict[str, Any]] | None = None,
        parent_ids: list[str] | None = None,
    ) -> None:
        obj_id = str(obj_id)
        existing = self._objects.get(obj_id, {"id": obj_id})
        if name is not None:
            existing["name"] = name
        if class_id is not None:
            existing["class_id"] = class_id
        if class_name is not None:
            existing["class_name"] = class_name
        if properties is not None:
            existing["properties"] = properties
            existing["has_props"] = bool(properties)
        if parent_ids is not None:
            existing["parent_ids"] = parent_ids
            existing["has_parents"] = True
        self._objects[obj_id] = existing

    def upsert_parents(self, obj_id: str, parent_ids: list[str]) -> None:
        obj_id = str(obj_id)
        existing = self._objects.setdefault(obj_id, {"id": obj_id})
        existing["parent_ids"] = parent_ids
        existing["has_parents"] = True

    def peek_object(self, obj_id: str) -> dict[str, Any] | None:
        return self._objects.get(str(obj_id))

    def peek_node(self, obj_id: str) -> ResolvedNode | None:
        obj = self.peek_object(obj_id)
        if not obj:
            return None
        if not obj.get("has_parents") and obj.get("class_id") is None:
            return None
        name = obj.get("name")
        return ResolvedNode(
            parents=list(obj.get("parent_ids") or []),
            class_id=str(obj["class_id"]) if obj.get("class_id") is not None else None,
            name=name if isinstance(name, str) and name else None,
        )

    async def get_object(self, obj_id: str) -> dict[str, Any] | None:
        cached = self.peek_object(obj_id)
        if cached:
            return cached
        if self._client is None:
            return None
        data = await self._client.get_object(obj_id)
        if data:
            self.upsert_object(
                obj_id,
                name=data.get("name"),
                class_id=data.get("class_id"),
                class_name=data.get("class_name"),
                parent_ids=_normalize_parent_ids(data.get("parent_id")),
            )
        return self.peek_object(obj_id)

    async def fetch_object_full(self, obj_id: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        data = await self._client.get_object(obj_id)
        if not data:
            return None
        props = data.get("properties") if isinstance(data.get("properties"), list) else []
        self.upsert_object(
            obj_id,
            name=data.get("name"),
            class_id=data.get("class_id"),
            class_name=data.get("class_name"),
            properties=props,
            parent_ids=_normalize_parent_ids(data.get("parent_id")),
        )
        return self.peek_object(obj_id)

    async def get_object_with_props(self, obj_id: str) -> dict[str, Any] | None:
        obj = self.peek_object(obj_id)
        if obj and obj.get("has_props"):
            return obj
        if self._client is None:
            return obj
        props_data = await self._client.get_object_props(obj_id)
        if props_data:
            props = props_data if isinstance(props_data, list) else props_data.get("properties", [])
            self.upsert_object(obj_id, properties=props if isinstance(props, list) else [])
        return self.peek_object(obj_id)

    async def get_ancestor_chain(self, entity_id: str) -> list[str]:
        entity_id = str(entity_id)
        if not entity_id:
            return []

        chain = await self._try_build_chain(entity_id)
        if chain:
            return chain

        if entity_id in self._paths_miss:
            return []

        if self._client is not None:
            await self._fetch_paths_and_cache(entity_id)
            chain = await self._try_build_chain(entity_id)
            if chain:
                return chain

        return []

    async def _try_build_chain(self, entity_id: str) -> list[str] | None:
        seen: set[str] = set()
        order: list[str] = []
        queue = [entity_id]

        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            order.append(cur)

            obj = self.peek_object(cur)
            if obj is None and self._client is not None:
                obj = await self.get_object(cur)
            if not obj or not obj.get("has_parents"):
                return None

            for parent in obj.get("parent_ids") or []:
                if parent not in seen:
                    queue.append(parent)

        return order if order else None

    async def _fetch_paths_and_cache(self, entity_id: str) -> None:
        if self._client is None:
            return
        data = await self._client.get_object_paths(entity_id)
        if data is None:
            self._paths_miss.add(entity_id)
            return

        paths = data.get("paths") or []
        objects = data.get("objects") or []
        parents_of: dict[str, set[str]] = {}

        for path in paths:
            if not isinstance(path, list):
                continue
            for i in range(len(path) - 1):
                child = str(path[i])
                parent = str(path[i + 1])
                if child and parent:
                    parents_of.setdefault(child, set()).add(parent)

        for obj in objects:
            obj_id = str(obj.get("_id") or obj.get("id") or "")
            if not obj_id:
                continue
            direct = _normalize_parent_ids(obj.get("parent_id"))
            inferred = list(parents_of.get(obj_id, []))
            parent_ids = direct if direct else inferred
            props = obj.get("properties") if isinstance(obj.get("properties"), list) else None
            self.upsert_object(
                obj_id,
                name=obj.get("name"),
                class_id=obj.get("class_id"),
                class_name=obj.get("class_name"),
                properties=props,
                parent_ids=parent_ids if parent_ids else None,
            )


def _normalize_parent_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]
