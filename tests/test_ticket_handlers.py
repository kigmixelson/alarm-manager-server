from pathlib import Path

from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.worker.formatter import build_tracked_groups
from alarm_manager_server.worker.ticket_handlers import (
    HandlerResult,
    LoggingTicketHandler,
    dispatch_ticket_handlers,
    load_ticket_handlers,
)
from alarm_manager_server.worker.tickets import TicketStore, sync_tickets


def _inc(id: str, **kwargs) -> ProcessedIncident:
    defaults = dict(
        title="Host",
        display_title="Host",
        owner_display_title="Host",
        object_display_name="Host",
        severity=1,
        status=2,
        status_label="warning",
        started_at="2025-01-01T10:00:00+00:00",
        text="alarm",
    )
    defaults.update(kwargs)
    return ProcessedIncident(id=id, **defaults)


def test_logging_handler_sets_external_ref(tmp_path: Path):
    path = tmp_path / "tickets.json"
    store = TicketStore(path)
    cfg = Settings()
    inc = _inc("a1")
    groups = build_tracked_groups([inc], GroupingResult(), cfg)
    result = sync_tickets(store, groups, incidents_by_id={"a1": inc})
    handlers = [LoggingTicketHandler()]
    dispatch_ticket_handlers(handlers, store, result.events)
    ticket = store._data["tickets"][result.events[0].ticket_id]
    assert ticket["external_ref"] == "log:T-000001"


def test_load_handler_by_spec():
    handlers = load_ticket_handlers(
        ["alarm_manager_server.worker.ticket_handlers:LoggingTicketHandler"]
    )
    assert len(handlers) == 1
    assert isinstance(handlers[0], LoggingTicketHandler)


class _CaptureHandler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_ticket_event(self, ctx) -> HandlerResult | None:
        self.calls.append(ctx.event.action)
        if ctx.event.action == "created":
            return HandlerResult(external_ref="EXT-1")
        return None


def test_dispatch_custom_callable_instance(tmp_path: Path):
    path = tmp_path / "tickets.json"
    store = TicketStore(path)
    cfg = Settings()
    inc = _inc("a1")
    groups = build_tracked_groups([inc], GroupingResult(), cfg)
    result = sync_tickets(store, groups, incidents_by_id={"a1": inc})
    handler = _CaptureHandler()
    dispatch_ticket_handlers([handler], store, result.events)
    assert handler.calls == ["created"]
    assert store._data["tickets"]["T-000001"]["external_ref"] == "EXT-1"
