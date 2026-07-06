"""ELMA365 Public API — app item on CREATE, feed message on UPDATE/CLOSE."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins._format import (
    close_comment,
    issue_description,
    issue_title,
    update_comment,
)
from alarm_manager_server.plugins._refs import external_id
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class ElmaTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        namespace: str,
        app_code: str,
        title_field: str = "__name",
        description_field: str = "",
        close_status: str = "",
        context_extra: str = "{}",
        auth_mode: str = "bearer",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip()
        self.namespace = namespace.strip()
        self.app_code = app_code.strip()
        self.title_field = title_field.strip() or "__name"
        self.description_field = description_field.strip()
        self.close_status = close_status.strip()
        self.context_extra = context_extra
        self.auth_mode = auth_mode.strip().lower() or "bearer"
        self._client = http_client
        self._owns_client = http_client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> ElmaTicketHandler | None:
        if not cfg.elma_enabled:
            return None
        return cls(
            base_url=cfg.elma_base_url,
            api_token=cfg.elma_api_token.get_secret_value(),
            namespace=cfg.elma_namespace,
            app_code=cfg.elma_app_code,
            title_field=cfg.elma_title_field,
            description_field=cfg.elma_description_field,
            close_status=cfg.elma_close_status,
            context_extra=cfg.elma_context_extra,
            auth_mode=cfg.elma_auth_mode,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if self.auth_mode == "x-token":
                headers["X-Token"] = self.api_token
            else:
                headers["Authorization"] = f"Bearer {self.api_token}"
            self._client = httpx.Client(
                base_url=f"{self.base_url}/pub/v1",
                headers=headers,
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        context: dict[str, Any] = {self.title_field: issue_title(ctx)}
        if self.description_field:
            context[self.description_field] = issue_description(ctx)
        payload = {"context": _merge_context(context, self.context_extra)}

        resp = self._http().post(
            f"/app/{self.namespace}/{self.app_code}/create",
            json=payload,
        )
        resp.raise_for_status()
        item_id, title = _parse_item_response(resp.json())
        logger.info("ELMA item %s created for %s", title, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=title,
            external_meta={
                "system": "elma",
                "item_id": item_id,
                "title": title,
            },
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        item_id = external_id(ctx, system="elma", id_key="item_id")
        if not item_id:
            return self.on_created(ctx)
        self._feed_message(item_id, update_comment(ctx), title="Alarm Manager — обновление")
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        item_id = external_id(ctx, system="elma", id_key="item_id")
        if not item_id:
            return None
        self._feed_message(item_id, close_comment(ctx), title="Alarm Manager — закрытие")
        if self.close_status:
            resp = self._http().post(
                f"/app/{self.namespace}/{self.app_code}/{item_id}/set-status",
                json={"status": {"code": self.close_status}},
            )
            resp.raise_for_status()
        logger.info("ELMA item %s closed", item_id)
        return None

    def _feed_message(self, item_id: str, body: str, *, title: str) -> None:
        resp = self._http().post(
            f"/feed/{self.namespace}/{self.app_code}/{item_id}/message",
            json={"title": title, "body": body},
        )
        resp.raise_for_status()


def _merge_context(context: dict[str, Any], extra_json: str) -> dict[str, Any]:
    if not extra_json.strip() or extra_json.strip() == "{}":
        return context
    extra = json.loads(extra_json)
    if not isinstance(extra, dict):
        raise ValueError("ELMA_CONTEXT_EXTRA must be a JSON object")
    merged = dict(extra)
    merged.update(context)
    return merged


def _parse_item_response(data: Any) -> tuple[str, str]:
    item = data.get("item") if isinstance(data, dict) else None
    if not isinstance(item, dict) and isinstance(data, dict):
        item = data
    if not isinstance(item, dict):
        raise RuntimeError(f"ELMA create returned unexpected payload: {data!r}")
    item_id = str(item.get("__id") or item.get("id") or "")
    title = str(item.get("__name") or item.get("name") or item_id)
    if not item_id:
        raise RuntimeError(f"ELMA create returned no item id: {data!r}")
    return item_id, title
