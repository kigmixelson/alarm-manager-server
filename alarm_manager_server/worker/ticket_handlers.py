"""Pluggable ticket handlers for external systems (service desk, webhooks, etc.)."""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from alarm_manager_server.worker.tickets import (
    TicketEvent,
    TicketStore,
    _format_group_block,
    close_reason_label,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HandlerResult:
    """Optional outcome after registering/updating an external ticket."""

    external_ref: str | None = None
    external_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TicketHandlerContext:
    """Context passed to handlers after local ticket state is persisted."""

    event: TicketEvent
    ticket: dict[str, Any]
    body_text: str


@runtime_checkable
class TicketHandler(Protocol):
    """Register or update tickets in an external system."""

    def on_ticket_event(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        """Handle CREATE / UPDATE / CLOSE. Return external id to store locally."""
        ...


class BaseTicketHandler(ABC):
    """Convenience base: route to on_created / on_updated / on_closed."""

    def on_ticket_event(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        if ctx.event.action == "created":
            return self.on_created(ctx)
        if ctx.event.action == "updated":
            return self.on_updated(ctx)
        if ctx.event.action == "closed":
            return self.on_closed(ctx)
        return None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        return None

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        return None


class LoggingTicketHandler(BaseTicketHandler):
    """Built-in handler: logs events (for debugging); assigns pseudo external_ref on CREATE."""

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        logger.info(
            "ticket created %s group_key=%s title=%s",
            ctx.event.ticket_id,
            ctx.ticket.get("group_key"),
            ctx.event.title,
        )
        return HandlerResult(external_ref=f"log:{ctx.event.ticket_id}")

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        logger.info(
            "ticket updated %s changes=%s",
            ctx.event.ticket_id,
            "; ".join(ctx.event.changes),
        )
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        logger.info(
            "ticket closed %s reason=%s external_ref=%s",
            ctx.event.ticket_id,
            close_reason_label(ctx.event.close_reason),
            ctx.ticket.get("external_ref"),
        )
        return None


def _body_text_for_event(event: TicketEvent) -> str:
    if event.group is not None:
        return _format_group_block(event.group.display)
    return ""


def _import_from_spec(spec: str) -> TicketHandler:
    spec = spec.strip()
    if not spec:
        raise ValueError("empty handler spec")
    if ":" not in spec:
        raise ValueError(f"handler spec must be module:Object, got {spec!r}")
    module_name, _, qualname = spec.partition(":")
    if not module_name or not qualname:
        raise ValueError(f"invalid handler spec {spec!r}")

    module = importlib.import_module(module_name)
    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part)

    if isinstance(obj, type):
        instance = obj()
    elif callable(obj):
        instance = obj()
    else:
        instance = obj

    if not isinstance(instance, TicketHandler) and not hasattr(instance, "on_ticket_event"):
        raise TypeError(f"{spec!r} is not a TicketHandler (missing on_ticket_event)")
    return instance  # type: ignore[return-value]


def load_ticket_handlers(specs: list[str]) -> list[TicketHandler]:
    handlers: list[TicketHandler] = []
    for spec in specs:
        if not spec.strip():
            continue
        try:
            handlers.append(_import_from_spec(spec))
            logger.info("loaded ticket handler %s", spec.strip())
        except Exception:
            logger.exception("failed to load ticket handler %s", spec)
            raise
    return handlers


def parse_handler_specs(
    *,
    cli_handlers: list[str] | None = None,
    env_value: str = "",
) -> list[str]:
    """Merge --ticket-handler values and TICKET_HANDLERS env (comma-separated)."""
    specs: list[str] = []
    if env_value.strip():
        specs.extend(s.strip() for s in env_value.split(",") if s.strip())
    if cli_handlers:
        for item in cli_handlers:
            specs.extend(s.strip() for s in item.split(",") if s.strip())
    return specs


def apply_handler_results(ticket: dict[str, Any], result: HandlerResult | None) -> None:
    if result is None:
        return
    if result.external_ref is not None:
        ticket["external_ref"] = result.external_ref
    if result.external_meta:
        existing = ticket.get("external_meta")
        if isinstance(existing, dict):
            existing.update(result.external_meta)
        else:
            ticket["external_meta"] = dict(result.external_meta)


def dispatch_ticket_handlers(
    handlers: list[TicketHandler],
    store: TicketStore,
    events: list[TicketEvent],
) -> None:
    """Invoke handlers for each event; persist external_ref / external_meta on tickets."""
    if not handlers or not events:
        return

    dirty = False

    for event in events:
        ticket = store.get_ticket(event.ticket_id)
        if not isinstance(ticket, dict):
            logger.warning("ticket %s not found for handler dispatch", event.ticket_id)
            continue

        ctx = TicketHandlerContext(
            event=event,
            ticket=ticket,
            body_text=_body_text_for_event(event),
        )
        for handler in handlers:
            name = type(handler).__name__
            try:
                result = handler.on_ticket_event(ctx)
            except Exception:
                logger.exception("ticket handler %s failed for %s", name, event.ticket_id)
                continue
            if result is not None:
                apply_handler_results(ticket, result)
                dirty = True

    if dirty:
        store.save()
