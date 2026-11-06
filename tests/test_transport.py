"""Transport: safe XML-RPC, JSON-2 and XML-RPC error mapping, outcomes, redaction."""

from __future__ import annotations

import io
import json
from email.message import Message
from typing import Any
from urllib.error import HTTPError

import pytest
from defusedxml.xmlrpc import xmlrpc_client

from runtime import errors
from runtime.connection import Connection
from runtime.errors import ExtensionError
from runtime.transport import OdooTransport, parse_xmlrpc

KEY = "odoo-fixture-key-0123456789abcdef"  # pragma: allowlist secret


def connection(api: str = "json2") -> Connection:
    return Connection(
        connection_id="c1",
        base_url="https://erp.example.com",
        database="db1",
        login="bot@example.com",
        api=api,
        odoo_version=None,
        language="",
        _api_key=KEY,
    )


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = Message()

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = (
            (self._body, b"") if size < 0 else (self._body[:size], self._body[size:])
        )
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


class Opener:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[Any] = []

    def __call__(self, request, *, timeout_seconds, purpose):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        status, body = answer
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 300:
            raise HTTPError(request.full_url, status, "error", Message(), io.BytesIO(raw))
        return Response(status, raw)


def transport(opener: Opener, api: str = "json2") -> OdooTransport:
    return OdooTransport(connection(api), opener=opener, sleeper=lambda _s: None)


def xml_answer(value: Any) -> bytes:
    return xmlrpc_client.dumps((value,), methodresponse=True).encode()


def xml_fault(code: int, text: str) -> bytes:
    return xmlrpc_client.dumps(xmlrpc_client.Fault(code, text), methodresponse=True).encode()


def test_an_xml_bomb_is_refused() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b"<methodResponse><params><param><value><string>&lol2;</string></value></param></params></methodResponse>"
    )
    with pytest.raises(Exception) as caught:
        parse_xmlrpc(bomb)
    assert "DTD" in type(caught.value).__name__ or "Forbidden" in type(caught.value).__name__


def test_an_xml_bomb_from_the_server_is_a_safe_error() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><methodResponse/>'
    opener = Opener((200, xml_answer(7)), (200, bomb))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call("res.partner", "search_count", kwargs={"domain": []})
    assert caught.value.code == errors.INVALID_RESPONSE


def test_json2_request_shape() -> None:
    opener = Opener((200, [{"id": 1}]))
    rows = transport(opener).call(
        "res.partner", "search_read", kwargs={"domain": [], "fields": ["name"]}
    )
    request = opener.requests[0]
    assert rows == [{"id": 1}]
    assert request.full_url == "https://erp.example.com/json/2/res.partner/search_read"
    assert request.get_header("Authorization") == f"bearer {KEY}"
    assert request.get_header("X-odoo-database") == "db1"
    assert json.loads(request.data) == {"domain": [], "fields": ["name"]}


def test_xmlrpc_authenticates_and_sends_create_values_positionally() -> None:
    opener = Opener((200, xml_answer(2)), (200, xml_answer([41])))
    new = transport(opener, "xmlrpc").call(
        "res.partner", "create", kwargs={"vals_list": [{"name": "X"}]}, mutating=True
    )
    assert new == [41]
    params, method = xmlrpc_client.loads(opener.requests[1].data)
    assert method == "execute_kw"
    assert params[:6] == ("db1", 2, KEY, "res.partner", "create", [[{"name": "X"}]])
    assert params[6] == {}


def test_xmlrpc_wrong_key_is_authentication_failed() -> None:
    opener = Opener((200, xml_answer(False)))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call("res.partner", "search_count", kwargs={})
    assert caught.value.code == errors.AUTHENTICATION_FAILED


@pytest.mark.parametrize(
    ("code", "expected", "no_effect"),
    [
        (2, errors.UPSTREAM_VALIDATION_FAILED, True),
        (3, errors.AUTHENTICATION_FAILED, True),
        (4, errors.ACCESS_DENIED, True),
    ],
)
def test_xmlrpc_refusals_prove_nothing_was_written(code, expected, no_effect) -> None:
    opener = Opener((200, xml_answer(2)), (200, xml_fault(code, "Refused")))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call(
            "account.move", "action_post", ids=[1], mutating=True, risky=True
        )
    assert caught.value.code == expected
    assert caught.value.definitely_no_external_effect is no_effect


