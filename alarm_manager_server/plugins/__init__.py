"""Optional integrations (Jira, Redmine, …) — loaded when configured in .env."""

__all__ = ["discover_ticket_handlers"]


def __getattr__(name: str):
    if name == "discover_ticket_handlers":
        from .registry import discover_ticket_handlers

        return discover_ticket_handlers
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
