"""Field and method names that differ between Odoo 16, 17, 18, 19 and 20.

One table, checked against the odoo/odoo sources of each branch (16.0 ... 20.0);
``tests/test_versions.py`` pins it. Everything not listed here is the same in all
five versions (account.move.line: tax_ids, quantity, price_unit, discount,
product_uom_id, display_type; account.payment.register.action_create_payments;
account.move.action_post; sale.order.action_confirm; mail.thread.main.attachment's
message_main_attachment_id; res.partner credit/debit).
"""

from __future__ import annotations

from typing import Any

from . import errors
from .errors import ExtensionError

SUPPORTED: tuple[int, ...] = (16, 17, 18, 19, 20)

#: name -> {major version: value}
_TABLE: dict[str, dict[int, str]] = {
    # sale.order.line unit of measure: product_uom until 18, product_uom_id from 19.
    "sale_line_uom": {
        16: "product_uom",
        17: "product_uom",
        18: "product_uom",
        19: "product_uom_id",
        20: "product_uom_id",
    },
    # stock.quant unit: product_uom_id until 19, uom_id in 20.
    "quant_uom": {
        16: "product_uom_id",
        17: "product_uom_id",
        18: "product_uom_id",
        19: "product_uom_id",
        20: "uom_id",
    },
    # ir.attachment content (base64 over RPC): datas until 19; 20 has only raw.
    "attachment_content": {16: "datas", 17: "datas", 18: "datas", 19: "datas", 20: "raw"},
    # account.payment memo: the move's ref until 17, memo from 18.
    "payment_memo": {16: "ref", 17: "ref", 18: "memo", 19: "memo", 20: "memo"},
    # product.pricelist.item percentage: percent_price until 19, removed in 20 (empty: none).
    "pricelist_percent": {
        16: "percent_price",
        17: "percent_price",
        18: "percent_price",
        19: "percent_price",
        20: "",
    },
    # read_group: the (domain, fields, groupby, lazy) form until 19 (deprecated there);
    # 20 takes (domain, groupby, aggregates) and returns rows as lists.
    "read_group_style": {16: "fields", 17: "fields", 18: "fields", 19: "fields", 20: "aggregates"},
}


def name(key: str, version: int) -> str:
    try:
        return _TABLE[key][version]
    except KeyError as exc:
        raise ExtensionError(
            errors.UNSUPPORTED_VERSION,
            f"Odoo {version} is not supported; this extension supports Odoo 16 to 20",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc


def major(version_info: Any) -> int:
    """The major version from Odoo's ``server_version_info`` ([19, 0, 0, 'final', 0, ''])."""
    first = version_info[0] if isinstance(version_info, (list, tuple)) and version_info else None
    if isinstance(first, str) and first.startswith("saas~"):
        first = first.removeprefix("saas~").split(".", 1)[0]
    try:
        number = int(first)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ExtensionError(
            errors.INVALID_RESPONSE,
            "Odoo reported a version this extension cannot read",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc
    if number not in SUPPORTED:
        raise ExtensionError(
            errors.UNSUPPORTED_VERSION,
            f"Odoo {number} is not supported; this extension supports Odoo 16 to 20",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    return number


def product_type_values(version: int, *, service: bool, storable: bool) -> dict[str, Any]:
    """Fields that make a product a service, plain goods or stocked goods.

    16-17: detailed_type 'service' | 'consu' | 'product' (stocked, with the stock app);
    18-20: type 'service' | 'consu' plus is_storable for stocked goods.
    """
    name("sale_line_uom", version)  # validates the version
    if version <= 17:
        if service:
            return {"detailed_type": "service"}
        return {"detailed_type": "product" if storable else "consu"}
    if service:
        return {"type": "service"}
    values: dict[str, Any] = {"type": "consu"}
    if storable:
        values["is_storable"] = True
    return values


__all__ = ["SUPPORTED", "major", "name", "product_type_values"]
