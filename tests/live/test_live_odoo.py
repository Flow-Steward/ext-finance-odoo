"""End-to-end tests against a real Odoo. Skipped unless ODOO_LIVE_URL is set.

Start a disposable Odoo with ``tests/live/odoo_matrix.sh up <16..20>`` and run, on
its network and with ``FS_ALLOW_PRIVATE_REMOTE_URLS=1``:

    ODOO_LIVE_URL=http://fs-odoo-live-18:8069 ODOO_LIVE_DB=live18 ODOO_LIVE_LOGIN=admin \\
    ODOO_LIVE_KEY=<key> ODOO_LIVE_API=xmlrpc ODOO_LIVE_VERSION=18 pytest tests/live

Every step goes through the real runtime (handle_runtime); only the connection's
https check is bypassed, because the disposable server speaks plain http. That
patch is applied per module and undone afterwards.

Each test can run alone: the records several tests use (a partner, a product, a
posted invoice) come from module fixtures, not from earlier tests. Every run
uses its own references (RUN), so reruns on one database do not collide.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
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


def _live_connection(_payload: Any, connection_ref: str) -> Connection:
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


@pytest.fixture(scope="module", autouse=True)
def live_connection() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(operations, "connection_from_payload", _live_connection)
        yield


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


# ---------------------------------------------------------------------------
# Shared records, made once per module and independent of test order


@pytest.fixture(scope="module")
def partner() -> dict[str, Any]:
    inputs = {
        "ref": f"FSP-{RUN}",
        "name": f"FS Partner {RUN}",
        "vat": "",
        "email": f"fs{RUN}@example.com",
        "country_code": "LT",
    }
    first = step("find_or_create_partner", inputs)
    return {"inputs": inputs, "first": first, "ref": inputs["ref"], "id": first["partner_id"]}


@pytest.fixture(scope="module")
def product() -> dict[str, Any]:
    inputs = {
        "product_code": f"FSPR-{RUN}",
        "name": f"FS Product {RUN}",
        "kind": "goods",
        "storable": True,
        "list_price": "12.50",
    }
    first = step("find_or_create_product", inputs)
    return {"inputs": inputs, "first": first, "code": inputs["product_code"]}


def _invoice_inputs(partner: dict[str, Any], product: dict[str, Any], tag: str) -> dict[str, Any]:
    return {
        "partner_ref": partner["ref"],
        "reference": f"FSINV-{tag}-{RUN}",
        "lines": [{"product_code": product["code"], "quantity": "2", "price_unit": "10.00"}],
        "post": True,
        "document_file": "live-file-1",
    }


@pytest.fixture(scope="module")
def invoice(partner: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    inputs = _invoice_inputs(partner, product, "A")
    return {"inputs": inputs, "first": step("create_customer_invoice", inputs)}


# ---------------------------------------------------------------------------
# Tests


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
    # null clears a field on both APIs (XML-RPC sends it as false).
    step("update_records", {"model": "res.partner", "ids": [record_id], "values": {"phone": None}})
    cleared = step(
        "read_records", {"model": "res.partner", "ids": [record_id], "fields": ["phone"]}
    )
    assert cleared["record"]["phone"] in (False, None, "")
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
    by_month = step(
        "group_records",
        {"model": "res.partner", "groupby": ["create_date:month"], "aggregates": ["__count"]},
    )
    assert by_month["count"] >= 1
    fields = step("describe_fields", {"model": "res.partner"})
    assert "name" in fields["fields"]
    step("call_method", {"model": "res.partner", "method": "action_archive", "ids": [record_id]})
    deleted = step("delete_records", {"model": "res.partner", "ids": [record_id]})
    assert deleted["deleted"] is True


def test_03_partner(partner: dict[str, Any]) -> None:
    assert partner["first"]["created"] is True
    again = step("find_or_create_partner", partner["inputs"])
    assert again["created"] is False and again["partner_id"] == partner["id"]


def test_04_product(product: dict[str, Any]) -> None:
    assert product["first"]["created"] is True
    again = step("find_or_create_product", product["inputs"])
    assert again["created"] is False
    service = step(
        "find_or_create_product",
        {"product_code": f"FSSV-{RUN}", "name": "FS Service", "kind": "service"},
    )
    assert service["created"] is True


def test_05_customer_invoice_attach_post_and_rerun(invoice: dict[str, Any]) -> None:
    first = invoice["first"]
    assert first["created"] is True and first["posted"] is True and first["state"] == "posted"
    assert first["attachment_id"] and first["total_untaxed"] == "20.00"
    again = step("create_customer_invoice", invoice["inputs"])
    assert again["created"] is False and again["invoice_id"] == first["invoice_id"]


def test_06_register_payment_and_rerun(
    invoice: dict[str, Any], partner: dict[str, Any], product: dict[str, Any]
) -> None:
    memo = f"FSPAY-{RUN}"
    invoice_id = invoice["first"]["invoice_id"]
    first = step("register_payment", {"invoice_id": invoice_id, "memo": memo, "amount": "5.00"})
    assert first["created"] is True and first["payment_state"] == "partial"
    again = step("register_payment", {"invoice_id": invoice_id, "memo": memo, "amount": "5.00"})
    assert again["created"] is False
    # Same partner, memo, date and amount, other invoice: not a rerun, it is paid.
    other = step("create_customer_invoice", _invoice_inputs(partner, product, "B"))
    paid = step(
        "register_payment", {"invoice_id": other["invoice_id"], "memo": memo, "amount": "5.00"}
    )
    assert paid["created"] is True
    rest = step("register_payment", {"invoice_id": invoice_id, "memo": f"{memo}-rest"})
    assert rest["created"] is True and rest["payment_state"] in {"paid", "in_payment"}


def test_07_vendor_bill(partner: dict[str, Any]) -> None:
    bill = step(
        "create_vendor_bill",
        {
            "partner_ref": partner["ref"],
            "reference": f"FSBILL-{RUN}",
            "lines": [{"description": "Service", "quantity": 1, "price_unit": "5.00"}],
        },
    )
    assert bill["created"] is True and bill["state"] == "draft"


def test_08_sale_order(partner: dict[str, Any], product: dict[str, Any]) -> None:
    inputs = {
        "partner_ref": partner["ref"],
        "reference": f"FSSO-{RUN}",
        "lines": [{"product_code": product["code"], "quantity": 3}],
    }
    order = step("create_sale_order", {**inputs, "confirm": True})
    assert order["created"] is True and order["confirmed"] is True and order["state"] == "sale"
    again = step("create_sale_order", inputs)
    assert again["created"] is False


def test_09_reads(partner: dict[str, Any], product: dict[str, Any]) -> None:
    stock = step("stock_levels", {"limit": 20})
    assert "items" in stock
    prices = step("product_prices", {"product_code": product["code"]})
    assert prices["sales_price"] == "12.5000"
    balance = step("partner_balance", {"partner_ref": partner["ref"]})
    assert balance["partner_id"] == partner["id"]
    assert "overdue_receivable" in balance and "overdue_payable" in balance


def test_10_files_and_export(partner: dict[str, Any]) -> None:
    attached = step(
        "attach_file",
        {
            "model": "res.partner",
            "record_id": partner["id"],
            "document_file": "live-file-2",
            "file_name": "contract.pdf",
        },
    )
    assert attached["attachment_id"]
    downloaded = step(
        "download_attachment",
        {"model": "res.partner", "record_id": partner["id"], "file_name": "contract.pdf"},
    )
    assert downloaded["found"] is True and downloaded["size_bytes"] == len(PDF)
    assert FILES.saved[-1]["body"] == PDF
    exported = step(
        "export_records",
        {"model": "res.partner", "fields": ["name"], "domain": [["id", "=", partner["id"]]]},
    )
    assert exported["record_count"] == 1 and exported["complete"] is True


def test_11_changes_since_skips_nothing() -> None:
    # Page through every partner two at a time: module data shares write_dates to
    # the microsecond, so pages end inside a second; none may be skipped.
    seen: set[int] = set()
    cursor: dict[str, Any] = {"since": "2000-01-01"}
    for _ in range(500):
        page = step("changes_since", {"entity": "partners", "limit": 2, **cursor})
        seen.update(r["id"] for r in page["records"])
        if not page["has_more"]:
            break
        cursor = {"since": page["next_since"]}
        if page["next_after_id"]:
            cursor["after_id"] = page["next_after_id"]
    else:
        raise AssertionError("changes_since did not finish")
    every = step(
        "search_records", {"model": "res.partner", "domain": [], "fields": ["id"], "limit": 200}
    )
    assert {r["id"] for r in every["records"]} <= seen
