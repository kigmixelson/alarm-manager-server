"""Normalize SAYMON JSON response shapes."""

from __future__ import annotations

from typing import Any


class SaymonResponseError(ValueError):
    """Unexpected JSON body from SAYMON API."""


def coerce_json_list(data: Any, *, label: str = "response") -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "incidents", "results", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    raise SaymonResponseError(f"SAYMON {label}: expected a JSON array, got {type(data).__name__}")
