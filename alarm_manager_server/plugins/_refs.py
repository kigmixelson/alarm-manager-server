"""Resolve external ticket ids from handler context."""

from __future__ import annotations

from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext


def external_id(
    ctx: TicketHandlerContext,
    *,
    system: str,
    id_key: str = "sys_id",
    ref_is_numeric: bool = False,
) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    refs = meta.get("external_refs")
    if isinstance(refs, dict) and refs.get(system):
        return str(refs[system]).strip()
    if meta.get("system") == system and meta.get(id_key):
        return str(meta[id_key]).strip()
    ref = ctx.ticket.get("external_ref")
    if isinstance(ref, str) and ref.strip():
        cleaned = ref.strip()
        if ref_is_numeric:
            digits = cleaned.lstrip("#")
            if digits.isdigit():
                return digits
        elif not cleaned.lstrip("#").isdigit():
            return cleaned
    return ""
