from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any

from alarm_manager_server.cache.file_cache import FileCache


@dataclass
class ResolvedNode:
    parents: list[str] = field(default_factory=list)
    class_id: str | None = None
    name: str | None = None


class ObjectStore:
    """In-memory object graph with optional SAYMON API backfill and file cache."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        file_cache: FileCache | None = None,
        object_paths_ttl_sec: int = 0,
    ) -> None:
        self._client = client
        self._file_cache = file_cache
        self._object_paths_ttl_sec = object_paths_ttl_sec
        self._objects: dict[str, dict[str, Any]] = {}
        self._paths_miss: set[str] = set()
        self._name_inflight: dict[str, asyncio.Task[str]] = {}
        self._objects_dirty = False

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
        self._objects_dirty = True

    def upsert_parents(self, obj_id: str, parent_ids: list[str]) -> None:
        obj_id = str(obj_id)
        existing = self._objects.setdefault(obj_id, {"id": obj_id})
        existing["parent_ids"] = parent_ids
        existing["has_parents"] = True
        self._objects_dirty = True

    def export_snapshot(self) -> dict[str, Any]:
        return {
            "objects": copy.deepcopy(self._objects),
            "paths_miss": sorted(self._paths_miss),
        }

    def import_snapshot(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        objects = data.get("objects")
        if isinstance(objects, dict):
            self._objects = copy.deepcopy(objects)
        miss = data.get("paths_miss")
        if isinstance(miss, list):
            self._paths_miss = {str(x) for x in miss}
        self._objects_dirty = False

    def persist_snapshot(self, *, ttl_sec: int) -> None:
        if self._file_cache is None or ttl_sec <= 0:
            return
        self._file_cache.set("objects", self.export_snapshot())
        self._objects_dirty = False

    def load_snapshot_from_cache(self, *, ttl_sec: int) -> bool:
        if self._file_cache is None or ttl_sec <= 0:
            return False
        payload = self._file_cache.get("objects", ttl_sec)
        if not isinstance(payload, dict):
            return False
        self.import_snapshot(payload)
        return True

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
        obj_id = str(obj_id)
        cached = self.peek_object(obj_id)
        if cached and _has_real_name(cached, obj_id):
            return cached
        if self._client is None:
            return cached
        data = await self._client.get_object(obj_id)
        if data:
            self._upsert_from_api_payload(obj_id, data)
        return self.peek_object(obj_id)

    async def resolve_object_name(self, object_id: str) -> str:
        """Load human-readable object name (never return a stub without API fetch)."""
        object_id = str(object_id)
        cached = self.peek_object(object_id)
        if cached and _has_real_name(cached, object_id):
            return str(cached["name"]).strip()

        inflight = self._name_inflight.get(object_id)
        if inflight is not None:
            return await inflight

        task = asyncio.create_task(self._resolve_object_name_impl(object_id))
        self._name_inflight[object_id] = task
        try:
            return await task
        finally:
            if self._name_inflight.get(object_id) is task:
                self._name_inflight.pop(object_id, None)

    async def _resolve_object_name_impl(self, object_id: str) -> str:
        obj = await self.fetch_object_full(object_id)
        if obj and _has_real_name(obj, object_id):
            return str(obj["name"]).strip()

        if self._client is not None and object_id not in self._paths_miss:
            await self._fetch_paths_and_cache(object_id)
            obj = self.peek_object(object_id)
            if obj and _has_real_name(obj, object_id):
                return str(obj["name"]).strip()

        return object_id

    async def prefetch_object_names(self, object_ids: set[str]) -> None:
        ids = {str(i) for i in object_ids if i}
        if not ids:
            return
        await asyncio.gather(*(self.resolve_object_name(i) for i in ids))

    async def fetch_object_full(self, obj_id: str) -> dict[str, Any] | None:
        obj_id = str(obj_id)
        cached = self.peek_object(obj_id)
        if (
            cached
            and _has_real_name(cached, obj_id)
            and cached.get("has_props")
            and cached.get("class_id") is not None
        ):
            return cached
        if self._client is None:
            return cached
        data = await self._client.get_object(obj_id)
        if not data:
            return self.peek_object(obj_id)
        props = data.get("properties") if isinstance(data.get("properties"), list) else []
        self._upsert_from_api_payload(obj_id, data, properties=props)
        return self.peek_object(obj_id)

    async def get_object_with_props(self, obj_id: str) -> dict[str, Any] | None:
        obj = self.peek_object(obj_id)
        if obj and obj.get("has_props") and isinstance(obj.get("properties"), list):
            return obj
        return await self.fetch_object_full(obj_id)

    async def prefetch_ancestor_chains(self, entity_ids: set[str]) -> None:
        ids = {str(i) for i in entity_ids if i}
        if not ids:
            return
        await asyncio.gather(*(self.get_ancestor_chain(i) for i in ids))

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
            if self._client is not None:
                if obj is None or not obj.get("has_parents"):
                    obj = await self.get_object(cur) or obj
            if not obj or not obj.get("has_parents"):
                return None

            for parent in obj.get("parent_ids") or []:
                if parent not in seen:
                    queue.append(parent)

        return order if order else None

    def _load_object_paths_cache(self) -> dict[str, Any]:
        if self._file_cache is None or self._object_paths_ttl_sec <= 0:
            return {}
        payload = self._file_cache.get("object_paths", self._object_paths_ttl_sec)
        return payload if isinstance(payload, dict) else {}

    def _save_object_paths_entry(self, entity_id: str, data: dict[str, Any] | None) -> None:
        if self._file_cache is None or self._object_paths_ttl_sec <= 0:
            return
        bucket = self._load_object_paths_cache()
        bucket[str(entity_id)] = data
        self._file_cache.set("object_paths", bucket)

    def _apply_paths_payload(self, data: dict[str, Any]) -> None:
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
            obj_id = str(obj.get("id") or obj.get("_id") or "")
            if not obj_id:
                continue
            direct = _normalize_parent_ids(obj.get("parent_id"))
            inferred = list(parents_of.get(obj_id, []))
            parent_ids = direct if direct else inferred
            props = obj.get("properties") if isinstance(obj.get("properties"), list) else None
            self.upsert_object(
                obj_id,
                name=_extract_name_from_payload(obj, obj_id),
                class_id=obj.get("class_id"),
                class_name=obj.get("class_name"),
                properties=props,
                parent_ids=parent_ids if parent_ids else None,
            )

    async def _fetch_paths_and_cache(self, entity_id: str) -> None:
        entity_id = str(entity_id)
        if entity_id in self._paths_miss:
            return

        if self._file_cache is not None and self._object_paths_ttl_sec > 0:
            bucket = self._load_object_paths_cache()
            cached = bucket.get(entity_id)
            if cached is not None:
                if cached:
                    self._apply_paths_payload(cached)
                else:
                    self._paths_miss.add(entity_id)
                return

        if self._client is None:
            return
        data = await self._client.get_object_paths(entity_id)
        self._save_object_paths_entry(entity_id, data)
        if data is None:
            self._paths_miss.add(entity_id)
            return

        self._apply_paths_payload(data)

    def _upsert_from_api_payload(
        self,
        obj_id: str,
        data: dict[str, Any],
        *,
        properties: list[dict[str, Any]] | None = None,
    ) -> None:
        props = properties
        if props is None and isinstance(data.get("properties"), list):
            props = data["properties"]
        self.upsert_object(
            obj_id,
            name=_extract_name_from_payload(data, obj_id),
            class_id=data.get("class_id"),
            class_name=data.get("class_name"),
            properties=props,
            parent_ids=_normalize_parent_ids(data.get("parent_id")),
        )


def _has_real_name(obj: dict[str, Any] | None, obj_id: str) -> bool:
    name = _extract_name_from_payload(obj, obj_id) if obj else None
    return name is not None


def _extract_name_from_payload(data: dict[str, Any] | None, obj_id: str) -> str | None:
    if not data:
        return None
    for key in ("name", "displayName", "caption", "title"):
        value = data.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped != str(obj_id):
                return stripped
    return None


def _normalize_parent_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]
