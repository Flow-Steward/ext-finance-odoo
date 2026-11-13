"""BRANDING.md records the provenance of the icon.png that ships in the bundle."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _branding() -> str:
    return (BUNDLE_ROOT / "BRANDING.md").read_text(encoding="utf-8")


def test_branding_records_the_shipped_icon_hash() -> None:
    icon = hashlib.sha256((BUNDLE_ROOT / "icon.png").read_bytes()).hexdigest()
    recorded = re.search(r"SHA-256 of `icon\.png`: `([0-9a-f]{64})`", _branding())
    assert recorded is not None, "BRANDING.md must record the SHA-256 of icon.png"
    assert recorded.group(1) == icon


def test_branding_records_the_official_source() -> None:
    text = _branding()
    assert (
        "https://raw.githubusercontent.com/odoo/odoo/19.0/addons/web/static/img/odoo-icon.svg"
        in text
    )
    assert re.search(r"SHA-256 of the source: `[0-9a-f]{64}`", text)
    assert "https://www.odoo.com/page/brand-assets" in text
