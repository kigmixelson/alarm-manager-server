from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class IncidentOwner(BaseModel):
    id: str = Field(alias="_id")
    name: str = ""
    class_id: str | int | None = None
    parent_id: list[str] = Field(default_factory=list)
    properties: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _normalize_owner_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "_id" not in data and "id" in data:
            return {**data, "_id": data["id"]}
        return data

    @field_validator("class_id", mode="before")
    @classmethod
    def _coerce_class_id(cls, value: Any) -> str | int | None:
        if value is None or value == "":
            return None
        return value

    @field_validator("parent_id", mode="before")
    @classmethod
    def _coerce_parent_id(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value if v is not None and str(v)]
        return [str(value)]


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
        owner = _parse_owner(raw.get("owner"))
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
    owner_display_title: str = ""


def _parse_owner(owner_data: Any) -> IncidentOwner | None:
    if not isinstance(owner_data, dict):
        return None
    try:
        return IncidentOwner.model_validate(owner_data)
    except Exception:
        owner_id = owner_data.get("_id") or owner_data.get("id")
        if owner_id is None:
            return None
        return IncidentOwner(
            id=str(owner_id),
            name=str(owner_data.get("name") or ""),
            class_id=owner_data.get("class_id"),
            parent_id=owner_data.get("parent_id") or [],
            properties=owner_data.get("properties")
            if isinstance(owner_data.get("properties"), list)
            else [],
        )


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
