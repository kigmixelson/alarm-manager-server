"""HTTP client for alarm-manager-server /process endpoint."""

from __future__ import annotations

from typing import Any

import httpx

from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident


class ProcessApiClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def process(self, *, resolve_macros: bool = True) -> tuple[list[ProcessedIncident], GroupingResult]:
        params = {"resolve_macros": "true" if resolve_macros else "false"}
        async with httpx.AsyncClient(base_url=self._base, timeout=self._timeout) as client:
            response = await client.post("/process", params=params)
            if response.is_error:
                detail = response.text.strip()[:2000]
                raise httpx.HTTPStatusError(
                    f"{response.status_code} {response.reason_phrase}"
                    + (f": {detail}" if detail else ""),
                    request=response.request,
                    response=response,
                )
            data: dict[str, Any] = response.json()

        incidents = [ProcessedIncident.model_validate(item) for item in data.get("incidents", [])]
        grouping_raw = data.get("grouping", {})
        grouping = GroupingResult.model_validate(grouping_raw)
        return incidents, grouping
