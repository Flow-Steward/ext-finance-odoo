"""Operation registry: the generic model operations and the everyday ones."""

from __future__ import annotations

import re
from typing import Any

TEST_CONNECTION = "test_connection"
EXPORT_RECORDS = "export_records"

MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,6}$")
#: Public method names only: Odoo itself refuses names starting with "_" over RPC.
METHOD_RE = re.compile(r"^[a-z][a-z0-9_]{0,80}$")

#: Inputs each generic operation accepts (besides connection_ref).
GENERIC_INPUTS: dict[str, set[str]] = {
    "search_records": {
        "model",
        "domain",
        "fields",
        "order",
        "limit",
        "offset",
        "fetch_all_pages",
        "max_pages",
    },
    "read_records": {"model", "ids", "fields"},
    "count_records": {"model", "domain"},
    "group_records": {"model", "domain", "groupby", "aggregates", "limit"},
    "describe_fields": {"model"},
    "create_record": {"model", "values"},
    "update_records": {"model", "ids", "values"},
    "delete_records": {"model", "ids"},
    "call_method": {"model", "method", "ids", "kwargs"},
    EXPORT_RECORDS: {"model", "domain", "fields", "order", "start_offset", "max_pages"},
}
GENERIC_WRITE_IDS: frozenset[str] = frozenset(
    {"create_record", "update_records", "delete_records", "call_method"}
)

FRIENDLY_WRITE_IDS: frozenset[str] = frozenset(
    {
        "find_or_create_partner",
        "find_or_create_product",
        "create_customer_invoice",
        "create_vendor_bill",
        "register_payment",
        "create_sale_order",
        "attach_file",
    }
)
FRIENDLY_READ_IDS: frozenset[str] = frozenset(
    {"stock_levels", "product_prices", "changes_since", "partner_balance", "download_attachment"}
)
FRIENDLY_IDS: frozenset[str] = FRIENDLY_WRITE_IDS | FRIENDLY_READ_IDS

OPERATION_IDS: frozenset[str] = (
    frozenset({TEST_CONNECTION}) | frozenset(GENERIC_INPUTS) | FRIENDLY_IDS
)
WRITE_OPERATION_IDS: frozenset[str] = GENERIC_WRITE_IDS | FRIENDLY_WRITE_IDS

#: Every declared output of each generic write, as a test run returns it.
NEUTRAL_OUTPUTS: dict[str, dict[str, Any]] = {
    "create_record": {"model": "", "id": None},
    "update_records": {"model": "", "ids": [], "updated": False},
    "delete_records": {"model": "", "ids": [], "deleted": False},
    "call_method": {"model": "", "method": "", "result": None},
}

DOWNLOAD_BINDING = "odoo_attachment"
UPLOAD_BINDING = "odoo_upload"
EXPORT_BINDING = "odoo_export"

__all__ = [
    "DOWNLOAD_BINDING",
    "EXPORT_BINDING",
    "EXPORT_RECORDS",
    "FRIENDLY_IDS",
    "FRIENDLY_READ_IDS",
    "FRIENDLY_WRITE_IDS",
    "GENERIC_INPUTS",
    "GENERIC_WRITE_IDS",
    "METHOD_RE",
    "MODEL_RE",
    "NEUTRAL_OUTPUTS",
    "OPERATION_IDS",
    "TEST_CONNECTION",
    "UPLOAD_BINDING",
    "WRITE_OPERATION_IDS",
]
