"""HTTP client for SAYMON REST API with session authentication."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.saymon.auth import SaymonAuthError, csrf_headers, require_session_cookies


class SaymonClient:
    def __init__(
        self,
        base_api_url: str,
        *,
        login: str,
        password: str,
        saymon_base_url: str,
        auth_redirect_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_api_url.rstrip("/")
        self._saymon_base = saymon_base_url.rstrip("/")
        self._login = login
        self._password = password
        self._auth_redirect_url = (auth_redirect_url or "").strip() or None
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._authenticated = False
        self._auth_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, cfg: Settings) -> SaymonClient:
        return cls(
            cfg.api_url,
            login=cfg.saymon_login,
            password=cfg.saymon_password.get_secret_value(),
            saymon_base_url=cfg.saymon_base_url,
            auth_redirect_url=cfg.saymon_auth_redirect_url,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._authenticated = False

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

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                follow_redirects=True,
            )
        await self._ensure_authenticated()
        return self._client

    async def _ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        if not self._login or not self._password:
            raise SaymonAuthError("SAYMON_LOGIN and SAYMON_PASSWORD must be set")

        async with self._auth_lock:
            if self._authenticated:
                return
            client = self._client
            if client is None:
                client = httpx.AsyncClient(
                    base_url=self._base,
                    timeout=self._timeout,
                    follow_redirects=True,
                )
                self._client = client

            response = await client.post(
                "/users/session",
                json={"login": self._login, "password": self._password},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if response.status_code != 200:
                raise SaymonAuthError(
                    f"POST /users/session failed with status {response.status_code}"
                )

            require_session_cookies(client.cookies)

            if self._auth_redirect_url:
                redirect_target = self._resolve_redirect_url(self._auth_redirect_url)
                redirect_response = await client.get(
                    redirect_target,
                    headers=csrf_headers(client.cookies),
                )
                redirect_response.raise_for_status()

            self._authenticated = True

    def _resolve_redirect_url(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("/"):
            return f"{self._saymon_base}{url}"
        return f"{self._saymon_base}/{url}"

    def _request_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        return csrf_headers(client.cookies)

    async def _get_json(self, path: str) -> Any:
        client = await self._get_client()
        response = await client.get(path, headers=self._request_headers(client))
        response.raise_for_status()
        return response.json()
