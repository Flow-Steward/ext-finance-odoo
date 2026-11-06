"""End-to-end tests against a real Odoo. Skipped unless ODOO_LIVE_URL is set.

Start a disposable Odoo with ``tests/live/odoo_matrix.sh up <16..20>`` and run, on
its network and with ``FS_ALLOW_PRIVATE_REMOTE_URLS=1``:

    ODOO_LIVE_URL=http://fs-odoo-live-18:8069 ODOO_LIVE_DB=live18 ODOO_LIVE_LOGIN=admin \\
    ODOO_LIVE_KEY=<key> ODOO_LIVE_API=xmlrpc ODOO_LIVE_VERSION=18 pytest tests/live

Every step goes through the real runtime (handle_runtime); only the connection's
https check is bypassed, because the disposable server speaks plain http.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from runtime import operations
from runtime.connection import Connection
from runtime.operations import handle_runtime
from runtime.transport import OdooTransport

URL = os.environ.get("ODOO_LIVE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="live Odoo tests need ODOO_LIVE_URL")

RUN = str(int(time.time()))
PDF = b"%PDF-1.4\n% Flow Steward live test\n%%EOF\n"
STATE: dict[str, Any] = {}


def _connection() -> Connection:
    return Connection(
        connection_id="live",
        base_url=URL,
        database=os.environ.get("ODOO_LIVE_DB", ""),
        login=os.environ.get("ODOO_LIVE_LOGIN", "admin"),
        api=os.environ.get("ODOO_LIVE_API", "json2"),
        odoo_version=None,
        language="",
        _api_key=os.environ.get("ODOO_LIVE_KEY", ""),
    )


def _live_connection(_payload: Any, connection_ref: str) -> Connection:
    return _connection()


class Files:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def write(self, payload, chunks, *, binding_key, content_type, timeout_seconds):
        self.saved.append(
            {"binding_key": binding_key, "content_type": content_type, "body": b"".join(chunks)}
        )
        return {"artifact_handle": f"live-artifact-{len(self.saved)}"}

    def read(self, payload, *, artifact_id, timeout_seconds=30.0):
        return PDF


FILES = Files()


def step(operation_id: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "live",
            "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
            "test_mode": False,
        },
        "action": {
            "action_id": operation_id,
            "input": {"connection_ref": "live", **(inputs or {})},
            "target": {
                "connection": {
                    "connection_id": "live",
                    "connection_type_id": "odoo",
                    "config": {"base_url": "https://placeholder.example.com", "api": "json2"},
                    "secrets": {"api_key": "placeholder-key"},
                }
            },
        },
    }
    # The real connection, with the https check skipped for the plain-http test server.
    operations.connection_from_payload = _live_connection  # type: ignore[assignment]
    response = handle_runtime(
        payload,
        transport_factory=OdooTransport,
        artifact_reader=FILES.read,
        artifact_writer=FILES.write,
    )
    assert response["ok"] is True, (
        f"{operation_id}: {response.get('error_code')} {response.get('error')}"
    )
    return response["result"]


def test_01_connection() -> None:
    result = step("test_connection")
    assert result["connected"] is True
    if os.environ.get("ODOO_LIVE_VERSION"):
        assert result["odoo_version"] == int(os.environ["ODOO_LIVE_VERSION"])


def test_02_generic_round_trip() -> None:
    created = step(
        "create_record",
        {"model": "res.partner", "values": {"name": f"FS Live {RUN}", "ref": f"FSL-{RUN}"}},
    )
    record_id = created["id"]
    read = step(
        "read_records", {"model": "res.partner", "ids": [record_id], "fields": ["name", "ref"]}
    )
    assert read["record"]["ref"] == f"FSL-{RUN}"
    step(
        "update_records",
        {"model": "res.partner", "ids": [record_id], "values": {"phone": "+37060000000"}},
    )
    found = step(
        "search_records",
        {"model": "res.partner", "domain": [["ref", "=", f"FSL-{RUN}"]], "fields": ["phone"]},
    )
    assert found["count"] == 1 and found["record"]["phone"] == "+37060000000"
    count = step("count_records", {"model": "res.partner", "domain": [["ref", "=", f"FSL-{RUN}"]]})
    assert count["count"] == 1
    groups = step(
        "group_records",
        {
            "model": "res.partner",
            "domain": [["ref", "=", f"FSL-{RUN}"]],
            "groupby": ["is_company"],
            "aggregates": ["__count"],
        },
    )
    assert groups["count"] >= 1
    fields = step("describe_fields", {"model": "res.partner"})
    assert "name" in fields["fields"]
    step("call_method", {"model": "res.partner", "method": "action_archive", "ids": [record_id]})
    deleted = step("delete_records", {"model": "res.partner", "ids": [record_id]})
    assert deleted["deleted"] is True


def test_03_partner() -> None:
    inputs = {
        "ref": f"FSP-{RUN}",
        "name": f"FS Partner {RUN}",
        "vat": "",
        "email": f"fs{RUN}@example.com",
        "country_code": "LT",
    }
    first = step("find_or_create_partner", inputs)
    assert first["created"] is True
    again = step("find_or_create_partner", inputs)
    assert again["created"] is False and again["partner_id"] == first["partner_id"]
    STATE["partner_ref"] = inputs["ref"]
    STATE["partner_id"] = first["partner_id"]


def test_04_product() -> None:
    inputs = {
        "product_code": f"FSPR-{RUN}",
        "name": f"FS Product {RUN}",
        "kind": "goods",
        "storable": True,
        "list_price": "12.50",
    }
    first = step("find_or_create_product", inputs)
    assert first["created"] is True
    again = step("find_or_create_product", inputs)
    assert again["created"] is False
    service = step(
        "find_or_create_product",
        {"product_code": f"FSSV-{RUN}", "name": "FS Service", "kind": "service"},
    )
    assert service["created"] is True
    STATE["product_code"] = inputs["product_code"]


def test_05_customer_invoice_attach_post_and_rerun() -> None:
    inputs = {
        "partner_ref": STATE["partner_ref"],
        "reference": f"FSINV-{RUN}",
        "lines": [{"product_code": STATE["product_code"], "quantity": "2", "price_unit": "10.00"}],
        "post": True,
        "document_file": "live-file-1",
    }
    first = step("create_customer_invoice", inputs)
    assert first["created"] is True and first["posted"] is True and first["state"] == "posted"
    assert first["attachment_id"] and first["total_untaxed"] == "20.00"
    again = step("create_customer_invoice", inputs)
    assert again["created"] is False and again["invoice_id"] == first["invoice_id"]
    STATE["invoice_id"] = first["invoice_id"]


def test_06_register_payment_and_rerun() -> None:
    first = step("register_payment", {"invoice_id": STATE["invoice_id"], "memo": f"FSPAY-{RUN}"})
    assert first["created"] is True
    assert first["payment_state"] in {"paid", "in_payment"}
    again = step("register_payment", {"invoice_id": STATE["invoice_id"], "memo": f"FSPAY-{RUN}"})
    assert again["created"] is False


def test_07_vendor_bill() -> None:
    bill = step(
        "create_vendor_bill",
        {
            "partner_ref": STATE["partner_ref"],
            "reference": f"FSBILL-{RUN}",
            "lines": [{"description": "Service", "quantity": 1, "price_unit": "5.00"}],
        },
    )
    assert bill["created"] is True and bill["state"] == "draft"


def test_08_sale_order() -> None:
    order = step(
        "create_sale_order",
        {
            "partner_ref": STATE["partner_ref"],
            "reference": f"FSSO-{RUN}",
            "lines": [{"product_code": STATE["product_code"], "quantity": 3}],
            "confirm": True,
        },
    )
    assert order["created"] is True and order["confirmed"] is True and order["state"] == "sale"
    again = step(
        "create_sale_order",
        {
            "partner_ref": STATE["partner_ref"],
            "reference": f"FSSO-{RUN}",
            "lines": [{"product_code": STATE["product_code"], "quantity": 3}],
        },
    )
    assert again["created"] is False


def test_09_reads() -> None:
    stock = step("stock_levels", {"limit": 20})
    assert "items" in stock
    prices = step("product_prices", {"product_code": STATE["product_code"]})
    assert prices["sales_price"] == "12.5000"
    changes = step("changes_since", {"entity": "partners", "since": "2000-01-01", "limit": 5})
    assert changes["count"] >= 1
    balance = step("partner_balance", {"partner_ref": STATE["partner_ref"]})
    assert balance["partner_id"] == STATE["partner_id"]


def test_10_files_and_export() -> None:
    attached = step(
        "attach_file",
        {
            "model": "res.partner",
            "record_id": STATE["partner_id"],
            "document_file": "live-file-2",
            "file_name": "contract.pdf",
        },
    )
    assert attached["attachment_id"]
    downloaded = step(
        "download_attachment",
        {"model": "res.partner", "record_id": STATE["partner_id"], "file_name": "contract.pdf"},
    )
    assert downloaded["found"] is True and downloaded["size_bytes"] == len(PDF)
    assert FILES.saved[-1]["body"] == PDF
    exported = step(
        "export_records",
        {"model": "res.partner", "fields": ["name"], "domain": [["id", "=", STATE["partner_id"]]]},
    )
    assert exported["record_count"] == 1 and exported["complete"] is True
