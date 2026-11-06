"""Repro tests from the second 0.1.0 audit (audit-artifacts/odoo-review), made to pass.

Two of the original expectations changed with the fixes, for the better:

* a bad currency is now refused before any write, so no partner is created at
  all (the audit expected the created partner to be reported);
* a failed read after a completed payment no longer fails the step: the payment
  is reported as created, with ``succeeded`` (the audit accepted either form).
"""

from __future__ import annotations

from typing import Any

from test_operations import paths, run
from test_transport import Opener

from runtime import operations

DENIED = {"name": "odoo.exceptions.AccessError", "message": "Denied"}
INVOICE = {
    "id": 1,
    "state": "posted",
    "payment_state": "not_paid",
    "amount_residual": 100,
    "partner_id": [5, "Partner"],
    "move_type": "out_invoice",
    "currency_id": [1, "EUR"],
}


def test_bad_currency_is_refused_before_a_partner_is_created() -> None:
    opener = Opener()
    response = run(
        opener,
        "create_customer_invoice",
        {
            "partner_ref": "NEW",
            "partner_name": "New partner",
            "currency_code": "!@#",
            "lines": [{"description": "Work", "price_unit": 10}],
        },
    )
    assert response["ok"] is False and response["error_code"] == "invalid_payload"
    assert opener.requests == []
    assert response["definitely_no_external_effect"] is True


def test_partner_created_then_failure_preserves_the_effect() -> None:
    # The partner is created; the invoice create is then refused: both facts reported.
    refused = {"name": "odoo.exceptions.ValidationError", "message": "No journal"}
    opener = Opener((200, {}), (200, []), (200, [5]), (422, refused))
    response = run(
        opener,
        "create_customer_invoice",
        {
            "partner_ref": "NEW",
            "partner_name": "New partner",
            "lines": [{"description": "Work", "price_unit": 10}],
        },
    )
    assert "/json/2/res.partner/create" in paths(opener)
    assert response["ok"] is False
    assert response["definitely_no_external_effect"] is False
    assert response["external_effect_status"] == "timeout_unknown"
    assert "partner" in response["error"] and "(id 5)" in response["error"]


def test_completed_payment_then_read_denied_keeps_the_payment() -> None:
    opener = Opener((200, {}), (200, [INVOICE]), (200, [7]), (200, None), (403, DENIED))
    response = run(opener, "register_payment", {"invoice_id": 1})
    assert "/json/2/account.payment.register/action_create_payments" in paths(opener)
    assert response["ok"] is True
    assert response["result"]["created"] is True
    assert response["definitely_no_external_effect"] is False
    assert response["external_effect_status"] == "succeeded"


def test_created_attachment_then_main_write_denied_must_preserve_effect(monkeypatch: Any) -> None:
    monkeypatch.setattr(operations, "read_input_file", lambda *_: (b"file", "invoice.pdf"))
    opener = Opener(
        (200, {"message_main_attachment_id": {"type": "many2one"}}), (200, [9]), (403, DENIED)
    )
    response = run(
        opener,
        "attach_file",
        {
            "model": "account.move",
            "record_id": 1,
            "document_file": "artifact",
            "set_main": True,
        },
    )
    assert "/json/2/ir.attachment/create" in paths(opener)
    assert response["ok"] is False
    assert response.get("definitely_no_external_effect") is False, response
    assert response.get("external_effect_status") == "timeout_unknown", response


def _pay_with_memo(found: list[dict[str, Any]], *more: Any) -> tuple[Opener, dict[str, Any]]:
    opener = Opener((200, {}), (200, [INVOICE]), (200, found), *more)
    response = run(
        opener,
        "register_payment",
        {"invoice_id": 1, "memo": "Monthly payment", "payment_date": "2026-11-06"},
    )
    return opener, response


def test_unrelated_payment_must_not_suppress_payment_for_this_invoice() -> None:
    other = {
        "id": 55,
        "amount": 100,
        "currency_id": [1, "EUR"],
        "payment_type": "inbound",
        "reconciled_invoice_ids": [2],
        "reconciled_bill_ids": [],
        "invoice_ids": [2],
    }
    after = [{"payment_state": "paid", "amount_residual": 0}]
    opener, response = _pay_with_memo([other], (200, [7]), (200, None), (200, after))
    assert response["ok"] is True and response["result"]["created"] is True, response
    assert "/json/2/account.payment.register/action_create_payments" in paths(opener)


def test_payment_linked_to_this_invoice_is_a_rerun() -> None:
    same = {
        "id": 56,
        "amount": 100,
        "currency_id": [1, "EUR"],
        "payment_type": "inbound",
        "reconciled_invoice_ids": [1],
        "reconciled_bill_ids": [],
        "invoice_ids": [1],
    }
    opener, response = _pay_with_memo([same])
    assert response["ok"] is True and response["result"]["created"] is False
    assert all("payment.register" not in p for p in paths(opener)[1:])


def test_linked_payment_in_the_other_direction_is_not_a_rerun() -> None:
    refund = {
        "id": 57,
        "amount": 100,
        "currency_id": [1, "EUR"],
        "payment_type": "outbound",
        "reconciled_invoice_ids": [1],
        "reconciled_bill_ids": [],
        "invoice_ids": [1],
    }
    after = [{"payment_state": "paid", "amount_residual": 0}]
    _opener, response = _pay_with_memo([refund], (200, [7]), (200, None), (200, after))
    assert response["result"]["created"] is True
