"""Tests for Naumen / ITSM 365 ticket plugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.naumen import NaumenTicketHandler
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext
from alarm_manager_server.worker.tickets import TicketEvent


def _ctx() -> TicketHandlerContext:
    return TicketHandlerContext(
        event=TicketEvent(
            action="created",
            ticket_id="T-000001",
            group=None,
            changes=[],
            title="Router-A",
        ),
        ticket={"ticket_id": "T-000001", "group_key": "owner:x", "snapshot": {}},
        body_text="body",
    )


def test_discover_naumen():
    cfg = Settings(
        naumen_base_url="https://tenant.itsm365.com/sd",
        naumen_access_key="access-key",
        naumen_client="ou$1",
        naumen_client_employee="employee$2",
        naumen_agreement="agreement$3",
        naumen_service="slmService$4",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], NaumenTicketHandler)


def test_naumen_create_service_call():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"UUID": "serviceCall$99", "number": 82, "title": "INC82"},
        request=httpx.Request(
            "POST",
            "https://tenant.itsm365.com/sd/services/rest/create-m2m/serviceCall$serviceCall",
        ),
    )
    handler = NaumenTicketHandler(
        base_url="https://tenant.itsm365.com/sd",
        access_key="access-key",
        client="ou$1",
        client_employee="employee$2",
        agreement="agreement$3",
        service="slmService$4",
        http_client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "INC82"
    assert result.external_meta["uuid"] == "serviceCall$99"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["client"] == "ou$1"
    assert payload["service"] == "slmService$4"
    assert mock_client.post.call_args.kwargs["params"]["accessKey"] == "access-key"


def test_naumen_update_adds_comment():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "https://tenant.itsm365.com/sd/services/rest/create-m2m/comment",
        ),
    )
    handler = NaumenTicketHandler(
        base_url="https://tenant.itsm365.com/sd",
        access_key="access-key",
        client="ou$1",
        client_employee="employee$2",
        agreement="agreement$3",
        service="slmService$4",
        http_client=mock_client,
    )
    ctx = TicketHandlerContext(
        event=TicketEvent(
            action="updated",
            ticket_id="T-1",
            group=None,
            changes=["x"],
            title="G",
        ),
        ticket={
            "external_meta": {
                "system": "naumen",
                "uuid": "serviceCall$99",
                "title": "INC82",
            },
        },
        body_text="",
    )
    handler.on_updated(ctx)
    params = mock_client.post.call_args.kwargs["params"]
    assert params["source"] == "serviceCall$99"
    assert params["author"] == "employee$2"
    assert "обновление" in params["text"]


def test_naumen_close_edits_service_call():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "https://tenant.itsm365.com/sd/services/rest/edit/serviceCall$99/",
        ),
    )
    handler = NaumenTicketHandler(
        base_url="https://tenant.itsm365.com/sd",
        access_key="access-key",
        client="ou$1",
        client_employee="employee$2",
        agreement="agreement$3",
        service="slmService$4",
        http_client=mock_client,
    )
    ctx = TicketHandlerContext(
        event=TicketEvent(
            action="closed",
            ticket_id="T-1",
            group=None,
            changes=[],
            close_reason="all_cleared",
            title="G",
        ),
        ticket={
            "external_meta": {"system": "naumen", "uuid": "serviceCall$99", "title": "INC82"},
        },
        body_text="",
    )
    handler.on_closed(ctx)
    path = mock_client.post.call_args.args[0]
    assert path == "/edit/serviceCall$99/"
    params = mock_client.post.call_args.kwargs["params"]
    assert params["state"] == "resolved"
    assert params["codeOfClosing"] == "resolved"
    assert "закрытие" in params["resultDescr"]
