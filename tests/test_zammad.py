"""Tests for Zammad ticket plugin."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.plugins.zammad import ZammadTicketHandler
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


def test_discover_zammad():
    cfg = Settings(
        zammad_base_url="https://zammad.example.com",
        zammad_api_token="token",
        zammad_group="Users",
        zammad_customer="ops@example.com",
    )
    handlers = discover_ticket_handlers(cfg)
    assert any(isinstance(h, ZammadTicketHandler) for h in handlers)


def test_discover_zammad_requires_group_or_id():
    cfg = Settings(
        zammad_base_url="https://zammad.example.com",
        zammad_api_token="token",
        zammad_customer="ops@example.com",
    )
    assert not any(isinstance(h, ZammadTicketHandler) for h in discover_ticket_handlers(cfg))


def test_zammad_create_ticket():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"id": 42, "number": "10042"},
        request=httpx.Request("POST", "https://zammad.example.com/api/v1/tickets"),
    )
    handler = ZammadTicketHandler(
        base_url="https://zammad.example.com",
        api_token="token",
        group="Users",
        customer="ops@example.com",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "#10042"
    assert result.external_meta["ticket_id"] == "42"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["group"] == "Users"
    assert payload["customer"] == "ops@example.com"
    assert payload["article"]["body"]


def test_zammad_update_article():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"id": 1},
        request=httpx.Request("POST", "https://zammad.example.com/api/v1/ticket_articles"),
    )
    handler = ZammadTicketHandler(
        base_url="https://zammad.example.com",
        api_token="token",
        group="Users",
        customer="ops@example.com",
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
            "external_ref": "#10042",
            "external_meta": {"system": "zammad", "ticket_id": "42", "number": "10042"},
        },
        body_text="",
    )
    handler.on_updated(ctx)
    assert mock_client.post.call_args.args[0] == "/ticket_articles"
    assert mock_client.post.call_args.kwargs["json"]["ticket_id"] == 42


def test_zammad_close_sets_state():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.put.return_value = httpx.Response(
        200,
        json={"id": 42, "state": "closed"},
        request=httpx.Request("PUT", "https://zammad.example.com/api/v1/tickets/42"),
    )
    handler = ZammadTicketHandler(
        base_url="https://zammad.example.com",
        api_token="token",
        group="Users",
        customer="ops@example.com",
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
            "external_meta": {"system": "zammad", "ticket_id": "42", "number": "10042"},
        },
        body_text="",
    )
    handler.on_closed(ctx)
    payload = mock_client.put.call_args.kwargs["json"]
    assert payload["state"] == "closed"
    assert "закрытие" in payload["article"]["body"]
