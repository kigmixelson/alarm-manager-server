"""ServiceNow Table API — incident on CREATE, work_notes on UPDATE/CLOSE."""

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


class ServiceNowTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        instance_url: str,
        user: str = "",
        password: str = "",
        oauth_token: str = "",
        table: str = "incident",
        caller_id: str = "",
        assignment_group: str = "",
        impact: str = "2",
        urgency: str = "2",
        close_state: str = "7",
        client: httpx.Client | None = None,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.table = table
        self.caller_id = caller_id.strip()
        self.assignment_group = assignment_group.strip()
        self.impact = impact
        self.urgency = urgency
        self.close_state = close_state
        self._user = user
        self._password = password
        self._oauth_token = oauth_token
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> ServiceNowTicketHandler | None:
        if not cfg.servicenow_enabled:
            return None
        return cls(
            instance_url=cfg.servicenow_instance_url,
            user=cfg.servicenow_user,
            password=cfg.servicenow_password.get_secret_value(),
            oauth_token=cfg.servicenow_oauth_token.get_secret_value(),
            table=cfg.servicenow_table,
            caller_id=cfg.servicenow_caller_id,
            assignment_group=cfg.servicenow_assignment_group,
            impact=cfg.servicenow_impact,
            urgency=cfg.servicenow_urgency,
            close_state=cfg.servicenow_close_state,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            auth: tuple[str, str] | None = None
            if self._oauth_token:
                headers["Authorization"] = f"Bearer {self._oauth_token}"
            elif self._user:
                auth = (self._user, self._password)
            self._client = httpx.Client(
                base_url=f"{self.instance_url}/api/now/table",
                auth=auth,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        payload: dict[str, Any] = {
            "short_description": issue_title(ctx),
            "description": issue_description(ctx),
            "impact": self.impact,
            "urgency": self.urgency,
        }
        if self.caller_id:
            payload["caller_id"] = self.caller_id
        if self.assignment_group:
            payload["assignment_group"] = self.assignment_group

        resp = self._http().post(f"/{self.table}", json=payload)
        resp.raise_for_status()
        record = _unwrap_result(resp.json())
        sys_id = str(record.get("sys_id") or "")
        number = str(record.get("number") or sys_id)
        if not sys_id:
            raise RuntimeError(f"ServiceNow create returned no sys_id: {resp.text}")
        logger.info("ServiceNow %s created for %s", number, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=number,
            external_meta={"system": "servicenow", "sys_id": sys_id, "number": number},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        sys_id = _servicenow_sys_id(ctx)
        if not sys_id:
            return self.on_created(ctx)
        self._patch(sys_id, {"work_notes": update_comment(ctx)})
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        sys_id = _servicenow_sys_id(ctx)
        if not sys_id:
            return None
        fields: dict[str, Any] = {"work_notes": close_comment(ctx)}
        if self.close_state:
            fields["state"] = self.close_state
        self._patch(sys_id, fields)
        logger.info("ServiceNow incident %s closed", sys_id)
        return None

    def _patch(self, sys_id: str, fields: dict[str, Any]) -> None:
        resp = self._http().patch(f"/{self.table}/{sys_id}", json=fields)
        resp.raise_for_status()


def _servicenow_sys_id(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    if meta.get("sys_id"):
        return str(meta["sys_id"])
    return ""


def _unwrap_result(data: Any) -> dict[str, Any]:
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    if isinstance(data, dict):
        return data
    return {}
