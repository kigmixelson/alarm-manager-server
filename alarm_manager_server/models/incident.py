from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IncidentOwner(BaseModel):
    id: str = Field(alias="_id")
    name: str = ""
    class_id: int | None = None
    parent_id: list[str] = Field(default_factory=list)
    properties: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class Incident(BaseModel):
    """UI-compatible incident model (mirrors frontend Avaria + raw flags)."""

    id: str
    title: str
    severity: int | str
    status: int | str
    service: str = ""
    started_at: str
    resolved_at: str | None = None
    assignee: str = ""
    description: str = ""
    text: str = ""
    entity_id: str = ""
    is_history: bool = False
    is_synthetic: bool = False
    synthetic_child_ids: list[str] = Field(default_factory=list)
    owner: IncidentOwner | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_api(cls, raw: dict[str, Any], *, is_history: bool = False) -> Incident:
        owner_data = raw.get("owner")
        owner = IncidentOwner.model_validate(owner_data) if owner_data else None
        entity_id = str(raw.get("entityId") or "")
        title = owner.name if owner else entity_id
        ts = raw.get("localTimestamp") or raw.get("timestamp")
        started_at = _ms_to_iso(ts)

        clear_ts = raw.get("clearTimestamp")
        resolved_at = _ms_to_iso(clear_ts) if clear_ts else None

        enriched = dict(raw)
        enriched["__isHistory"] = is_history

        return cls(
            id=str(raw["id"]),
            title=title,
            severity=raw.get("lastState", 0),
            status=raw.get("state", 0),
            service=title,
            started_at=started_at,
            resolved_at=resolved_at,
            assignee=str(raw.get("acknowledgedBy") or ""),
            description=str(raw.get("text") or ""),
            text=str(raw.get("text") or ""),
            entity_id=entity_id,
            is_history=is_history,
            owner=owner,
            raw=enriched,
        )


class SyntheticGroupSeed(BaseModel):
    entity_id: str
    name: str
    child_ids: list[str]


class GroupingResult(BaseModel):
    children_of: dict[str, list[str]] = Field(default_factory=dict)
    parent_of: dict[str, str] = Field(default_factory=dict)
    parent_title_of: dict[str, str] = Field(default_factory=dict)


class ProcessedIncident(Incident):
    """Incident enriched with grouping metadata and responsible party."""

    avaria_owner: str | None = None
    parent_title: str | None = None
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    display_title: str = ""


def _ms_to_iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        ms = int(value)
        if ms <= 0:
            return ""
        from datetime import UTC, datetime

        return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()
    except (TypeError, ValueError):
        return str(value)


def get_opened_at_ms(incident: Incident) -> int:
    raw = incident.raw
    for key in ("localTimestamp", "timestamp"):
        v = raw.get(key)
        if v is not None:
            try:
                n = int(v)
                if n > 0:
                    return n
            except (TypeError, ValueError):
                pass
    if incident.started_at:
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(incident.started_at.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return 0


def is_active(incident: Incident) -> bool:
    return not incident.is_history
