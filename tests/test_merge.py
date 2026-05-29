"""Tests for grouping merge logic."""

from alarm_manager_server.models.incident import GroupingResult, Incident
from alarm_manager_server.services.grouping.merge import merge_groupings


def _synth(id_: str, child_ids: list[str]) -> Incident:
    return Incident(
        id=id_,
        title="Synthetic",
        severity=1,
        status=1,
        started_at="2024-01-01T00:00:00+00:00",
        is_synthetic=True,
        synthetic_child_ids=child_ids,
    )


def test_merge_owner_wins_over_class():
    owner = GroupingResult(
        children_of={"owner-parent": ["child1"]},
        parent_of={"child1": "owner-parent"},
    )
    class_g = GroupingResult(
        children_of={"class-parent": ["child1", "child2"]},
        parent_of={"child1": "class-parent", "child2": "class-parent"},
    )
    result = merge_groupings(owner, class_g, [])
    assert result.parent_of["child1"] == "owner-parent"
    assert result.parent_of["child2"] == "class-parent"


def test_merge_synthetic_requires_two_children_after_conflicts():
    synth = _synth("__synth__host1", ["c1", "c2", "c3"])
    owner = GroupingResult(
        children_of={"p1": ["c1"]},
        parent_of={"c1": "p1"},
    )
    class_g = GroupingResult()
    result = merge_groupings(owner, class_g, [synth])
    assert result.parent_of.get("c1") == "p1"
    assert result.parent_of.get("c2") == "__synth__host1"
    assert result.parent_of.get("c3") == "__synth__host1"
    assert result.children_of["__synth__host1"] == ["c2", "c3"]
