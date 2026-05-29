"""Tests for macro parser."""

from alarm_manager_server.services.macros.parser import (
    object_matches_selector,
    parse_macro,
    pick_property,
    MacroSelector,
)


def test_parse_macro_class_id():
    macro = parse_macro("{{parent[class.id=30,3,24].properties[17. Ответственный]}}")
    assert macro is not None
    assert macro.selector.kind == "class.id"
    assert macro.selector.values == ("30", "3", "24")
    assert macro.property_name == "17. Ответственный"
    assert macro.index is None


def test_parse_macro_with_index():
    macro = parse_macro("{{ancestor[class.name=Host].properties[tags][0]}}")
    assert macro is not None
    assert macro.selector.kind == "class.name"
    assert macro.index == 0


def test_object_matches_selector():
    sel = MacroSelector(kind="class.id", values=("30",))
    assert object_matches_selector({"class_id": 30}, sel)
    assert not object_matches_selector({"class_id": 5}, sel)


def test_pick_property():
    obj = {"properties": [{"name": "17. Ответственный", "value": "Ivanov"}]}
    assert pick_property(obj, "17. Ответственный", None) == "Ivanov"

    obj_arr = {"properties": [{"name": "tags", "value": ["a", "b"]}]}
    assert pick_property(obj_arr, "tags", 1) == "b"
