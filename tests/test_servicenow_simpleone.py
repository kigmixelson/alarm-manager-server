"""Tests for ServiceNow and SimpleOne ticket plugins."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.plugins.servicenow import ServiceNowTicketHandler
from alarm_manager_server.plugins.simpleone import SimpleOneTicketHandler
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


def test_discover_servicenow_basic_auth():
    cfg = Settings(
        servicenow_instance_url="https://dev.service-now.com",
        servicenow_user="api",
        servicenow_password="secret",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], ServiceNowTicketHandler)


def test_discover_simpleone():
    cfg = Settings(
        simpleone_base_url="https://sandbox.dev.simpleone.ru",
        simpleone_api_token="token",
        simpleone_caller="155931135900000001",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], SimpleOneTicketHandler)


def test_servicenow_create_incident():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"result": {"sys_id": "abc", "number": "INC0009999"}},
        request=httpx.Request("POST", "https://dev.service-now.com/api/now/table/incident"),
    )
    handler = ServiceNowTicketHandler(
        instance_url="https://dev.service-now.com",
        user="api",
        password="secret",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "INC0009999"
    assert result.external_meta["sys_id"] == "abc"


def test_servicenow_update_work_notes():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.patch.return_value = httpx.Response(
        200,
        request=httpx.Request("PATCH", "https://dev.service-now.com/api/now/table/incident/abc"),
    )
    handler = ServiceNowTicketHandler(
        instance_url="https://dev.service-now.com",
        user="api",
        password="secret",
        client=mock_client,
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
            "external_meta": {"system": "servicenow", "sys_id": "abc", "number": "INC1"},
        },
        body_text="",
    )
    handler.on_updated(ctx)
    assert mock_client.patch.call_args.kwargs["json"]["work_notes"]


def test_simpleone_create_incident():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"sys_id": "sys1", "number": "INC0000123"},
        request=httpx.Request("POST", "https://sandbox.dev.simpleone.ru/rest/v1/table/itsm_incident"),
    )
    handler = SimpleOneTicketHandler(
        base_url="https://sandbox.dev.simpleone.ru",
        api_token="token",
        caller="caller-sys-id",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "INC0000123"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["caller"] == "caller-sys-id"
