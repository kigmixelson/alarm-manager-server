"""Discover ticket notification plugins enabled via Settings / .env."""

from __future__ import annotations

import logging

from alarm_manager_server.config import Settings, settings
from alarm_manager_server.plugins.bitrix24 import Bitrix24TicketHandler
from alarm_manager_server.plugins.elma import ElmaTicketHandler
from alarm_manager_server.plugins.freshdesk import FreshdeskTicketHandler
from alarm_manager_server.plugins.hpsm import HpServiceManagerTicketHandler
from alarm_manager_server.plugins.jira import JiraTicketHandler
from alarm_manager_server.plugins.redmine import RedmineTicketHandler
from alarm_manager_server.plugins.naumen import NaumenTicketHandler
from alarm_manager_server.plugins.servicenow import ServiceNowTicketHandler
from alarm_manager_server.plugins.simpleone import SimpleOneTicketHandler
from alarm_manager_server.worker.ticket_handlers import TicketHandler

logger = logging.getLogger(__name__)

_PLUGIN_FACTORIES = (
    ("jira", JiraTicketHandler.from_settings),
    ("redmine", RedmineTicketHandler.from_settings),
    ("freshdesk", FreshdeskTicketHandler.from_settings),
    ("servicenow", ServiceNowTicketHandler.from_settings),
    ("simpleone", SimpleOneTicketHandler.from_settings),
    ("naumen", NaumenTicketHandler.from_settings),
    ("elma", ElmaTicketHandler.from_settings),
    ("bitrix24", Bitrix24TicketHandler.from_settings),
    ("hpsm", HpServiceManagerTicketHandler.from_settings),
)


def discover_ticket_handlers(cfg: Settings | None = None) -> list[TicketHandler]:
    """Return handlers for each integration that has required env vars set."""
    cfg = cfg or settings
    handlers: list[TicketHandler] = []
    for name, factory in _PLUGIN_FACTORIES:
        try:
            handler = factory(cfg)
        except Exception:
            logger.exception("failed to init %s ticket plugin", name)
            continue
        if handler is not None:
            handlers.append(handler)
            logger.info("ticket plugin enabled: %s", name)
    return handlers
