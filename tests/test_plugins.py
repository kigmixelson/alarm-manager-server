"""Tests for Jira / Redmine ticket notification plugins."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.jira import JiraTicketHandler
from alarm_manager_server.plugins.redmine import RedmineTicketHandler
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext
from alarm_manager_server.worker.tickets import TicketEvent


def _settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def _ctx(action: str = "created", external_ref: str | None = None) -> TicketHandlerContext:
    event = TicketEvent(
        action=action,
        ticket_id="T-000001",
        group=None,
        changes=["test change"] if action == "updated" else [],
        close_reason="all_cleared" if action == "closed" else None,
        title="Router-A",
    )
    ticket: dict = {
        "ticket_id": "T-000001",
        "group_key": "owner:abc",
        "snapshot": {"title": "Router-A", "member_ids": ["i1"]},
    }
    if external_ref:
        ticket["external_ref"] = external_ref
    return TicketHandlerContext(event=event, ticket=ticket, body_text="line1\nline2")


def test_discover_empty_without_config():
    assert discover_ticket_handlers(_settings()) == []


def test_discover_jira_when_configured():
    cfg = _settings(
        jira_base_url="https://jira.example.com",
        jira_user="bot@example.com",
        jira_api_token="secret",
        jira_project_key="OPS",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], JiraTicketHandler)


def test_discover_both_plugins():
    cfg = _settings(
        jira_base_url="https://jira.example.com",
        jira_user="u",
        jira_api_token="t",
        jira_project_key="OPS",
        redmine_base_url="https://redmine.example.com",
        redmine_api_key="k",
        redmine_project_id="42",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 2


def test_jira_create_issue():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"key": "OPS-99", "id": "10099"},
        request=httpx.Request("POST", "https://jira.example.com/rest/api/2/issue"),
    )
    handler = JiraTicketHandler(
        base_url="https://jira.example.com",
        user="u@example.com",
        api_token="token",
        project_key="OPS",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "OPS-99"
    assert result.external_meta["system"] == "jira"
    mock_client.post.assert_called_once()
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["fields"]["project"]["key"] == "OPS"
    assert "Router-A" in payload["fields"]["summary"]


def test_jira_update_adds_comment():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"id": "1"},
        request=httpx.Request("POST", "https://jira.example.com/rest/api/2/issue/OPS-1/comment"),
    )
    handler = JiraTicketHandler(
        base_url="https://jira.example.com",
        user="u",
        api_token="t",
        project_key="OPS",
        client=mock_client,
    )
    handler.on_updated(_ctx("updated", external_ref="OPS-1"))
    mock_client.post.assert_called_once()
    assert "/OPS-1/comment" in mock_client.post.call_args.args[0]


def test_redmine_create_issue():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"issue": {"id": 77}},
        request=httpx.Request("POST", "https://rm.example.com/issues.json"),
    )
    handler = RedmineTicketHandler(
        base_url="https://rm.example.com",
        api_key="key",
        project_id="5",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "#77"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["issue"]["project_id"] == 5


def test_redmine_close_with_status():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.put.return_value = httpx.Response(
        200,
        request=httpx.Request("PUT", "https://rm.example.com/issues/77.json"),
    )
    handler = RedmineTicketHandler(
        base_url="https://rm.example.com",
        api_key="key",
        project_id="5",
        status_closed_id=5,
        client=mock_client,
    )
    ticket = {
        "external_ref": "#77",
        "external_meta": {"system": "redmine", "issue_id": "77"},
    }
    ctx = TicketHandlerContext(
        event=TicketEvent(
            action="closed",
            ticket_id="T-1",
            group=None,
            changes=[],
            close_reason="all_cleared",
            title="G",
        ),
        ticket=ticket,
        body_text="",
    )
    handler.on_closed(ctx)
    payload = mock_client.put.call_args.kwargs["json"]
    assert payload["issue"]["status_id"] == 5
