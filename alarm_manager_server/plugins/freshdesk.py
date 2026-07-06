"""Freshdesk — create ticket on CREATE, private note on UPDATE/CLOSE."""

from __future__ import annotations

import logging

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins._format import (
    close_comment,
    issue_description,
    issue_title,
    plain_to_html,
    update_comment,
)
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class FreshdeskTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        requester_email: str,
        priority: int = 2,
        status_open: int = 2,
        status_closed: int = 5,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.requester_email = requester_email
        self.priority = priority
        self.status_open = status_open
        self.status_closed = status_closed
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> FreshdeskTicketHandler | None:
        if not cfg.freshdesk_enabled:
            return None
        return cls(
            base_url=cfg.freshdesk_base_url,
            api_key=cfg.freshdesk_api_key.get_secret_value(),
            requester_email=cfg.freshdesk_requester_email,
            priority=cfg.freshdesk_priority,
            status_open=cfg.freshdesk_status_open,
            status_closed=cfg.freshdesk_status_closed,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.base_url}/api/v2",
                auth=(self.api_key, "X"),
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        payload = {
            "email": self.requester_email,
            "subject": issue_title(ctx),
            "description": plain_to_html(issue_description(ctx)),
            "priority": self.priority,
            "status": self.status_open,
        }
        resp = self._http().post("/tickets", json=payload)
        resp.raise_for_status()
        data = resp.json()
        ticket_id = data.get("id")
        if ticket_id is None:
            raise RuntimeError(f"Freshdesk create returned no id: {resp.text}")
        ref = str(ticket_id)
        logger.info("Freshdesk ticket #%s created for %s", ref, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=f"#{ref}",
            external_meta={"system": "freshdesk", "ticket_id": ref},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        fd_id = _freshdesk_ticket_id(ctx)
        if not fd_id:
            return self.on_created(ctx)
        self._add_note(fd_id, update_comment(ctx))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        fd_id = _freshdesk_ticket_id(ctx)
        if not fd_id:
            return None
        self._add_note(fd_id, close_comment(ctx))
        self._http().put(f"/tickets/{fd_id}", json={"status": self.status_closed})
        logger.info("Freshdesk ticket #%s closed", fd_id)
        return None

    def _add_note(self, ticket_id: str, body: str) -> None:
        resp = self._http().post(
            f"/tickets/{ticket_id}/notes",
            json={"body": body, "private": True},
        )
        resp.raise_for_status()
        logger.info("Freshdesk note added to #%s", ticket_id)


def _freshdesk_ticket_id(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    refs = meta.get("external_refs")
    if isinstance(refs, dict) and refs.get("freshdesk"):
        return str(refs["freshdesk"]).lstrip("#")
    if meta.get("system") == "freshdesk" and meta.get("ticket_id"):
        return str(meta["ticket_id"])
    ref = ctx.ticket.get("external_ref")
    if isinstance(ref, str):
        cleaned = ref.lstrip("#").strip()
        if cleaned.isdigit():
            return cleaned
    return ""
