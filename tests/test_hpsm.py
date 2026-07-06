"""Tests for HP Service Manager ticket plugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.hpsm import HpServiceManagerTicketHandler
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


def test_discover_hpsm():
    cfg = Settings(
        hpsm_base_url="https://sm.example.com/SM/9/rest",
        hpsm_user="integration",
        hpsm_password="secret",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], HpServiceManagerTicketHandler)


def test_hpsm_create_incident():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"ReturnCode": 0, "Incident": {"IncidentID": "IM10099", "Title": "test"}},
        request=httpx.Request("POST", "https://sm.example.com/SM/9/rest/incidents"),
    )
    handler = HpServiceManagerTicketHandler(
        base_url="https://sm.example.com/SM/9/rest",
        user="integration",
        password="secret",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "IM10099"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["Incident"]["Title"].startswith("[Alarm Manager]")
    assert isinstance(payload["Incident"]["Description"], list)
    assert payload["Incident"]["Description"]


def test_hpsm_update_journal():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.put.return_value = httpx.Response(
        200,
        json={"ReturnCode": 0},
        request=httpx.Request(
            "PUT",
            "https://sm.example.com/SM/9/rest/incidents/IM10099",
        ),
    )
    handler = HpServiceManagerTicketHandler(
        base_url="https://sm.example.com/SM/9/rest",
        user="integration",
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
            "external_meta": {"system": "hpsm", "incident_id": "IM10099"},
        },
        body_text="",
    )
    handler.on_updated(ctx)
    payload = mock_client.put.call_args.kwargs["json"]["Incident"]
    assert payload["IncidentID"] == "IM10099"
    assert "обновление" in payload["JournalUpdates"][0]


def test_hpsm_close_incident():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.put.return_value = httpx.Response(
        200,
        json={"ReturnCode": 0},
        request=httpx.Request(
            "PUT",
            "https://sm.example.com/SM/9/rest/incidents/IM10099",
        ),
    )
    handler = HpServiceManagerTicketHandler(
        base_url="https://sm.example.com/SM/9/rest",
        user="integration",
        password="secret",
        closure_code="Solved Remotely",
        client=mock_client,
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
            "external_meta": {"system": "hpsm", "incident_id": "IM10099"},
        },
        body_text="",
    )
    handler.on_closed(ctx)
    payload = mock_client.put.call_args.kwargs["json"]["Incident"]
    assert payload["Status"] == "Closed"
    assert payload["ClosureCode"] == "Solved Remotely"
