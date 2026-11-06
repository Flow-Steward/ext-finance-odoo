"""Input checks shared by every operation."""

from __future__ import annotations

import re
from typing import Any

from . import errors
from .errors import ExtensionError

_DIGITS = re.compile(r"[0-9]{1,9}")


def invalid(message: str) -> ExtensionError:
    """A refused input: nothing was sent, so there was no external effect."""
    return ExtensionError(
        errors.INVALID_PAYLOAD,
        message,
        definitely_no_external_effect=True,
        external_effect_status="failed",
    )


def whole(name: str, value: Any, *, default: int, maximum: int, minimum: int = 1) -> int:
    """A whole number from ``minimum`` to ``maximum``; ASCII digit strings are accepted."""
    if value is None:
        return default
    if isinstance(value, str) and _DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise invalid(f"{name} must be a whole number from {minimum} to {maximum}")
    return value


def flag(name: str, value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise invalid(f"{name} must be true or false")
    return value


def text(name: str, value: Any, *, limit: int = 200, required: bool = False) -> str:
    """Printable text up to ``limit`` characters."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise invalid(f"{name} is required")
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise invalid(f"{name} must be text")
    result = str(value).strip()
    if len(result) > limit or not result.isprintable():
        raise invalid(f"{name} must be at most {limit} printable characters")
    return result


__all__ = ["flag", "invalid", "text", "whole"]