def test_xmlrpc_server_error_is_unknown_for_business_methods_only() -> None:
    trace = "Traceback (most recent call last):\n  File secret path\nRuntimeError: EDI gateway down"
    opener = Opener((200, xml_answer(2)), (200, xml_fault(1, trace)))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call(
            "account.move", "action_post", ids=[1], mutating=True, risky=True
        )
    assert caught.value.code == errors.TIMEOUT_UNKNOWN
    assert "secret path" not in caught.value.message and "EDI gateway down" in caught.value.message
    opener = Opener((200, xml_answer(2)), (200, xml_fault(1, trace)))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call(
            "res.partner", "write", ids=[1], kwargs={"vals": {}}, mutating=True
        )
    assert caught.value.code == errors.UPSTREAM_FAILURE
    assert caught.value.definitely_no_external_effect is True


def test_xmlrpc_none_answer_is_success() -> None:
    opener = Opener(
        (200, xml_answer(2)),
        (200, xml_fault(1, "TypeError: cannot marshal None unless allow_none is enabled")),
    )
    assert (
        transport(opener, "xmlrpc").call(
            "res.partner", "action_archive", ids=[1], mutating=True, risky=True
        )
        is None
    )


def test_missing_model_is_not_found() -> None:
    opener = Opener((200, xml_answer(2)), (200, xml_fault(2, "Object no.model doesn't exist")))
    with pytest.raises(ExtensionError) as caught:
        transport(opener, "xmlrpc").call("no.model", "fields_get", kwargs={})
    assert caught.value.code == errors.NOT_FOUND
    body = {
        "name": "werkzeug.exceptions.NotFound",
        "message": "the model 'no.model' does not exist",
        "debug": "trace",
    }
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((404, body))).call("no.model", "fields_get", kwargs={})
    assert caught.value.code == errors.NOT_FOUND


@pytest.mark.parametrize(
    ("status", "name", "expected"),
    [
        (401, "werkzeug.exceptions.Unauthorized", errors.AUTHENTICATION_FAILED),
        (403, "odoo.exceptions.AccessError", errors.ACCESS_DENIED),
        (404, "odoo.exceptions.MissingError", errors.NOT_FOUND),
        (409, "odoo.exceptions.LockError", errors.CONFLICT),
        (422, "odoo.exceptions.ValidationError", errors.UPSTREAM_VALIDATION_FAILED),
    ],
)
def test_json2_refusals_prove_nothing_was_written(status, name, expected) -> None:
    body = {"name": name, "message": "No", "arguments": ["No"], "debug": "Traceback secret"}
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((status, body))).call(
            "account.move", "action_post", ids=[1], mutating=True, risky=True
        )
    assert caught.value.code == expected
    assert caught.value.definitely_no_external_effect is True
    assert "secret" not in caught.value.message


def test_json2_server_error_crud_vs_business_method() -> None:
    body = {"name": "builtins.RuntimeError", "message": "boom", "debug": "trace"}
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((500, body))).call(
            "res.partner", "create", kwargs={"vals_list": [{}]}, mutating=True
        )
    assert (
        caught.value.code == errors.UPSTREAM_FAILURE
        and caught.value.definitely_no_external_effect is True
    )
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((500, body))).call(
            "sale.order", "action_confirm", ids=[1], mutating=True, risky=True
        )
    assert (
        caught.value.code == errors.TIMEOUT_UNKNOWN
        and caught.value.definitely_no_external_effect is False
    )


def test_a_proxy_error_on_a_write_is_unknown_and_on_a_read_retryable() -> None:
    html = b"<html>502 Bad Gateway</html>"
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((502, html))).call(
            "res.partner", "create", kwargs={"vals_list": [{}]}, mutating=True
        )
    assert caught.value.code == errors.TIMEOUT_UNKNOWN
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((502, html))).call("res.partner", "search_count", kwargs={})
    assert caught.value.code == errors.UPSTREAM_FAILURE and caught.value.retryable is True


def test_a_timeout_on_a_write_is_unknown() -> None:
    with pytest.raises(ExtensionError) as caught:
        transport(Opener(TimeoutError())).call("res.partner", "unlink", ids=[1], mutating=True)
    assert caught.value.code == errors.TIMEOUT_UNKNOWN


def test_the_key_is_redacted_from_answers() -> None:
    body = {"name": "odoo.exceptions.UserError", "message": f"bad key {KEY}"}
    with pytest.raises(ExtensionError) as caught:
        transport(Opener((422, body))).call("res.partner", "search_count", kwargs={})
    assert KEY not in caught.value.message and "[redacted]" in caught.value.message


def test_private_and_odd_names_are_refused_before_sending() -> None:
    opener = Opener()
    for model, method in (
        ("res.partner", "_compute_display_name"),
        ("Res.Partner", "read"),
        ("res.partner", "../x"),
    ):
        with pytest.raises(ExtensionError):
            transport(opener).call(model, method)
    assert opener.requests == []


def test_429_is_retried() -> None:
    opener = Opener((429, b""), (200, 3))
    assert transport(opener).call("res.partner", "search_count", kwargs={}) == 3
    assert len(opener.requests) == 2
