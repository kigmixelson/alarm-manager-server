"""Optional integrations (Jira, Redmine, …) — loaded when configured in .env."""

from alarm_manager_server.plugins.registry import discover_ticket_handlers

__all__ = ["discover_ticket_handlers"]
