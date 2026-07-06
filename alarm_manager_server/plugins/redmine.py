"""Redmine — create issue on CREATE, journal notes on UPDATE/CLOSE."""

from __future__ import annotations

import logging

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins._format import (
    close_comment,
    issue_description,
    issue_title,
    update_comment,
)
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class RedmineTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        project_id: str,
        tracker_id: int = 1,
        status_closed_id: int = 0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self.tracker_id = tracker_id
        self.status_closed_id = status_closed_id
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> RedmineTicketHandler | None:
        if not cfg.redmine_enabled:
            return None
        return cls(
            base_url=cfg.redmine_base_url,
            api_key=cfg.redmine_api_key.get_secret_value(),
            project_id=cfg.redmine_project_id,
            tracker_id=cfg.redmine_tracker_id,
            status_closed_id=cfg.redmine_status_closed_id,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={
                    "X-Redmine-API-Key": self.api_key,
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
        payload = {
            "issue": {
                "project_id": _coerce_project_id(self.project_id),
                "tracker_id": self.tracker_id,
                "subject": issue_title(ctx),
                "description": issue_description(ctx),
            }
        }
        resp = self._http().post("/issues.json", json=payload)
        resp.raise_for_status()
        issue = resp.json().get("issue") or {}
        issue_id = issue.get("id")
        if issue_id is None:
            raise RuntimeError(f"Redmine create returned no id: {resp.text}")
        ref = str(issue_id)
        logger.info("Redmine issue #%s created for %s", ref, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=f"#{ref}",
            external_meta={"system": "redmine", "issue_id": ref},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        issue_id = _redmine_issue_id(ctx)
        if not issue_id:
            return self.on_created(ctx)
        self._update_issue(issue_id, notes=update_comment(ctx))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        issue_id = _redmine_issue_id(ctx)
        if not issue_id:
            return None
        fields: dict = {"notes": close_comment(ctx)}
        if self.status_closed_id > 0:
            fields["status_id"] = self.status_closed_id
        self._update_issue(issue_id, **fields)
        return None

    def _update_issue(self, issue_id: str, **fields) -> None:
        payload = {"issue": fields}
        resp = self._http().put(f"/issues/{issue_id}.json", json=payload)
        resp.raise_for_status()
        logger.info("Redmine issue #%s updated", issue_id)


def _coerce_project_id(value: str) -> str | int:
    if value.isdigit():
        return int(value)
    return value


def _redmine_issue_id(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    refs = meta.get("external_refs")
    if isinstance(refs, dict) and refs.get("redmine"):
        return str(refs["redmine"]).lstrip("#")
    if meta.get("system") == "redmine" and meta.get("issue_id"):
        return str(meta["issue_id"])
    ref = ctx.ticket.get("external_ref")
    if isinstance(ref, str):
        cleaned = ref.lstrip("#").strip()
        if cleaned.isdigit():
            return cleaned
    return ""
