from alarm_manager_server.config import Settings
from alarm_manager_server.models.incident import GroupingResult, ProcessedIncident
from alarm_manager_server.worker.formatter import (
    IncidentGroup,
    build_groups,
    format_groups,
    incident_link,
)


def _inc(
    id: str,
    *,
    title: str = "Host",
    display_title: str = "",
    owner_display_title: str = "",
    is_synthetic: bool = False,
) -> ProcessedIncident:
    return ProcessedIncident(
        id=id,
        title=title,
        display_title=display_title or title,
        owner_display_title=owner_display_title or title,
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        is_synthetic=is_synthetic,
    )


def test_singleton_group_uses_owner_display_title():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    incidents = [_inc("a", title="Lonely", owner_display_title="Lonely (Router-1)")]
    grouping = GroupingResult()
    groups = build_groups(incidents, grouping, cfg)
    assert len(groups) == 1
    assert groups[0].name == "Lonely (Router-1)"
    assert groups[0].links == ["http://saymon/i/a"]


def test_merged_owner_group_lists_parent_and_children():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    parent = _inc("p", title="Parent host")
    child = _inc("c", title="Child")
    grouping = GroupingResult(children_of={"p": ["c"]}, parent_of={"c": "p"})
    groups = build_groups([parent, child], grouping, cfg)
    assert len(groups) == 1
    assert groups[0].name == "Parent host"
    assert groups[0].links == ["http://saymon/i/p", "http://saymon/i/c"]


def test_synthetic_group_name_and_child_links_only():
    cfg = Settings(saymon_base_url="http://saymon", incident_link_template="{saymon_base_url}/i/{id}")
    synth = _inc("__synth__e1", title="Router-A", is_synthetic=True)
    c1 = _inc("c1", title="A1")
    c2 = _inc("c2", title="A2")
    grouping = GroupingResult(
        children_of={"__synth__e1": ["c1", "c2"]},
        parent_of={"c1": "__synth__e1", "c2": "__synth__e1"},
    )
    groups = build_groups([synth, c1, c2], grouping, cfg)
    assert len(groups) == 1
    assert groups[0].name == "Router-A"
    assert groups[0].links == ["http://saymon/i/c1", "http://saymon/i/c2"]


def test_format_groups_separated_by_blank_line():
    text = format_groups(
        [
            IncidentGroup("G1", ["http://x/1"]),
            IncidentGroup("G2", ["http://x/2", "http://x/3"]),
        ]
    )
    assert text == "G1\nhttp://x/1\n\nG2\nhttp://x/2\nhttp://x/3"


def test_incident_link_template():
    cfg = Settings(
        saymon_base_url="http://host/",
        incident_link_template="{saymon_base_url}/apps?id={id}",
    )
    assert incident_link("42", cfg) == "http://host/apps?id=42"
