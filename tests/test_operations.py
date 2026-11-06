"""Operations through handle_runtime, against a fake Odoo (JSON-2)."""

from __future__ import annotations

import json
from typing import Any

from test_transport import KEY, Opener

from runtime.operations import handle_runtime
from runtime.transport import OdooTransport

PARTNER_FIELDS = {
    "name": {"type": "char"},
    "ref": {"type": "char"},
    "vat": {"type": "char"},
    "email": {"type": "char"},
}


def payload(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    test_mode: bool = False,
    version: str = "19",
) -> dict[str, Any]:
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
            "test_mode": test_mode,
        },
        "action": {
            "action_id": operation_id,
            "input": {"connection_ref": "c1", **(inputs or {})},
            "target": {
                "connection": {
                    "connection_id": "c1",
                    "connection_type_id": "odoo",
                    "config": {
                        "base_url": "https://erp.example.com",
                        "api": "json2",
                        "odoo_version": version,
                    },
                    "secrets": {"api_key": KEY},
                }
            },
        },
    }


def run(
    opener: Opener, operation_id: str, inputs: dict[str, Any] | None = None, **kw: Any
) -> dict[str, Any]:
    response = handle_runtime(
        payload(operation_id, inputs, **kw),
        transport_factory=lambda c: OdooTransport(c, opener=opener, sleeper=lambda _s: None),
    )
    assert KEY not in json.dumps(response)
    return response


def paths(opener: Opener) -> list[str]:
    return [r.full_url.removeprefix("https://erp.example.com") for r in opener.requests]


def test_search_validates_fields_before_reading() -> None:
    opener = Opener((200, PARTNER_FIELDS), (200, [{"id": 1, "name": "A"}]))
    response = run(
        opener,
        "search_records",
        {"model": "res.partner", "domain": [["ref", "=", "C1"]], "fields": ["name"]},
    )
    assert response["ok"] is True and response["result"]["record"]["name"] == "A"
    assert paths(opener) == ["/json/2/res.partner/fields_get", "/json/2/res.partner/search_read"]
    opener = Opener((200, PARTNER_FIELDS))
    response = run(
        opener, "search_records", {"model": "res.partner", "domain": [["password", "=", "x"]]}
    )
    assert response["error_code"] == "invalid_payload"
    assert len(opener.requests) == 1


def test_writes_are_effects_and_suppressed_in_test_mode() -> None:
    opener = Opener()
    response = run(
        opener, "create_record", {"model": "res.partner", "values": {"name": "X"}}, test_mode=True
    )
    assert response["ok"] is True and response["result"]["definitely_no_external_effect"] is True
    assert opener.requests == []
    opener = Opener((200, PARTNER_FIELDS), (200, [42]))
    response = run(opener, "create_record", {"model": "res.partner", "values": {"name": "X"}})
    assert response["result"]["id"] == 42
    assert response["external_effect_status"] == "succeeded"


def test_private_methods_are_refused() -> None:
    response = run(Opener(), "call_method", {"model": "res.partner", "method": "_compute_x"})
    assert response["error_code"] == "invalid_payload"


def test_find_or_create_partner_finds_without_writing() -> None:
    opener = Opener((200, [{"id": 5, "name": "A", "ref": "C1", "vat": False, "email": False}]))
    response = run(opener, "find_or_create_partner", {"ref": "C1"})
    assert response["result"]["created"] is False and response["result"]["partner_id"] == 5
    assert paths(opener) == ["/json/2/res.partner/search_read"]


def test_invoice_rerun_with_another_date_stops_without_writing() -> None:
    move_fields = {"ref": {"type": "char"}}
    opener = Opener(
        (200, move_fields),
        (200, [{"id": 5, "name": "A", "ref": "C1"}]),
        (200, [{"id": 9, "invoice_date": "2025-01-01", "state": "posted"}]),
    )
    response = run(
        opener,
        "create_customer_invoice",
        {
            "partner_ref": "C1",
            "reference": "INV-1",
            "invoice_date": "2026-01-01",
            "lines": [{"description": "x", "price_unit": 1}],
        },
    )
    assert response["error_code"] == "invalid_payload"
    assert "already exists" in response["error"]
    assert all(not p.endswith("/create") for p in paths(opener))


def test_missing_app_is_module_not_installed() -> None:
    body = {
        "name": "werkzeug.exceptions.NotFound",
        "message": "the model 'sale.order' does not exist",
    }
    opener = Opener((404, body))
    response = run(opener, "create_sale_order", {"partner_id": 5, "lines": [{"product_id": 1}]})
    assert response["error_code"] == "module_not_installed"
    assert response["definitely_no_external_effect"] is True


def test_comma_decimals_are_refused() -> None:
    response = run(
        Opener(), "register_payment", {"invoice_id": 1, "amount": "1,500"}, test_mode=True
    )
    assert response["error_code"] == "invalid_payload"
