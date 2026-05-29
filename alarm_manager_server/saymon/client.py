"""HTTP client for SAYMON REST API."""

from __future__ import annotations

from typing import Any

import httpx


class SaymonClient:
    def __init__(self, base_api_url: str, timeout: float = 30.0) -> None:
        self._base = base_api_url.rstrip("/")
        self._timeout = timeout

    async def get_incidents(self, limit: int = 200) -> list[dict[str, Any]]:
        return await self._get_json(f"/incidents?limit={limit}&skip=0&owner=true")

    async def get_incident_history(self, limit: int = 3000) -> list[dict[str, Any]]:
        return await self._get_json(
            f"/incident-history?inverse=true&limit={limit}&skip=0&owner=false"
        )

    async def get_classes(self) -> list[dict[str, Any]]:
        return await self._get_json("/classes")

    async def get_object(self, obj_id: str) -> dict[str, Any] | None:
        try:
            return await self._get_json(f"/objects/{obj_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_object_props(self, obj_id: str) -> Any:
        try:
            return await self._get_json(f"/objects/{obj_id}/props")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def get_object_paths(self, obj_id: str) -> dict[str, Any] | None:
        try:
            return await self._get_json(f"/objects/{obj_id}/paths")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def resolve_class_ids_by_names(self, names: list[str]) -> set[str]:
        classes = await self.get_classes()
        name_index: dict[str, str] = {}
        for cls in classes:
            cls_name = cls.get("name")
            cls_id = cls.get("id")
            if cls_name is not None and cls_id is not None:
                name_index[str(cls_name).casefold()] = str(cls_id)

        result: set[str] = set()
        for name in names:
            cls_id = name_index.get(name.casefold())
            if cls_id:
                result.add(cls_id)
        return result

    async def _get_json(self, path: str) -> Any:
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout) as client:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()
