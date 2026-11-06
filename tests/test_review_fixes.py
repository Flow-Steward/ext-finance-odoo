"""Regressions from the 0.1.0 code review: each test pins one fixed defect."""

from __future__ import annotations

import json
from typing import Any

import pytest
from defusedxml.xmlrpc import xmlrpc_client
from test_operations import paths, run
from test_transport import Opener, transport, xml_answer

from runtime import errors
from runtime.errors import ExtensionError
from runtime.friendly import Progress, attach

HTML_502 = (502, b"<html>502 Bad Gateway</html>")


def bodies(opener: Opener) -> list[Any]:
    return [json.loads(r.data) if r.data else None for r in opener.requests]


# 1. register_payment: the payment exists once action_create_payments answered,
#    so a failing final read must not turn the step into a failure.


def test_payment_survives_a_failing_final_read() -> None:
    invoice = {
        "id": 7,
        "state": "posted",
        "payment_state": "not_paid",
        "amount_residual": 100.0,
        "partner_id": [5, "A"],
        "move_type": "out_invoice",
    }
    opener = Opener((200, {}), (200, [invoice]), (200, [11]), (200, True), HTML_502)
    response = run(opener, "register_payment", {"invoice_id": 7})
    assert response["ok"] is True, response
    assert response["result"]["created"] is True
    assert response["result"]["payment_state"] is None
    assert response["external_effect_status"] == "succeeded"


def test_payment_wizard_failure_after_creation_is_reported() -> None:
    invoice = {
        "id": 7,
        "state": "posted",
        "payment_state": "not_paid",
        "amount_residual": 100.0,
        "partner_id": [5, "A"],
        "move_type": "out_invoice",
    }
    body = {"name": "builtins.RuntimeError", "message": "EDI down"}
    opener = Opener((200, {}), (200, [invoice]), (200, [11]), (500, body))
    response = run(opener, "register_payment", {"invoice_id": 7})
    assert response["ok"] is False
    assert response["error_code"] == errors.TIMEOUT_UNKNOWN
    assert response["definitely_no_external_effect"] is False


# 2. invoices and orders: an unknown product stops the step before a partner is created.


def test_invoice_unknown_product_creates_no_partner() -> None:
    opener = Opener((200, {"ref": {}}), (200, []))
    response = run(
        opener,
        "create_customer_invoice",
        {
            "partner_ref": "NEW-1",
            "partner_name": "New customer",
            "lines": [{"product_code": "NOPE", "price_unit": 1}],
        },
    )
    assert response["error_code"] == "invalid_payload"
    assert "product NOPE" in response["error"]
    assert response["definitely_no_external_effect"] is True
    assert paths(opener) == [
        "/json/2/account.move/fields_get",
        "/json/2/product.product/search_read",
    ]


def test_order_unknown_unit_creates_no_partner() -> None:
    opener = Opener((200, {}), (200, [{"id": 3}]), (200, []))
    response = run(
        opener,
        "create_sale_order",
        {
            "partner_ref": "NEW-1",
            "partner_name": "New customer",
            "lines": [{"product_code": "P1", "unit": "Crates"}],
        },
    )
    assert response["error_code"] == "invalid_payload"
    assert "unit Crates" in response["error"]
    assert all("res.partner" not in p for p in paths(opener))


# 3. group_records: a date field may be grouped by month, quarter and so on.

DATE_FIELDS = {"create_date": {"type": "datetime"}, "name": {"type": "char"}}


def test_group_by_month() -> None:
    opener = Opener(
        (200, DATE_FIELDS), (200, [{"create_date:month": "January 2026", "__count": 3}])
    )
    response = run(
        opener, "group_records", {"model": "res.partner", "groupby": ["create_date:month"]}
    )
    assert response["ok"] is True, response
    assert bodies(opener)[1]["groupby"] == ["create_date:month"]
    assert response["result"]["groups"] == [{"create_date:month": "January 2026"}]


@pytest.mark.parametrize("groupby", ["name:month", "create_date:decade", "create_date:"])
def test_bad_granularity_is_refused(groupby: str) -> None:
    opener = Opener((200, DATE_FIELDS))
    response = run(opener, "group_records", {"model": "res.partner", "groupby": [groupby]})
    assert response["error_code"] == "invalid_payload"
    assert len(opener.requests) == 1


