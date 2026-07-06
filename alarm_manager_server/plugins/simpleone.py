"""SimpleOne Table API — incident on CREATE, work_notes on UPDATE/CLOSE."""

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


class SimpleOneTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        table: str = "itsm_incident",
        caller: str = "",
        service: str = "",
        contact_type: str = "email",
        close_state: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.table = table
        self.caller = caller.strip()
        self.service = service.strip()
        self.contact_type = contact_type.strip() or "email"
        self.close_state = close_state.strip()
        self.api_token = api_token
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> SimpleOneTicketHandler | None:
        if not cfg.simpleone_enabled:
            return None
        return cls(
            base_url=cfg.simpleone_base_url,
            api_token=cfg.simpleone_api_token.get_secret_value(),
            table=cfg.simpleone_table,
            caller=cfg.simpleone_caller,
            service=cfg.simpleone_service,
            contact_type=cfg.simpleone_contact_type,
            close_state=cfg.simpleone_close_state,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.base_url}/rest/v1/table",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
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
        payload: dict[str, Any] = {
            "subject": issue_title(ctx),
            "description": issue_description(ctx),
            "caller": self.caller,
            "contact_type": self.contact_type,
        }
        if self.service:
            payload["service"] = self.service

        resp = self._http().post(f"/{self.table}", json=payload)
        resp.raise_for_status()
        record = _unwrap_record(resp.json())
        sys_id = str(record.get("sys_id") or "")
        number = str(record.get("number") or record.get("display_name") or sys_id)
        if not sys_id:
            raise RuntimeError(f"SimpleOne create returned no sys_id: {resp.text}")
        logger.info("SimpleOne %s created for %s", number, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=number,
            external_meta={"system": "simpleone", "sys_id": sys_id, "number": number},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        sys_id = _simpleone_sys_id(ctx)
        if not sys_id:
            return self.on_created(ctx)
        self._patch(sys_id, {"work_notes": update_comment(ctx)})
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        sys_id = _simpleone_sys_id(ctx)
        if not sys_id:
            return None
        fields: dict[str, Any] = {"work_notes": close_comment(ctx)}
        if self.close_state:
            fields["state"] = self.close_state
        self._patch(sys_id, fields)
        logger.info("SimpleOne incident %s closed", sys_id)
        return None

    def _patch(self, sys_id: str, fields: dict[str, Any]) -> None:
        resp = self._http().patch(f"/{self.table}/{sys_id}", json=fields)
        resp.raise_for_status()


def _simpleone_sys_id(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    if meta.get("sys_id"):
        return str(meta["sys_id"])
    return ""


def _unwrap_record(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            return data["data"]
        if isinstance(data.get("result"), dict):
            return data["result"]
        return data
    return {}
