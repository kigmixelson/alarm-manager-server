"""Tests for Freshdesk plugin and SAYMON incident comments."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import ProcessedIncident
from alarm_manager_server.plugins.freshdesk import FreshdeskTicketHandler
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.worker.incident_comments import (
    annotate_saymon_incidents_on_registration,
    format_saymon_sd_comment,
)
from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext
from alarm_manager_server.worker.tickets import TicketEvent, TicketStore


def _settings(**kwargs) -> Settings:
    return Settings(**kwargs)


def _ctx(action: str = "created") -> TicketHandlerContext:
    return TicketHandlerContext(
        event=TicketEvent(
            action=action,
            ticket_id="T-000001",
            group=None,
            changes=[],
            title="Router-A",
        ),
        ticket={
            "ticket_id": "T-000001",
            "group_key": "owner:x",
            "snapshot": {"title": "Router-A", "member_ids": ["i1"]},
        },
        body_text="stats\nrow",
    )


def test_discover_freshdesk_when_configured():
    cfg = _settings(
        freshdesk_base_url="https://acme.freshdesk.com",
        freshdesk_api_key="key",
        freshdesk_requester_email="bot@example.com",
    )
    handlers = discover_ticket_handlers(cfg)
    assert len(handlers) == 1
    assert isinstance(handlers[0], FreshdeskTicketHandler)


def test_freshdesk_create_ticket():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(
        201,
        json={"id": 42},
        request=httpx.Request("POST", "https://acme.freshdesk.com/api/v2/tickets"),
    )
    handler = FreshdeskTicketHandler(
        base_url="https://acme.freshdesk.com",
        api_key="key",
        requester_email="bot@example.com",
        client=mock_client,
    )
    result = handler.on_created(_ctx())
    assert result is not None
    assert result.external_ref == "#42"
    assert result.external_meta["system"] == "freshdesk"
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["email"] == "bot@example.com"
    assert "Router-A" in payload["subject"]


def test_format_saymon_sd_comment_multi():
    text = format_saymon_sd_comment(
        local_ticket_id="T-000001",
        external_refs={"freshdesk": "#42", "jira": "OPS-1"},
        template="Service Desk ({system}): {external_ref}",
    )
    assert "Freshdesk" in text
    assert "Jira" in text
    assert "T-000001" in text


async def test_annotate_saymon_skips_history(tmp_path):
    store = TicketStore(tmp_path / "tickets.json")
    store._data["tickets"]["T-000001"] = {
        "ticket_id": "T-000001",
        "external_meta": {"external_refs": {"freshdesk": "#42"}},
        "snapshot": {"member_ids": ["active-1", "hist-1"]},
    }
    incidents_by_id = {
        "active-1": ProcessedIncident(
            id="active-1",
            title="A",
            severity=1,
            status=2,
            started_at="2025-01-01T00:00:00+00:00",
            is_history=False,
        ),
        "hist-1": ProcessedIncident(
            id="hist-1",
            title="H",
            severity=1,
            status=3,
            started_at="2025-01-01T00:00:00+00:00",
            is_history=True,
        ),
    }
    events = [
        TicketEvent(
            action="created",
            ticket_id="T-000001",
            group=None,
            changes=[],
            title="G",
        )
    ]
    mock_client = AsyncMock()
    mock_client.add_incident_comment = AsyncMock()
    mock_client.aclose = AsyncMock()

    cfg = _settings(
        saymon_login="u",
        saymon_password="p",
        saymon_base_url="http://saymon",
    )

    with patch(
        "alarm_manager_server.worker.incident_comments.SaymonClient.from_settings",
        return_value=mock_client,
    ):
        await annotate_saymon_incidents_on_registration(
            events,
            store,
            incidents_by_id,
            cfg,
        )

    mock_client.add_incident_comment.assert_called_once()
    assert mock_client.add_incident_comment.call_args.args[0] == "active-1"
    commented = store._data["tickets"]["T-000001"]["external_meta"]["saymon_sd_comments"]
    assert "active-1" in commented
    assert "hist-1" not in commented


async def test_oracle_comment_retry_and_persistence(tmp_path):
    from alarm_manager_server.worker.incident_comments import flush_oracle_comments

    path = tmp_path / "tickets.json"
    store = TicketStore(path)
    store._data["tickets"]["T-1"] = {
        "ticket_id": "T-1",
        "external_meta": {"oracle_comments": [
            {"text": "[Module] Подтверждено", "pending_incident_ids": ["i1", "i2"]},
        ]},
    }
    cfg = Settings(_env_file=None, saymon_login="bot", saymon_password="secret")
    client = AsyncMock()
    client.add_incident_comment.side_effect = [None, RuntimeError("unavailable")]
    with patch("alarm_manager_server.worker.incident_comments.SaymonClient.from_settings", return_value=client):
        await flush_oracle_comments(store, cfg)
        reloaded = TicketStore(path)
        client.add_incident_comment.reset_mock(side_effect=True)
        await flush_oracle_comments(reloaded, cfg)
        client.add_incident_comment.assert_awaited_once_with("i2", "[Module] Подтверждено")
        client.add_incident_comment.reset_mock()
        await flush_oracle_comments(reloaded, cfg)
        client.add_incident_comment.assert_not_awaited()


async def test_oracle_comments_skip_known_history(tmp_path):
    from alarm_manager_server.worker.incident_comments import flush_oracle_comments

    store = TicketStore(tmp_path / "tickets.json")
    item = {"text": "Status", "pending_incident_ids": ["archived", "active"]}
    store._data["tickets"]["T-1"] = {
        "ticket_id": "T-1", "external_meta": {"oracle_comments": [item]},
    }
    cfg = Settings(_env_file=None, saymon_login="bot", saymon_password="secret")
    client = AsyncMock()
    with patch("alarm_manager_server.worker.incident_comments.SaymonClient.from_settings", return_value=client):
        await flush_oracle_comments(store, cfg, {"archived": MagicMock(is_history=True)})
    client.add_incident_comment.assert_awaited_once_with("active", "Status")
    assert item["skipped_incidents"] == {"archived": "history"}
    assert item["pending_incident_ids"] == []