# 4. XML-RPC has no null: None travels as False; values XML-RPC cannot carry are refused.


def test_xmlrpc_sends_none_as_false() -> None:
    opener = Opener((200, xml_answer(2)), (200, xml_answer(True)))
    transport(opener, "xmlrpc").call(
        "res.partner",
        "write",
        ids=[1],
        kwargs={"vals": {"phone": None, "category_id": [[6, 0, []]]}},
        mutating=True,
    )
    params, _method = xmlrpc_client.loads(opener.requests[1].data)
    assert params[6] == {"vals": {"phone": False, "category_id": [[6, 0, []]]}}


def test_xmlrpc_refuses_a_number_it_cannot_carry() -> None:
    opener = Opener((200, xml_answer(2)))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call(
            "res.partner", "write", ids=[1], kwargs={"vals": {"x": 2**40}}, mutating=True
        )
    assert caught.value.code == errors.INVALID_PAYLOAD
    assert caught.value.definitely_no_external_effect is True
    assert len(opener.requests) == 1


# 6. partner_balance: overdue split into receivable and payable.


def test_balance_splits_overdue() -> None:
    rows = [
        {
            "id": 1,
            "move_type": "out_invoice",
            "invoice_date_due": "2000-01-01",
            "amount_residual_signed": 30.0,
        },
        {
            "id": 2,
            "move_type": "in_invoice",
            "invoice_date_due": "2000-01-01",
            "amount_residual_signed": -20.0,
        },
        {
            "id": 3,
            "move_type": "out_invoice",
            "invoice_date_due": "2999-01-01",
            "amount_residual_signed": 10.0,
        },
    ]
    partner = [{"id": 5, "display_name": "A", "credit": 40.0, "debit": 20.0}]
    opener = Opener((200, {}), (200, partner), (200, rows))
    result = run(opener, "partner_balance", {"partner_id": 5})["result"]
    assert result["open_receivable"] == "40.00" and result["open_payable"] == "-20.00"
    assert result["overdue_receivable"] == "30.00"
    assert result["overdue_payable"] == "-20.00"
    assert "overdue" not in result


# 7. changes_since: >= with a (second, id) cursor that never skips a record,
#    archived records included. Odoo keeps microseconds but reads whole seconds.

PFIELDS: dict[str, dict[str, str]] = {"display_name": {}, "write_date": {}}


def rec(record_id: int, second: str) -> dict[str, Any]:
    return {"id": record_id, "display_name": f"r{record_id}", "write_date": f"2026-01-01 {second}"}


def test_changes_first_page_includes_the_moment_and_archived() -> None:
    opener = Opener((200, PFIELDS), (200, [rec(5, "10:00:00")]))
    result = run(opener, "changes_since", {"entity": "partners", "since": "2026-01-01"})["result"]
    sent = bodies(opener)[1]
    assert sent["domain"] == [["write_date", ">=", "2026-01-01 00:00:00"]]
    assert sent["context"] == {"active_test": False}
    assert sent["order"] == "write_date asc, id asc"
    assert result["has_more"] is False
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:00", None)


def test_changes_page_stops_before_a_split_second() -> None:
    # limit 2, three rows asked: the page would end inside 10:00:05, so it stops before it.
    opener = Opener(
        (200, PFIELDS), (200, [rec(9, "10:00:00"), rec(43, "10:00:05"), rec(7, "10:00:05")])
    )
    result = run(
        opener, "changes_since", {"entity": "partners", "since": "2026-01-01", "limit": 2}
    )["result"]
    assert [r["id"] for r in result["records"]] == [9]
    assert result["has_more"] is True
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:05", None)


