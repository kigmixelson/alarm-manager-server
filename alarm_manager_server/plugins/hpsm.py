"""HP Service Manager / Service Desk REST API — incident on CREATE, journal on UPDATE/CLOSE."""

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
from alarm_manager_server.plugins._refs import external_id
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class HpServiceManagerTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        password: str,
        collection: str = "incidents",
        resource_name: str = "Incident",
        impact: str = "3",
        urgency: str = "3",
        category: str = "incident",
        assignment_group: str = "",
        affected_ci: str = "",
        close_status: str = "Closed",
        closure_code: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.collection = collection.strip() or "incidents"
        self.resource_name = resource_name.strip() or "Incident"
        self.impact = impact
        self.urgency = urgency
        self.category = category.strip() or "incident"
        self.assignment_group = assignment_group.strip()
        self.affected_ci = affected_ci.strip()
        self.close_status = close_status.strip()
        self.closure_code = closure_code.strip()
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> HpServiceManagerTicketHandler | None:
        if not cfg.hpsm_enabled:
            return None
        return cls(
            base_url=cfg.hpsm_base_url,
            user=cfg.hpsm_user,
            password=cfg.hpsm_password.get_secret_value(),
            collection=cfg.hpsm_collection,
            resource_name=cfg.hpsm_resource_name,
            impact=cfg.hpsm_impact,
            urgency=cfg.hpsm_urgency,
            category=cfg.hpsm_category,
            assignment_group=cfg.hpsm_assignment_group,
            affected_ci=cfg.hpsm_affected_ci,
            close_status=cfg.hpsm_close_status,
            closure_code=cfg.hpsm_closure_code,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                auth=(self.user, self.password),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        fields: dict[str, Any] = {
            "Title": issue_title(ctx),
            "Description": [issue_description(ctx)],
            "Impact": self.impact,
            "Urgency": self.urgency,
            "Category": self.category,
        }
        if self.assignment_group:
            fields["AssignmentGroup"] = self.assignment_group
        if self.affected_ci:
            fields["AffectedCI"] = self.affected_ci

        resp = self._http().post(f"/{self.collection}", json=self._wrap(fields))
        resp.raise_for_status()
        incident_id = _parse_incident_id(resp.json(), resource_name=self.resource_name)
        logger.info("HP SM %s created for %s", incident_id, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=incident_id,
            external_meta={
                "system": "hpsm",
                "incident_id": incident_id,
            },
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        incident_id = external_id(ctx, system="hpsm", id_key="incident_id")
        if not incident_id:
            return self.on_created(ctx)
        self._put(incident_id, {"JournalUpdates": [update_comment(ctx)]})
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        incident_id = external_id(ctx, system="hpsm", id_key="incident_id")
        if not incident_id:
            return None
        fields: dict[str, Any] = {"JournalUpdates": [close_comment(ctx)]}
        if self.close_status:
            fields["Status"] = self.close_status
        if self.closure_code:
            fields["ClosureCode"] = self.closure_code
            fields["Solution"] = [close_comment(ctx)]
        self._put(incident_id, fields)
        logger.info("HP SM incident %s closed", incident_id)
        return None

    def _wrap(self, fields: dict[str, Any]) -> dict[str, Any]:
        return {self.resource_name: fields}

    def _put(self, incident_id: str, fields: dict[str, Any]) -> None:
        payload_fields = dict(fields)
        payload_fields["IncidentID"] = incident_id
        resp = self._http().put(
            f"/{self.collection}/{incident_id}",
            json=self._wrap(payload_fields),
        )
        resp.raise_for_status()
        _check_return_code(resp.json())


def _parse_incident_id(data: Any, *, resource_name: str) -> str:
    _check_return_code(data)
    if not isinstance(data, dict):
        raise RuntimeError(f"HP SM create returned unexpected payload: {data!r}")
    incident = data.get(resource_name)
    if not isinstance(incident, dict):
        raise RuntimeError(f"HP SM create returned no {resource_name}: {data!r}")
    incident_id = str(incident.get("IncidentID") or "")
    if not incident_id:
        raise RuntimeError(f"HP SM create returned no IncidentID: {data!r}")
    return incident_id


def _check_return_code(data: Any) -> None:
    if isinstance(data, dict) and data.get("ReturnCode") not in (None, 0):
        messages = data.get("Messages") or data.get("messages") or []
        raise RuntimeError(f"HP SM API error (ReturnCode={data.get('ReturnCode')}): {messages}")
