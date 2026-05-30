import pytest

from alarm_manager_server.models.incident import Incident
from alarm_manager_server.services.processor import merge_active_and_history_incidents
from alarm_manager_server.saymon.client import SaymonClient


def _inc(id_: str, *, is_history: bool, ts: int) -> Incident:
    return Incident(
        id=id_,
        title=f"inc-{id_}",
        severity=1,
        status=1,
        started_at="2024-01-01T00:00:00+00:00",
        is_history=is_history,
        raw={"localTimestamp": ts, "__isHistory": is_history},
    )


def test_merge_prefers_active_over_history_for_same_id():
    history = _inc("same", is_history=True, ts=3000)
    active = _inc("same", is_history=False, ts=1000)
    merged = merge_active_and_history_incidents([active], [history])
    assert len(merged) == 1
    assert merged[0].is_history is False


def test_merge_keeps_distinct_ids_sorted_by_opened_at():
    merged = merge_active_and_history_incidents(
        [_inc("a", is_history=False, ts=2000)],
        [_inc("b", is_history=True, ts=3000), _inc("c", is_history=True, ts=1000)],
    )
    assert [inc.id for inc in merged] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_fetch_paginated_loads_multiple_pages():
    calls: list[str] = []

    class FakeClient(SaymonClient):
        async def _get_json(self, path: str):
            calls.append(path)
            if "skip=0" in path:
                return [{"id": "1"}, {"id": "2"}]
            if "skip=2" in path:
                return [{"id": "3"}]
            raise AssertionError(f"unexpected path: {path}")

    client = object.__new__(SaymonClient)
    client._fetch_paginated = SaymonClient._fetch_paginated.__get__(client, SaymonClient)

    rows = await client._fetch_paginated(
        "/incidents",
        max_items=10,
        query={"owner": "true"},
        page_size=2,
    )
    assert [row["id"] for row in rows] == ["1", "2", "3"]
    assert len(calls) == 2