def test_changes_one_full_second_is_read_in_id_order() -> None:
    opener = Opener(
        (200, PFIELDS),
        (200, [rec(43, "10:00:05"), rec(7, "10:00:05"), rec(8, "10:00:05")]),
        (200, [rec(7, "10:00:05"), rec(8, "10:00:05")]),
    )
    result = run(
        opener,
        "changes_since",
        {"entity": "partners", "since": "2026-01-01 10:00:05", "limit": 2},
    )["result"]
    assert bodies(opener)[2]["domain"] == [
        ["write_date", ">=", "2026-01-01 10:00:05"],
        ["write_date", "<", "2026-01-01 10:00:06"],
    ]
    assert bodies(opener)[2]["order"] == "id asc"
    assert [r["id"] for r in result["records"]] == [7, 8]
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:05", 8)


def test_changes_resume_inside_a_second_then_continue() -> None:
    opener = Opener(
        (200, PFIELDS),
        (200, [rec(43, "10:00:05")]),
        (200, [rec(2, "10:00:09")]),
    )
    result = run(
        opener,
        "changes_since",
        {"entity": "partners", "since": "2026-01-01 10:00:05", "after_id": 8, "limit": 5},
    )["result"]
    sent = bodies(opener)
    assert sent[1]["domain"] == [
        ["write_date", ">=", "2026-01-01 10:00:05"],
        ["write_date", "<", "2026-01-01 10:00:06"],
        ["id", ">", 8],
    ]
    assert sent[2]["domain"] == [["write_date", ">=", "2026-01-01 10:00:06"]]
    assert [r["id"] for r in result["records"]] == [43, 2]
    assert result["has_more"] is False
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:09", None)


def test_changes_nothing_new_keeps_the_cursor() -> None:
    opener = Opener((200, PFIELDS), (200, []), (200, []))
    result = run(
        opener,
        "changes_since",
        {"entity": "partners", "since": "2026-01-01 10:00:05", "after_id": 8},
    )["result"]
    assert result["count"] == 0 and result["has_more"] is False
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:05", 8)


def test_changes_resume_that_exactly_fills_the_page() -> None:
    opener = Opener((200, PFIELDS), (200, [rec(43, "10:00:05")]), (200, [rec(2, "10:00:09")]))
    result = run(
        opener,
        "changes_since",
        {"entity": "partners", "since": "2026-01-01 10:00:05", "after_id": 8, "limit": 1},
    )["result"]
    assert [r["id"] for r in result["records"]] == [43]
    assert result["has_more"] is True
    assert (result["next_since"], result["next_after_id"]) == ("2026-01-01 10:00:06", None)


# 9. attach: a failing main-attachment write reports the attachment already created.


class FakeContext:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def version(self) -> int:
        return 19

    def fields(self, model: str) -> dict[str, Any]:
        return {"message_main_attachment_id": {}}

    def call(self, model: str, method: str, **_kw: Any) -> Any:
        self.calls.append(method)
        if method == "create":
            return [9]
        raise ExtensionError(
            errors.UPSTREAM_VALIDATION_FAILED,
            "refused",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )


def test_attach_main_write_failure_reports_the_attachment() -> None:
    with pytest.raises(ExtensionError) as caught:
        attach(FakeContext(), "res.partner", 5, b"x", "a.pdf", main=True, progress=Progress())
    assert caught.value.external_effect_status == "timeout_unknown"
    assert "attachment id 9" in caught.value.message


# 10-12. call_method context, read answers, unexpected crashes after a write.


def test_call_method_context_must_be_an_object() -> None:
    response = run(
        Opener(),
        "call_method",
        {"model": "res.partner", "method": "action_archive", "kwargs": {"context": "x"}},
    )
    assert response["error_code"] == "invalid_payload"


def test_read_records_non_list_answer_is_invalid_response() -> None:
    opener = Opener((200, {}), (200, {"unexpected": True}))
    response = run(opener, "read_records", {"model": "res.partner", "ids": [1]})
    assert response["error_code"] == errors.INVALID_RESPONSE


def test_a_crash_after_a_write_was_sent_is_unknown() -> None:
    opener = Opener((200, {"name": {}}), RuntimeError("socket library bug"))
    response = run(opener, "create_record", {"model": "res.partner", "values": {"name": "X"}})
    assert response["error_code"] == errors.TIMEOUT_UNKNOWN
    assert response["definitely_no_external_effect"] is False
