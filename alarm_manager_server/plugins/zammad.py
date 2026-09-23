"""Zammad REST API — ticket on CREATE, article on UPDATE/CLOSE."""

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
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class ZammadTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        group: str = "",
        group_id: int = 0,
        customer: str = "",
        priority: str = "2 normal",
        state_open: str = "new",
        state_closed: str = "closed",
        article_internal: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip()
        self.group = group.strip()
        self.group_id = group_id
        self.customer = customer.strip()
        self.priority = priority.strip() or "2 normal"
        self.state_open = state_open.strip() or "new"
        self.state_closed = state_closed.strip() or "closed"
        self.article_internal = article_internal
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> ZammadTicketHandler | None:
        if not cfg.zammad_enabled:
            return None
        return cls(
            base_url=cfg.zammad_base_url,
            api_token=cfg.zammad_api_token.get_secret_value(),
            group=cfg.zammad_group,
            group_id=cfg.zammad_group_id,
            customer=cfg.zammad_customer,
            priority=cfg.zammad_priority,
            state_open=cfg.zammad_state_open,
            state_closed=cfg.zammad_state_closed,
            article_internal=cfg.zammad_article_internal,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.base_url}/api/v1",
                headers={
                    "Authorization": f"Token token={self.api_token}",
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
        title = issue_title(ctx)
        body = issue_description(ctx)
        payload: dict[str, Any] = {
            "title": title,
            "customer": self.customer,
            "state": self.state_open,
            "priority": self.priority,
            "article": {
                "subject": title,
                "body": body,
                "type": "note",
                "internal": self.article_internal,
            },
        }
        if self.group_id > 0:
            payload["group_id"] = self.group_id
        elif self.group:
            payload["group"] = self.group

        resp = self._http().post("/tickets", json=payload)
        resp.raise_for_status()
        data = resp.json()
        ticket_id = data.get("id")
        number = str(data.get("number") or ticket_id or "")
        if ticket_id is None:
            raise RuntimeError(f"Zammad create returned no id: {resp.text}")
        logger.info("Zammad ticket #%s created for %s", number, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=f"#{number}",
            external_meta={
                "system": "zammad",
                "ticket_id": str(ticket_id),
                "number": str(number),
            },
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        ticket_id = _zammad_ticket_id(ctx)
        if not ticket_id:
            return self.on_created(ctx)
        self._add_article(ticket_id, update_comment(ctx), subject="Alarm Manager — обновление")
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        ticket_id = _zammad_ticket_id(ctx)
        if not ticket_id:
            return None
        payload: dict[str, Any] = {
            "article": {
                "subject": "Alarm Manager — закрытие",
                "body": close_comment(ctx),
                "type": "note",
                "internal": self.article_internal,
            },
        }
        if self.state_closed:
            payload["state"] = self.state_closed
        resp = self._http().put(f"/tickets/{ticket_id}", json=payload)
        resp.raise_for_status()
        logger.info("Zammad ticket #%s closed", ticket_id)
        return None

    def _add_article(self, ticket_id: str, body: str, *, subject: str) -> None:
        resp = self._http().post(
            "/ticket_articles",
            json={
                "ticket_id": int(ticket_id),
                "subject": subject,
                "body": body,
                "type": "note",
                "internal": self.article_internal,
            },
        )
        resp.raise_for_status()


def _zammad_ticket_id(ctx: TicketHandlerContext) -> str:
    """Prefer internal id from meta (number ≠ id in Zammad)."""
    meta = ctx.ticket.get("external_meta") or {}
    if meta.get("ticket_id"):
        return str(meta["ticket_id"]).strip()
    return ""
