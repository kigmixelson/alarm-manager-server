"""Shared text formatting for external ticket systems."""

from __future__ import annotations

from typing import Any

from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext
from alarm_manager_server.worker.tickets import close_reason_label


def issue_title(ctx: TicketHandlerContext) -> str:
    snap = ctx.ticket.get("snapshot") or {}
    title = (ctx.event.title or snap.get("title") or ctx.ticket.get("group_key") or "").strip()
    return f"[Alarm Manager] {title}" if title else f"[Alarm Manager] {ctx.event.ticket_id}"


def issue_description(ctx: TicketHandlerContext) -> str:
    lines = [
        f"Локальный тикет: {ctx.event.ticket_id}",
        f"Group key: {ctx.ticket.get('group_key', '')}",
    ]
    if ctx.body_text:
        lines.extend(["", ctx.body_text])
    return "\n".join(lines)


def update_comment(ctx: TicketHandlerContext) -> str:
    parts = [f"Alarm Manager — обновление {ctx.event.ticket_id}"]
    if ctx.event.changes:
        parts.append("Изменения: " + "; ".join(ctx.event.changes))
    if ctx.body_text:
        parts.extend(["", ctx.body_text])
    return "\n".join(parts)


def close_comment(ctx: TicketHandlerContext) -> str:
    reason = close_reason_label(ctx.event.close_reason)
    snap = ctx.ticket.get("snapshot") or {}
    title = ctx.event.title or snap.get("title") or ctx.event.ticket_id
    return f"Alarm Manager — закрытие {ctx.event.ticket_id}\nГруппа: {title}\nПричина: {reason}"


def snapshot_member_ids(ctx: TicketHandlerContext) -> list[str]:
    snap = ctx.ticket.get("snapshot") or {}
    raw = snap.get("member_ids") or []
    return [str(x) for x in raw if x]


def plain_to_html(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped.replace("\n", "<br>")
