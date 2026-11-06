"""Odoo search domains and field lists, checked before they are sent.

A domain is a list of ``[field, operator, value]`` conditions and the prefix
operators ``"&"``, ``"|"`` and ``"!"``. Field paths must be identifiers whose first
part is a field of the model (from ``fields_get``), operators come from a fixed
list, values are scalars or short lists of scalars.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from typing import Any

from .inputs import invalid

OPERATORS: frozenset[str] = frozenset(
    {
        "=",
        "!=",
        "<",
        "<=",
        ">",
        ">=",
        "in",
        "not in",
        "like",
        "ilike",
        "not like",
        "not ilike",
        "=like",
        "=ilike",
        "child_of",
        "parent_of",
    }
)
LOGIC: frozenset[str] = frozenset({"&", "|", "!"})
MAX_CONDITIONS = 50
MAX_LIST = 1000
MAX_TEXT = 500
MAX_FIELDS = 200
_PATH_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*){0,4}$")
_ORDER_RE = re.compile(r"^[a-z_][a-z0-9_]*( (asc|desc))?$", re.IGNORECASE)
#: Fields every model has even when fields_get leaves them out.
_ALWAYS = frozenset({"id", "display_name"})


def _scalar(where: str, value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= MAX_TEXT and value.isprintable():
        return value
    raise invalid(f"{where} must be short printable text, a number, true/false or null")


def _field(where: str, path: Any, known: Collection[str] | None) -> str:
    if not isinstance(path, str) or not _PATH_RE.fullmatch(path):
        raise invalid(f"{where} is not a field name")
    head = path.split(".", 1)[0]
    if known is not None and head not in known and head not in _ALWAYS:
        raise invalid(f"{where}: the model has no field {head}")
    return path


def domain(raw: Any, known: Collection[str] | None) -> list[Any]:
    """A checked domain; ``known`` is the model's field names (None skips that check)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_CONDITIONS:
        raise invalid(f"domain must be a list of at most {MAX_CONDITIONS} conditions")
    checked: list[Any] = []
    for index, item in enumerate(raw):
        where = f"domain[{index}]"
        if isinstance(item, str):
            if item not in LOGIC:
                raise invalid(f"{where} must be a condition or one of & | !")
            checked.append(item)
            continue
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise invalid(f"{where} must be [field, operator, value]")
        path, operator, value = item
        path = _field(f"{where} field", path, known)
        if not isinstance(operator, str) or operator.lower() not in OPERATORS:
            raise invalid(f"{where} operator must be one of: {', '.join(sorted(OPERATORS))}")
        if isinstance(value, (list, tuple)):
            if len(value) > MAX_LIST:
                raise invalid(f"{where} value lists may have at most {MAX_LIST} items")
            value = [_scalar(f"{where} value", v) for v in value]
        else:
            value = _scalar(f"{where} value", value)
        checked.append([path, operator.lower(), value])
    return checked


def field_list(raw: Any, known: Collection[str] | None, *, name: str = "fields") -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        raise invalid(f"{name} must be a list of at most {MAX_FIELDS} field names")
    return [_field(f"{name}[{i}]", item, known) for i, item in enumerate(raw)]


def order(raw: Any, known: Collection[str] | None) -> str | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw) > 200:
        raise invalid("order must be text such as 'write_date desc, id'")
    parts = [part.strip() for part in raw.split(",")]
    for part in parts:
        if not _ORDER_RE.fullmatch(part):
            raise invalid("order must be field names with optional asc/desc, comma separated")
        _field("order", part.split(" ", 1)[0], known)
    return ", ".join(parts)


def values(raw: Any, known: Collection[str] | None, *, name: str = "values") -> dict[str, Any]:
    """Field values for create/write: known top-level field names, JSON values."""
    if not isinstance(raw, dict) or not raw:
        raise invalid(f"{name} must be an object of field values")
    if len(raw) > MAX_FIELDS:
        raise invalid(f"{name} may set at most {MAX_FIELDS} fields")
    for key in raw:
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
            raise invalid(f"{name}: {key!r} is not a field name")
        if known is not None and key not in known:
            raise invalid(f"{name}: the model has no field {key}")
    return dict(raw)


def ids(raw: Any, *, name: str = "ids", limit: int = 1000) -> list[int]:
    if not isinstance(raw, list) or not raw or len(raw) > limit:
        raise invalid(f"{name} must be a list of 1 to {limit} record ids")
    checked = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise invalid(f"{name} must contain positive whole numbers")
        checked.append(item)
    return checked


__all__ = ["LOGIC", "OPERATORS", "domain", "field_list", "ids", "order", "values"]
