"""Post Service Desk reference to SAYMON incidents after external ticket registration."""

from __future__ import annotations

import logging

from alarm_manager_server.logging_utils import log_error
from typing import Any

from alarm_manager_server.config import Settings, settings
from alarm_manager_server.models.incident import Incident
from alarm_manager_server.saymon.client import SaymonClient
from alarm_manager_server.worker.tickets import TicketEvent, TicketStore

logger = logging.getLogger(__name__)

SYSTEM_LABELS: dict[str, str] = {
    "jira": "Jira",
    "redmine": "Redmine",
    "freshdesk": "Freshdesk",
    "servicenow": "ServiceNow",
    "simpleone": "SimpleOne",
    "naumen": "Naumen",
    "elma": "ELMA365",
    "bitrix24": "Битрикс24",
    "hpsm": "HP Service Manager",
    "zammad": "Zammad",
}


def format_saymon_sd_comment(
    *,
    local_ticket_id: str,
    external_refs: dict[str, str],
    template: str,
) -> str:
    lines: list[str] = []
    for system, ref in sorted(external_refs.items()):
        label = SYSTEM_LABELS.get(system, system)
        lines.append(
            template.format(
                system=label,
                external_ref=ref,
                local_ticket_id=local_ticket_id,
            )
        )
    if len(lines) == 1:
        return lines[0]
    header = f"Зарегистрировано во внешней системе (тикет {local_ticket_id}):"
    return header + "\n" + "\n".join(f"- {line}" for line in lines)


def _member_ids_from_ticket(ticket: dict[str, Any]) -> list[str]:
    snap = ticket.get("snapshot") or {}
    raw = snap.get("member_ids") or []
    return [str(x) for x in raw if x]


async def annotate_saymon_incidents_on_registration(
    events: list[TicketEvent],
    store: TicketStore,
    incidents_by_id: dict[str, Incident],
    cfg: Settings | None = None,
) -> None:
    """After CREATE + successful external registration, comment on active SAYMON incidents."""
    cfg = cfg or settings
    if not cfg.ticket_saymon_comment_enabled:
        return
    if not cfg.saymon_login or not cfg.saymon_password.get_secret_value().strip():
        logger.debug("SAYMON credentials missing; skip incident SD comments")
        return

    create_events = [e for e in events if e.action == "created"]
    if not create_events:
        return

    client = SaymonClient.from_settings(cfg)
    dirty = False
    try:
        for event in create_events:
            ticket = store.get_ticket(event.ticket_id)
            if not isinstance(ticket, dict):
                continue
            meta = ticket.setdefault("external_meta", {})
            refs = meta.get("external_refs")
            if not isinstance(refs, dict) or not refs:
                continue

            # Oracle has separate outcome comments, including failures and no-ID results.
            refs = {k: v for k, v in refs.items() if k != "oracle" or not meta.get("oracle_comments")}
            if not refs:
                continue

            commented: dict[str, Any] = meta.setdefault("saymon_sd_comments", {})
            if not isinstance(commented, dict):
                commented = {}
                meta["saymon_sd_comments"] = commented

            comment = format_saymon_sd_comment(
                local_ticket_id=event.ticket_id,
                external_refs={str(k): str(v) for k, v in refs.items() if v},
                template=cfg.ticket_saymon_comment_template,
            )
            for inc_id in _member_ids_from_ticket(ticket):
                if inc_id in commented:
                    continue
                inc = incidents_by_id.get(inc_id)
                if inc is not None and inc.is_history:
                    logger.debug("skip history incident %s for SD comment", inc_id)
                    continue
                try:
                    await client.add_incident_comment(inc_id, comment)
                    commented[inc_id] = dict(refs)
                    dirty = True
                    logger.info(
                        "SAYMON comment on incident %s for %s refs=%s",
                        inc_id,
                        event.ticket_id,
                        refs,
                    )
                except Exception:
                    log_error(logger,
                        "failed SAYMON comment on incident %s for %s",
                        inc_id,
                        event.ticket_id,
                    )
    finally:
        await client.aclose()

    if dirty:
        store.save()


async def flush_oracle_comments(
    store: TicketStore, cfg: Settings, incidents_by_id: dict[str, Incident] | None = None,
) -> None:
    """Retry pending status comments independently of new ticket events."""
    if not cfg.oracle_saymon_comment_enabled:
        return
    pending = [
        (ticket, item)
        for ticket in store.all_tickets()
        for item in (ticket.get("external_meta") or {}).get("oracle_comments", [])
        if item.get("pending_incident_ids")
    ]
    if not pending:
        return
    if not cfg.saymon_login or not cfg.saymon_password.get_secret_value():
        logger.warning("Oracle status comments pending: SAYMON credentials missing")
        return
    client = SaymonClient.from_settings(cfg)
    try:
        for ticket, item in pending:
            for incident_id in list(item["pending_incident_ids"]):
                incident = (incidents_by_id or {}).get(incident_id)
                if incident is not None and incident.is_history:
                    item.setdefault("skipped_incidents", {})[incident_id] = "history"
                    item["pending_incident_ids"].remove(incident_id)
                    store.save()
                    logger.warning("Oracle status comment skipped ticket=%s incident=%s reason=history",
                                   ticket.get("ticket_id"), incident_id)
                    continue
                try:
                    await client.add_incident_comment(incident_id, item["text"])
                except Exception:
                    log_error(logger, "Oracle status comment failed ticket=%s incident=%s; will retry",
                                     ticket.get("ticket_id"), incident_id)
                    continue
                item["pending_incident_ids"].remove(incident_id)
                store.save()
                logger.info("Oracle status comment delivered ticket=%s incident=%s",
                            ticket.get("ticket_id"), incident_id)
    finally:
        await client.aclose()
