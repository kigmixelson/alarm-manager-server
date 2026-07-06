"""Bitrix24 incoming webhook — task on CREATE, comment on UPDATE, complete on CLOSE."""

from __future__ import annotations

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


class Bitrix24TicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        webhook_url: str,
        responsible_id: int,
        created_by: int = 0,
        group_id: int = 0,
        comment_author_id: int = 0,
        complete_on_close: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.webhook_url = _normalize_webhook_url(webhook_url)
        self.responsible_id = responsible_id
        self.created_by = created_by
        self.group_id = group_id
        self.comment_author_id = comment_author_id
        self.complete_on_close = complete_on_close
        self._client = http_client
        self._owns_client = http_client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> Bitrix24TicketHandler | None:
        if not cfg.bitrix24_enabled:
            return None
        return cls(
            webhook_url=cfg.bitrix24_webhook_url,
            responsible_id=cfg.bitrix24_responsible_id,
            created_by=cfg.bitrix24_created_by,
            group_id=cfg.bitrix24_group_id,
            comment_author_id=cfg.bitrix24_comment_author_id,
            complete_on_close=cfg.bitrix24_complete_on_close,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.webhook_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        fields: dict[str, Any] = {
            "TITLE": issue_title(ctx),
            "DESCRIPTION": issue_description(ctx),
            "RESPONSIBLE_ID": self.responsible_id,
        }
        if self.created_by > 0:
            fields["CREATED_BY"] = self.created_by
        if self.group_id > 0:
            fields["GROUP_ID"] = self.group_id

        data = _unwrap_result(self._call("tasks.task.add", {"fields": fields}))
        task = data.get("task") if isinstance(data, dict) else None
        if not isinstance(task, dict):
            raise RuntimeError(f"Bitrix24 create returned no task: {data!r}")
        task_id = str(task.get("id") or "")
        if not task_id:
            raise RuntimeError(f"Bitrix24 create returned no task id: {data!r}")
        logger.info("Bitrix24 task #%s created for %s", task_id, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=f"#{task_id}",
            external_meta={"system": "bitrix24", "task_id": task_id},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        task_id = external_id(ctx, system="bitrix24", id_key="task_id", ref_is_numeric=True)
        if not task_id:
            return self.on_created(ctx)
        self._add_comment(task_id, update_comment(ctx))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        task_id = external_id(ctx, system="bitrix24", id_key="task_id", ref_is_numeric=True)
        if not task_id:
            return None
        self._add_comment(task_id, close_comment(ctx))
        if self.complete_on_close:
            self._call("tasks.task.complete", {"taskId": int(task_id)})
        logger.info("Bitrix24 task #%s closed", task_id)
        return None

    def _add_comment(self, task_id: str, message: str) -> None:
        fields: dict[str, Any] = {"POST_MESSAGE": message}
        if self.comment_author_id > 0:
            fields["AUTHOR_ID"] = self.comment_author_id
        self._call(
            "task.commentitem.add",
            {"TASKID": int(task_id), "FIELDS": fields},
        )

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        resp = self._http().post(method, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(
                f"Bitrix24 {method} error: {body.get('error_description') or body.get('error')}"
            )
        return body.get("result") if isinstance(body, dict) and "result" in body else body


def _normalize_webhook_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    return f"{normalized}/"


def _unwrap_result(data: Any) -> Any:
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data
