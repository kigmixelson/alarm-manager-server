"""Naumen ITSM / ITSM 365 REST API — service call on CREATE, comment on UPDATE, edit on CLOSE."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

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


class NaumenTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        access_key: str,
        meta_class: str = "serviceCall$serviceCall",
        client: str = "",
        client_employee: str = "",
        agreement: str = "",
        service: str = "",
        office: str = "",
        comment_author: str = "",
        close_state: str = "resolved",
        close_code: str = "resolved",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key.strip()
        self.meta_class = meta_class.strip() or "serviceCall$serviceCall"
        self.client = client.strip()
        self.client_employee = client_employee.strip()
        self.agreement = agreement.strip()
        self.service = service.strip()
        self.office = office.strip()
        self.comment_author = comment_author.strip() or self.client_employee
        self.close_state = close_state.strip() or "resolved"
        self.close_code = close_code.strip() or "resolved"
        self._client = http_client
        self._owns_client = http_client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> NaumenTicketHandler | None:
        if not cfg.naumen_enabled:
            return None
        return cls(
            base_url=cfg.naumen_base_url,
            access_key=cfg.naumen_access_key.get_secret_value(),
            meta_class=cfg.naumen_meta_class,
            client=cfg.naumen_client,
            client_employee=cfg.naumen_client_employee,
            agreement=cfg.naumen_agreement,
            service=cfg.naumen_service,
            office=cfg.naumen_office,
            comment_author=cfg.naumen_comment_author,
            close_state=cfg.naumen_close_state,
            close_code=cfg.naumen_close_code,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=f"{self.base_url}/services/rest",
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        payload: dict[str, Any] = {
            "shortDescr": issue_title(ctx),
            "metaClass": self.meta_class,
            "client": self.client,
            "clientEmployee": self.client_employee,
            "agreement": self.agreement,
            "service": self.service,
            "descriptionInRTF": plain_to_html(issue_description(ctx)),
        }
        if self.office:
            payload["office"] = self.office

        resp = self._http().post(
            f"/create-m2m/{self.meta_class}",
            params={"accessKey": self.access_key},
            json=payload,
        )
        resp.raise_for_status()
        uuid, title, number = _parse_create_response(resp.json())
        logger.info("Naumen %s created for %s", title, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=title,
            external_meta={
                "system": "naumen",
                "uuid": uuid,
                "number": number,
                "title": title,
            },
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        uuid = _naumen_uuid(ctx)
        if not uuid:
            return self.on_created(ctx)
        self._add_comment(uuid, update_comment(ctx))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        uuid = _naumen_uuid(ctx)
        if not uuid:
            return None
        params: dict[str, str] = {
            "accessKey": self.access_key,
            "resultDescr": close_comment(ctx),
        }
        if self.close_state:
            params["state"] = self.close_state
        if self.close_code:
            params["codeOfClosing"] = self.close_code
        resp = self._http().post(f"/edit/{quote(uuid, safe='$')}/", params=params)
        resp.raise_for_status()
        logger.info("Naumen service call %s closed", uuid)
        return None

    def _add_comment(self, service_call_uuid: str, text: str) -> None:
        resp = self._http().post(
            "/create-m2m/comment",
            params={
                "accessKey": self.access_key,
                "source": service_call_uuid,
                "author": self.comment_author,
                "text": text,
            },
        )
        resp.raise_for_status()


def _naumen_uuid(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    if meta.get("uuid"):
        return str(meta["uuid"])
    return ""


def _parse_create_response(data: Any) -> tuple[str, str, str]:
    if isinstance(data, str) and data.strip():
        ref = data.strip()
        return ref, ref, ref
    if isinstance(data, dict):
        uuid = str(data.get("UUID") or data.get("uuid") or "")
        number = str(data.get("number") or "")
        title = str(data.get("title") or number or uuid)
        if uuid:
            return uuid, title, number
    raise RuntimeError(f"Naumen create returned unexpected payload: {data!r}")
