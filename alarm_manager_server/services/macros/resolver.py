"""Macro resolver — mirrors useMacroResolver.ts."""

from __future__ import annotations

from alarm_manager_server.services.macros.parser import (
    ParsedMacro,
    object_matches_selector,
    pick_property,
)
from alarm_manager_server.saymon.object_store import ObjectStore


def _selector_key(sel) -> str:
    return f"{sel.kind}={','.join(sorted(sel.values))}"


class MacroResolver:
    def __init__(self, store: ObjectStore, depth: int = 8) -> None:
        self._store = store
        self._depth = depth
        self._selector_match: dict[str, bool] = {}
        self._node_prop: dict[str, str | None] = {}
        self._entity_result: dict[str, str] = {}

    def reset_cache(self) -> None:
        self._selector_match.clear()
        self._node_prop.clear()
        self._entity_result.clear()

    async def resolve_for_entity(self, entity_id: str, macros: list[ParsedMacro]) -> str | None:
        if not entity_id or not macros:
            return None
        parts: list[str] = []
        for macro in macros:
            value = await self._resolve_one(entity_id, macro)
            if value:
                parts.append(value)
        return ", ".join(parts) if parts else None

    async def resolve_for_incidents(
        self,
        incidents: list,
        macros: list[ParsedMacro],
    ) -> dict[str, str | None]:
        self.reset_cache()
        result: dict[str, str | None] = {}
        for inc in incidents:
            if inc.is_synthetic or not inc.entity_id:
                result[inc.id] = None
                continue
            result[inc.id] = await self.resolve_for_entity(inc.entity_id, macros)
        return result

    async def _resolve_one(self, entity_id: str, macro: ParsedMacro) -> str | None:
        cache_key = f"{entity_id}::{macro.raw}"
        if cache_key in self._entity_result:
            return self._entity_result[cache_key]

        chain = await self._store.get_ancestor_chain(entity_id)
        if not chain:
            return None

        limited = chain[: self._depth + 1]
        for node_id in limited:
            value = await self._probe_node(node_id, macro)
            if value is not None:
                self._entity_result[cache_key] = value
                return value

        return None

    async def _probe_node(self, object_id: str, macro: ParsedMacro) -> str | None:
        prop_key = f"{object_id}::{macro.raw}"
        if prop_key in self._node_prop:
            return self._node_prop[prop_key]

        base = self._store.peek_object(object_id)
        if base is None:
            base = await self._store.get_object(object_id)
        if base is None:
            return None

        if base.get("class_id") is None and not base.get("class_name"):
            full = await self._store.fetch_object_full(object_id)
            if not full or (full.get("class_id") is None and not full.get("class_name")):
                self._node_prop[prop_key] = None
                return None
            base = self._store.peek_object(object_id) or full

        sel_key = _selector_key(macro.selector)
        sm_key = f"{object_id}::{sel_key}"
        matches = self._selector_match.get(sm_key)
        if matches is None:
            matches = object_matches_selector(base, macro.selector)
            self._selector_match[sm_key] = matches

        if not matches:
            self._node_prop[prop_key] = None
            return None

        obj = await self._store.get_object_with_props(object_id)
        if not obj:
            self._node_prop[prop_key] = None
            return None

        value = pick_property(obj, macro.property_name, macro.index)
        self._node_prop[prop_key] = value
        return value
