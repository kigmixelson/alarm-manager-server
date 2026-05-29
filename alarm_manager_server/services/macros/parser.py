"""Macro syntax parser — mirrors macroParser.ts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

MacroSelectorKind = Literal["class.id", "class.name", "name"]

MACRO_RE = re.compile(
    r"^\{\{\s*(ancestor|parent)\s*\[\s*([^\]]+?)\s*\]\s*\.\s*properties\s*\[\s*([^\]]+?)\s*\]"
    r"(?:\s*\[\s*(\d+)\s*\])?\s*\}\}$"
)


@dataclass(frozen=True)
class MacroSelector:
    kind: MacroSelectorKind
    values: tuple[str, ...]


@dataclass(frozen=True)
class ParsedMacro:
    raw: str
    walker: Literal["ancestor"]
    selector: MacroSelector
    property_name: str
    index: int | None


def _parse_selector(input_str: str) -> MacroSelector | None:
    trimmed = input_str.strip()
    if not trimmed:
        return None

    id_match = re.match(r"^class\.id\s*=\s*(.+)$", trimmed, re.IGNORECASE)
    if id_match:
        values = tuple(v.strip() for v in id_match.group(1).split(",") if v.strip())
        return MacroSelector(kind="class.id", values=values)

    name_match = re.match(r"^class\.name\s*=\s*(.+)$", trimmed, re.IGNORECASE)
    if name_match:
        values = tuple(v.strip() for v in name_match.group(1).split(",") if v.strip())
        return MacroSelector(kind="class.name", values=values)

    return MacroSelector(kind="name", values=(trimmed,))


def parse_macro(macro: str) -> ParsedMacro | None:
    m = MACRO_RE.match(macro.strip())
    if not m:
        return None
    selector = _parse_selector(m.group(2))
    if selector is None:
        return None
    idx_str = m.group(4)
    return ParsedMacro(
        raw=macro.strip(),
        walker="ancestor",
        selector=selector,
        property_name=m.group(3).strip(),
        index=int(idx_str) if idx_str else None,
    )


def object_matches_selector(obj: dict[str, Any], sel: MacroSelector) -> bool:
    if sel.kind == "class.id":
        class_id = obj.get("class_id")
        return class_id is not None and str(class_id) in sel.values
    if sel.kind == "class.name":
        class_name = obj.get("class_name")
        return bool(class_name) and class_name in sel.values
    name = obj.get("name")
    return bool(name) and name in sel.values


def pick_property(
    obj: dict[str, Any],
    property_name: str,
    index: int | None,
) -> str | None:
    props = obj.get("properties")
    if not isinstance(props, list):
        return None
    found = next((p for p in props if isinstance(p, dict) and p.get("name") == property_name), None)
    if not found:
        return None
    value: Any = found.get("value")
    if index is not None:
        if not isinstance(value, list):
            return None
        if index >= len(value):
            return None
        value = value[index]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
