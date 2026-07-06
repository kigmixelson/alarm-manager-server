"""Jira Cloud / Server — create issue on CREATE, comment on UPDATE/CLOSE."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins._format import (
    close_comment,
    issue_description,
    issue_title,
    update_comment,
)
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler,
    HandlerResult,
    TicketHandlerContext,
)

logger = logging.getLogger(__name__)


class JiraTicketHandler(BaseTicketHandler):
    def __init__(
        self,
        *,
        base_url: str,
        user: str,
        api_token: str,
        project_key: str,
        issue_type: str = "Task",
        close_transition: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.api_token = api_token
        self.project_key = project_key
        self.issue_type = issue_type
        self.close_transition = close_transition.strip()
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_settings(cls, cfg: Settings) -> JiraTicketHandler | None:
        if not cfg.jira_enabled:
            return None
        return cls(
            base_url=cfg.jira_base_url,
            user=cfg.jira_user,
            api_token=cfg.jira_api_token.get_secret_value(),
            project_key=cfg.jira_project_key,
            issue_type=cfg.jira_issue_type,
            close_transition=cfg.jira_close_transition,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                auth=(self.user, self.api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=30.0,
            )
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def on_created(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": issue_title(ctx),
                "description": issue_description(ctx),
                "issuetype": {"name": self.issue_type},
            }
        }
        resp = self._http().post("/rest/api/2/issue", json=payload)
        resp.raise_for_status()
        data = resp.json()
        issue_key = str(data.get("key") or data.get("id") or "")
        if not issue_key:
            raise RuntimeError(f"Jira create returned no key: {data!r}")
        logger.info("Jira issue created %s for %s", issue_key, ctx.event.ticket_id)
        return HandlerResult(
            external_ref=issue_key,
            external_meta={"system": "jira", "issue_key": issue_key},
        )

    def on_updated(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        issue_key = _jira_issue_key(ctx)
        if not issue_key:
            return self.on_created(ctx)
        self._add_comment(issue_key, update_comment(ctx))
        return None

    def on_closed(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        issue_key = _jira_issue_key(ctx)
        if not issue_key:
            return None
        self._add_comment(issue_key, close_comment(ctx))
        if self.close_transition:
            self._apply_transition(issue_key, self.close_transition)
        return None

    def _add_comment(self, issue_key: str, body: str) -> None:
        resp = self._http().post(
            f"/rest/api/2/issue/{issue_key}/comment",
            json={"body": body},
        )
        resp.raise_for_status()
        logger.info("Jira comment added to %s", issue_key)

    def _apply_transition(self, issue_key: str, transition_name: str) -> None:
        resp = self._http().get(f"/rest/api/2/issue/{issue_key}/transitions")
        resp.raise_for_status()
        transitions = resp.json().get("transitions") or []
        target = _find_transition(transitions, transition_name)
        if target is None:
            logger.warning(
                "Jira transition %r not found for %s (available: %s)",
                transition_name,
                issue_key,
                [t.get("name") for t in transitions],
            )
            return
        tid = target.get("id")
        resp = self._http().post(
            f"/rest/api/2/issue/{issue_key}/transitions",
            json={"transition": {"id": tid}},
        )
        resp.raise_for_status()
        logger.info("Jira transition %s applied to %s", transition_name, issue_key)


def _jira_issue_key(ctx: TicketHandlerContext) -> str:
    meta = ctx.ticket.get("external_meta") or {}
    refs = meta.get("external_refs")
    if isinstance(refs, dict) and refs.get("jira"):
        return str(refs["jira"])
    if meta.get("system") == "jira" and meta.get("issue_key"):
        return str(meta["issue_key"])
    ref = ctx.ticket.get("external_ref")
    if isinstance(ref, str) and ref.strip() and not ref.lstrip("#").isdigit():
        return ref.strip()
    return ""


def _find_transition(transitions: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    name_cf = name.casefold()
    for t in transitions:
        if str(t.get("name", "")).casefold() == name_cf:
            return t
        if str(t.get("id", "")) == name:
            return t
    return None
