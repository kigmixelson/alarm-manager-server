"""Tests for ELMA365 and Bitrix24 ticket plugins."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.bitrix24 import Bitrix24TicketHandler
from alarm_manager_server.plugins.elma import ElmaTicketHandler
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


def test_discover_elma():
    cfg = Settings(
        elma_base_url="https://company.elma365.ru",
        elma_api_token="token",
        elma_namespace="service_desk",
        elma_app_code="incident",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], ElmaTicketHandler)


def test_discover_bitrix24():
    cfg = Settings(
        bitrix24_webhook_url="https://portal.bitrix24.ru/rest/1/abc123",
        bitrix24_responsible_id=42,
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], Bitrix24TicketHandler)


def test_elma_create_item():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"item": {"__id": "uuid-1", "__name": "INC-100"}},
        request=httpx.Request(
            "POST",
            "https://company.elma365.ru/pub/v1/app/service_desk/incident/create",
        ),
    )
    handler = ElmaTicketHandler(
        base_url="https://company.elma365.ru",
        api_token="token",
        namespace="service_desk",
        app_code="incident",
        description_field="description",
        http_client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "INC-100"
    assert result.external_meta["item_id"] == "uuid-1"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["context"]["__name"].startswith("[Alarm Manager]")
    assert "description" in payload["context"]


def test_elma_update_feed_message():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        request=httpx.Request(
            "POST",
            "https://company.elma365.ru/pub/v1/feed/service_desk/incident/uuid-1/message",
        ),
    )
    handler = ElmaTicketHandler(
        base_url="https://company.elma365.ru",
        api_token="token",
        namespace="service_desk",
        app_code="incident",
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
            "external_meta": {"system": "elma", "item_id": "uuid-1", "title": "INC-100"},
        },
        body_text="",
    )
    handler.on_updated(ctx)
    path = mock_client.post.call_args.args[0]
    assert path.endswith("/feed/service_desk/incident/uuid-1/message")
    assert "обновление" in mock_client.post.call_args.kwargs["json"]["body"]


def test_elma_close_sets_status():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        request=httpx.Request("POST", "https://company.elma365.ru/pub/v1/"),
    )
    handler = ElmaTicketHandler(
        base_url="https://company.elma365.ru",
        api_token="token",
        namespace="service_desk",
        app_code="incident",
        close_status="closed",
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
            "external_meta": {"system": "elma", "item_id": "uuid-1", "title": "INC-100"},
        },
        body_text="",
    )
    handler.on_closed(ctx)
    calls = [call.args[0] for call in mock_client.post.call_args_list]
    assert any("/set-status" in path for path in calls)


def test_bitrix24_create_task():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"result": {"task": {"id": "77"}}},
        request=httpx.Request(
            "POST",
            "https://portal.bitrix24.ru/rest/1/abc123/tasks.task.add",
        ),
    )
    handler = Bitrix24TicketHandler(
        webhook_url="https://portal.bitrix24.ru/rest/1/abc123",
        responsible_id=5,
        created_by=1,
        http_client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "#77"
    assert mock_client.post.call_args.args[0] == "tasks.task.add"
    fields = mock_client.post.call_args.kwargs["json"]["fields"]
    assert fields["RESPONSIBLE_ID"] == 5
    assert fields["CREATED_BY"] == 1


def test_bitrix24_update_comment():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"result": 1},
        request=httpx.Request("POST", "https://portal.bitrix24.ru/rest/1/abc123/task.commentitem.add"),
    )
    handler = Bitrix24TicketHandler(
        webhook_url="https://portal.bitrix24.ru/rest/1/abc123/",
        responsible_id=5,
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
            "external_ref": "#77",
            "external_meta": {"system": "bitrix24", "task_id": "77"},
        },
        body_text="",
    )
    handler.on_updated(ctx)
    assert mock_client.post.call_args.args[0] == "task.commentitem.add"
    assert mock_client.post.call_args.kwargs["json"]["TASKID"] == 77


def test_bitrix24_close_completes_task():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        200,
        json={"result": True},
        request=httpx.Request("POST", "https://portal.bitrix24.ru/rest/1/abc123/tasks.task.complete"),
    )
    handler = Bitrix24TicketHandler(
        webhook_url="https://portal.bitrix24.ru/rest/1/abc123",
        responsible_id=5,
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
            "external_meta": {"system": "bitrix24", "task_id": "77"},
        },
        body_text="",
    )
    handler.on_closed(ctx)
    methods = [call.args[0] for call in mock_client.post.call_args_list]
    assert "task.commentitem.add" in methods
    assert "tasks.task.complete" in methods
