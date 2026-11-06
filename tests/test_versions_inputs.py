"""Version table, connection validation and domain checks."""

from __future__ import annotations

import pytest

from runtime import domain, versions
from runtime.connection import connection_from_payload
from runtime.errors import ExtensionError


def test_version_table_matches_the_odoo_sources() -> None:
    expected = {
        "sale_line_uom": ["product_uom"] * 3 + ["product_uom_id"] * 2,
        "quant_uom": ["product_uom_id"] * 4 + ["uom_id"],
        "attachment_content": ["datas"] * 4 + ["raw"],
        "payment_memo": ["ref", "ref", "memo", "memo", "memo"],
        "pricelist_percent": ["percent_price"] * 4 + [""],
        "read_group_style": ["fields"] * 4 + ["aggregates"],
    }
    for key, values in expected.items():
        assert [versions.name(key, v) for v in (16, 17, 18, 19, 20)] == values, key


def test_product_types_per_version() -> None:
    assert versions.product_type_values(16, service=False, storable=True) == {
        "detailed_type": "product"
    }
    assert versions.product_type_values(17, service=True, storable=False) == {
        "detailed_type": "service"
    }
    assert versions.product_type_values(18, service=False, storable=True) == {
        "type": "consu",
        "is_storable": True,
    }
    assert versions.product_type_values(20, service=False, storable=False) == {"type": "consu"}


def test_major_version() -> None:
    assert versions.major([19, 0, 0, "final", 0, ""]) == 19
    assert versions.major(["saas~19", 1, 0, "final", 0, ""]) == 19
    with pytest.raises(ExtensionError):
        versions.major([15, 0])
    with pytest.raises(ExtensionError):
        versions.name("sale_line_uom", 21)


def _payload(config, secrets=None):
    return {
        "action": {
            "target": {
                "connection": {
                    "connection_id": "c",
                    "connection_type_id": "odoo",
                    "config": config,
                    "secrets": secrets or {"api_key": "abcdefgh12345678"},
                }
            }
        }
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://erp.example.com",
        "https://10.0.0.5",
        "https://localhost",
        "https://erp.local",
        "https://user:pw@erp.example.com",
        "https://erp.example.com/odoo",
        "https://erp.example.com/?db=x",
        "",
    ],
)
def test_unsafe_addresses_are_refused(url) -> None:
    with pytest.raises(ExtensionError):
        connection_from_payload(_payload({"base_url": url}), connection_ref="c")


def test_connection_rules() -> None:
    conn = connection_from_payload(
        _payload({"base_url": "https://MyCo.odoo.com/"}), connection_ref="c"
    )
    assert conn.base_url == "https://myco.odoo.com" and conn.api == "json2"
    with pytest.raises(ExtensionError):
        connection_from_payload(
            _payload({"base_url": "https://myco.odoo.com", "api": "xmlrpc"}), connection_ref="c"
        )
    conn = connection_from_payload(
        _payload(
            {
                "base_url": "https://myco.odoo.com",
                "api": "xmlrpc",
                "database": "myco",
                "login": "bot",
                "odoo_version": "17",
            }
        ),
        connection_ref="c",
    )
    assert conn.odoo_version == 17
    assert "abcdefgh12345678" not in repr(conn)


def test_domains_are_checked() -> None:
    known: dict[str, dict[str, str]] = {"name": {}, "partner_id": {}}
    assert domain.domain(["|", ["name", "ilike", "x"], ["partner_id.ref", "=", "C1"]], known)
    for bad in (
        [["name", "~", "x"]],
        [["unknown", "=", 1]],
        [["name; drop", "=", 1]],
        [["name", "=", {"x": 1}]],
        ["^"],
        [["name", "=", "a\nb"]],
    ):
        with pytest.raises(ExtensionError):
            domain.domain(bad, known)
    with pytest.raises(ExtensionError):
        domain.order("name desc; drop", known)
    assert domain.order("name desc, id", known) == "name desc, id"
