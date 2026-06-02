"""Example ticket handler for an external service desk.

Enable:
  TICKET_HANDLERS=examples.ticket_handler_example:ExampleServiceDeskHandler
  # or
  alarm-manager-worker --tickets --ticket-handler examples.ticket_handler_example:ExampleServiceDeskHandler

Replace _create_issue / _update_issue / _close_issue with real API calls.
"""

from __future__ import annotations

import logging
from typing import Any

from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)
from alarm_manager_server.worker.tickets import close_reason_label

logger = logging.getLogger(__name__)


class ExampleServiceDeskHandler(BaseTicketHandler):
    """Template: map CREATE/UPDATE/CLOSE to your ticketing API."""

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        external_id = self._create_issue(ctx)
        return HandlerResult(external_ref=external_id, external_meta={"system": "example-sd"})

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        ref = ctx.ticket.get("external_ref")
        if not ref:
            ref = self._create_issue(ctx)
        else:
            self._update_issue(ref, ctx)
        return HandlerResult(external_ref=ref)

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        ref = ctx.ticket.get("external_ref")
        if ref:
            self._close_issue(ref, ctx)
        return None

    def _create_issue(self, ctx: TicketHandlerContext) -> str:
        payload = _payload(ctx)
        logger.info("SD CREATE %s", payload)
        # return requests.post(...).json()["id"]
        return f"SD-{ctx.event.ticket_id}"

    def _update_issue(self, external_ref: str, ctx: TicketHandlerContext) -> None:
        logger.info("SD UPDATE %s %s", external_ref, _payload(ctx))

    def _close_issue(self, external_ref: str, ctx: TicketHandlerContext) -> None:
        logger.info(
            "SD CLOSE %s reason=%s",
            external_ref,
            close_reason_label(ctx.event.close_reason),
        )


def _payload(ctx: TicketHandlerContext) -> dict[str, Any]:
    snap = ctx.ticket.get("snapshot") or {}
    return {
        "local_ticket_id": ctx.event.ticket_id,
        "title": ctx.event.title or snap.get("title"),
        "group_key": ctx.ticket.get("group_key"),
        "changes": list(ctx.event.changes),
        "close_reason": ctx.event.close_reason,
        "member_ids": snap.get("member_ids"),
        "body": ctx.body_text,
        "external_ref": ctx.ticket.get("external_ref"),
    }
