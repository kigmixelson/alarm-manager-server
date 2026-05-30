from alarm_manager_server.models.incident import ProcessedIncident
from alarm_manager_server.worker.formatter import _format_incident_row


def _inc(id: str, *, title: str = "svc-1") -> ProcessedIncident:
    return ProcessedIncident(
        id=id,
        title=title,
        severity=1,
        status=2,
        status_label="warning",
        started_at="2025-01-01T10:00:00+00:00",
        text="alert",
    )


def test_child_row_uses_object_display_name():
    row = _format_incident_row(
        ProcessedIncident(
            id="child-id",
            title="67cb1f06120ab073c5adb78c",
            object_display_name="PSU.#1",
            severity=1,
            status=2,
            status_label="warning",
            started_at="2025-01-01T10:00:00+00:00",
            text="alert",
        ),
        closed_width=16,
        show_responsible=False,
        is_child=True,
    )
    cols = row.split("\t")
    assert cols[1] == "PSU.#1"


def test_child_row_uses_object_name_and_omits_id():
    row = _format_incident_row(
        _inc("child-id", title="PSU.#1"),
        closed_width=16,
        show_responsible=False,
        is_child=True,
    )
    cols = row.split("\t")
    assert cols[0] == "warning"
    assert cols[1] == "PSU.#1"
    assert cols[2] == "01.01.2025 10:00"
    assert cols[4] == "alert"
    assert "child-id" not in row


def test_parent_row_keeps_id_in_last_data_column():
    row = _format_incident_row(
        _inc("parent-id"),
        closed_width=0,
        show_responsible=False,
        is_child=False,
    )
    cols = row.split("\t")
    assert cols[1] == "01.01.2025 10:00"
    assert cols[4] == "parent-id"
