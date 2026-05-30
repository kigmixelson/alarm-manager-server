from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.worker.formatter import (
    IncidentGroup,
    build_groups,
    format_groups,
)


def _inc(
    id: str,
    *,
    title: str = "Host",
    owner_display_title: str = "",
    is_synthetic: bool = False,
    started_at: str = "2025-01-01T10:00:00+00:00",
    resolved_at: str | None = None,
    status_label: str = "critical",
    text: str = "disk full",
) -> ProcessedIncident:
    return ProcessedIncident(
        id=id,
        title=title,
        display_title=title,
        owner_display_title=owner_display_title or title,
        severity=1,
        status=3,
        status_label=status_label,
        started_at=started_at,
        resolved_at=resolved_at,
        text=text,
        is_synthetic=is_synthetic,
    )


def test_singleton_group_header_and_tab_row():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    incidents = [
        _inc(
            "a",
            owner_display_title="Lonely (Router-1)",
            started_at="2025-01-02T12:00:00+00:00",
            resolved_at="2025-01-02T13:00:00+00:00",
        )
    ]
    groups = build_groups(incidents, GroupingResult(), cfg)
    assert len(groups) == 1
    assert groups[0].title == "Lonely (Router-1)"
    assert "аварий: 1" in groups[0].stats_line
    assert "02.01.2025" in groups[0].stats_line
    row = groups[0].rows[0].split("\t")
    assert row[0] == "critical"
    assert row[1] == "02.01.2025 12:00"
    assert row[2] == "02.01.2025 13:00"
    assert row[3] == "disk full"
    assert row[4] == "a"


def test_open_incident_has_padded_empty_closed_column():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    open_inc = _inc("open", started_at="2025-01-01T10:00:00+00:00", resolved_at=None)
    closed_inc = _inc(
        "closed",
        started_at="2025-01-01T11:00:00+00:00",
        resolved_at="2025-01-01T12:00:00+00:00",
    )
    grouping = GroupingResult(children_of={"open": ["closed"]}, parent_of={"closed": "open"})
    groups = build_groups([open_inc, closed_inc], grouping, cfg)
    assert len(groups) == 1
    assert "аварий: 2" in groups[0].stats_line
    rows = [r.split("\t") for r in groups[0].rows]
    assert len(rows[0][2]) == len(rows[1][2])
    assert rows[0][2].strip() == ""


def test_synthetic_group_lists_children_only():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    synth = _inc("__synth__e1", title="Router-A", is_synthetic=True, status_label="")
    c1 = _inc("c1", title="A1", started_at="2025-01-01T08:00:00+00:00")
    c2 = _inc("c2", title="A2", started_at="2025-01-03T08:00:00+00:00")
    grouping = GroupingResult(
        children_of={"__synth__e1": ["c1", "c2"]},
        parent_of={"c1": "__synth__e1", "c2": "__synth__e1"},
    )
    groups = build_groups([synth, c1, c2], grouping, cfg)
    assert groups[0].title == "Router-A"
    assert "аварий: 2" in groups[0].stats_line
    assert len(groups[0].rows) == 2
    assert all("c1" in r or "c2" in r for r in groups[0].rows)


def test_format_groups_separated_by_blank_line():
    text = format_groups(
        [
            IncidentGroup("G1", "первая: — | последняя: — | аварий: 1", ["critical\ta\t\tb\tid1"]),
            IncidentGroup("G2", "стат", ["row1", "row2"]),
        ]
    )
    assert text.startswith("G1\nпервая:")
    assert "\n\nG2\n" in text
