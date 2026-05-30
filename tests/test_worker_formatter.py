from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.worker.formatter import (
    IncidentGroup,
    build_groups,
    format_groups,
    is_all_cleared_group,
    is_cleared_incident,
)


def _inc(
    id: str,
    *,
    title: str = "Host",
    owner_display_title: str = "",
    is_synthetic: bool = False,
    started_at: str = "2025-01-01T10:00:00+00:00",
    resolved_at: str | None = None,
    status: int | str = 2,
    status_label: str = "critical",
    text: str = "disk full",
    avaria_owner: str | None = None,
    object_display_name: str = "",
) -> ProcessedIncident:
    return ProcessedIncident(
        id=id,
        title=title,
        display_title=title,
        owner_display_title=owner_display_title or title,
        object_display_name=object_display_name,
        severity=1,
        status=status,
        status_label=status_label,
        started_at=started_at,
        resolved_at=resolved_at,
        text=text,
        avaria_owner=avaria_owner,
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
        title="Child",
        started_at="2025-01-01T11:00:00+00:00",
        resolved_at="2025-01-01T12:00:00+00:00",
    )
    grouping = GroupingResult(children_of={"open": ["closed"]}, parent_of={"closed": "open"})
    groups = build_groups([open_inc, closed_inc], grouping, cfg)
    assert len(groups) == 1
    assert "аварий: 2" in groups[0].stats_line
    rows = [r.split("\t") for r in groups[0].rows]
    assert len(rows[0][3]) == len(rows[1][3])
    assert rows[0][3].strip() == ""
    assert rows[0][1] == "Host"
    assert rows[1][1] == "Child"
    assert rows[1][3] == "01.01.2025 12:00"
    assert rows[1][4] == "disk full"
    assert "open" not in groups[0].rows[0]
    assert "closed" not in groups[0].rows[1]


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
    for row in groups[0].rows:
        cols = row.split("\t")
        assert cols[1] in {"A1", "A2"}
        assert cols[4] == "disk full"
        assert "c1" not in row and "c2" not in row


def test_active_only_skips_all_cleared_group():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    cleared = _inc("c1", status=3, status_label="cleared", resolved_at="2025-01-01T11:00:00+00:00")
    open_inc = _inc("o1", status=2, status_label="warning")
    grouping = GroupingResult()
    all_groups = build_groups([cleared], grouping, cfg, active_only=False)
    active_groups = build_groups([cleared, open_inc], grouping, cfg, active_only=True)
    assert len(all_groups) == 1
    assert len(active_groups) == 1
    assert active_groups[0].rows[0].endswith("o1")
    only_cleared = build_groups([cleared], grouping, cfg, active_only=True)
    assert only_cleared == []


def test_is_cleared_by_status_label():
    assert is_cleared_incident(_inc("x", status=2, status_label="cleared"))
    assert is_cleared_incident(_inc("x", status=3, status_label=""))
    assert not is_cleared_incident(_inc("x", status=2, status_label="warning"))
    assert is_all_cleared_group([_inc("a", status=3, status_label="cleared")])


def test_responsible_line_and_row_column():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    inc = _inc("a", avaria_owner="Иванов И.И.")
    groups = build_groups([inc], GroupingResult(), cfg, show_responsible=True)
    assert groups[0].responsible_line == "ответственный: Иванов И.И."
    assert groups[0].rows[0].endswith("\tИванов И.И.")


def test_responsible_includes_cleared_members():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    c1 = _inc("c1", status=3, status_label="cleared", avaria_owner="Иванов И.И.")
    c2 = _inc("c2", status=3, status_label="cleared", avaria_owner="Петров П.П.")
    grouping = GroupingResult(
        children_of={"__synth__e1": ["c1", "c2"]},
        parent_of={"c1": "__synth__e1", "c2": "__synth__e1"},
    )
    synth = _inc("__synth__e1", title="Router-A", is_synthetic=True)
    groups = build_groups([synth, c1, c2], grouping, cfg, show_responsible=True)
    assert groups[0].responsible_line == "ответственные: Иванов И.И., Петров П.П."


def test_responsible_on_active_and_cleared_rows():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    active = _inc("a1", status=2, status_label="warning", avaria_owner="Иванов И.И.")
    cleared = _inc(
        "c1",
        status=3,
        status_label="cleared",
        avaria_owner="Петров П.П.",
        resolved_at="2025-01-01T12:00:00+00:00",
    )
    grouping = GroupingResult(children_of={"a1": ["c1"]}, parent_of={"c1": "a1"})
    groups = build_groups([active, cleared], grouping, cfg, show_responsible=True)
    assert groups[0].responsible_line == "ответственные: Иванов И.И., Петров П.П."
    rows = [r.split("\t") for r in groups[0].rows]
    assert any(row[-1] == "Иванов И.И." for row in rows)
    assert any(row[-1] == "Петров П.П." for row in rows)


def test_object_display_name_used_in_compact_row():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    inc = _inc(
        "c1",
        title="67cb1f06120ab073c5adb78c",
        object_display_name="PSU.#1@R2.saymon",
        status=3,
        status_label="cleared",
    )
    grouping = GroupingResult(
        children_of={"__synth__e1": ["c1"]},
        parent_of={"c1": "__synth__e1"},
    )
    synth = _inc("__synth__e1", title="Router-A", is_synthetic=True)
    groups = build_groups([synth, inc], grouping, cfg)
    cols = groups[0].rows[0].split("\t")
    assert cols[1] == "PSU.#1@R2.saymon"
    assert "67cb1f06120ab073c5adb78c" not in groups[0].rows[0]


def test_responsible_omitted_when_not_found():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    inc = _inc("a", avaria_owner=None)
    groups = build_groups([inc], GroupingResult(), cfg, show_responsible=True)
    assert groups[0].responsible_line is None
    assert groups[0].rows[0].count("\t") == 5
    assert groups[0].rows[0].endswith("\t")


def test_format_groups_separated_by_blank_line():
    text = format_groups(
        [
            IncidentGroup("G1", "первая: — | последняя: — | аварий: 1", ["critical\ta\t\tb\tid1"]),
            IncidentGroup("G2", "стат", ["row1", "row2"]),
        ]
    )
    assert text.startswith("G1\nпервая:")
    assert "\n\nG2\n" in text
