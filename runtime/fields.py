"""Reading Rivile ERP JSON without trusting the case of its field names."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def field(record: Any, name: str) -> Any:
    """A field of a Rivile ERP object, matched case-insensitively (exact match first)."""
    if not isinstance(record, Mapping):
        return None
    if name in record:
        return record[name]
    lowered = name.lower()
    for key, value in record.items():
        if isinstance(key, str) and key.lower() == lowered:
            return value
    return None


__all__ = ["field"]
