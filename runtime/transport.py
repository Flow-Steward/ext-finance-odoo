"""The only way this extension reaches Odoo.

Two documented external APIs, one interface (``call(model, method, ids, kwargs)``):

* **JSON-2** (Odoo 19+, ``POST /json/2/<model>/<method>``, ``Authorization: bearer``),
  the replacement for the RPC APIs;
* **XML-RPC** (Odoo 16-20, ``/xmlrpc/2/common`` and ``/xmlrpc/2/object``,
  ``execute_kw(db, uid, key, model, method, args, kwargs)``), deprecated since 19 and
  scheduled for removal in Odoo 22 / Online 21.1.

Every request goes through the SDK's pinned opener (DNS pinning, peer check, no
redirects, no private addresses). XML-RPC answers are parsed only with defusedxml's
``DefusedExpatParser`` (DTDs and entities refused); the request XML is built here.

Outcomes. Each Odoo call runs in its own database transaction that is discarded
on error (JSON-2 docs, "Transaction"). An error *answered by Odoo* therefore proves
that nothing was written to the database — but a business method (``action_post``
with e-invoicing, ``action_confirm`` sending mail, a payment wizard, any custom
method) may have called an outside service before failing. So:

* Odoo's own refusals — UserError/ValidationError (422, XML-RPC fault 2), AccessError
  (403, fault 4), AccessDenied (401/403, fault 3), MissingError (404), LockError
  (409) — are "nothing done" for every call;
* any other server error is "nothing done" for plain CRUD (``create``, ``write``,
  ``unlink``), but ``timeout_unknown`` for business methods (``risky``);
* a timeout, a lost connection, a proxy error without an Odoo body or an unreadable
  answer to a write is ``timeout_unknown``. Nothing ambiguous is retried.
"""

from __future__ import annotations

import http.client
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request

from defusedxml.xmlrpc import DefusedExpatParser, xmlrpc_client
from flowsteward_extension_sdk import PinnedPeerError, open_pinned_url, parse_retry_after

from . import errors
from .connection import Connection
from .errors import ExtensionError
from .fields import field

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_REQUEST_BODY_BYTES = 40 * 1024 * 1024
MAX_RESPONSE_BYTES = 80 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 1024 * 1024
CHUNK_BYTES = 64 * 1024
MIN_INTERVAL_SECONDS = 0.05
BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
MIN_CALL_WINDOW_SECONDS = 15.0
_USER_AGENT = "FlowSteward-Odoo/0.1"
_MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$|^[a-z][a-z0-9_]*$")
_METHOD_RE = re.compile(r"^[a-z][a-z0-9_]{0,80}$")
#: The fault Odoo's XML-RPC raises when a method's answer is None (see _xmlrpc).
_NONE_RESULT = "cannot marshal None unless allow_none is enabled"
#: XML-RPC fault codes of /xmlrpc/2 (odoo/addons/base/controllers/rpc.py).
FAULT_APPLICATION_ERROR = 1
FAULT_WARNING = 2
FAULT_ACCESS_DENIED = 3
FAULT_ACCESS_ERROR = 4
#: How Odoo says a model does not exist: XML-RPC 16-18 "Object x doesn't exist" (a
#: warning fault), a KeyError in a traceback, or JSON-2's "the model 'x' does not exist".
_MISSING_MODEL_RE = re.compile(
    r"Object ([a-z0-9_.]+) doesn't exist|KeyError: '([a-z0-9_]+\.[a-z0-9_.]+)'"
    r"|the model '([a-z0-9_.]+)' does not exist"
)


class DeadlineReached(Exception):
    """The invocation's time budget leaves no room for another request."""


@dataclass
class Reply:
    status: int
    data: Any


def _failure(
    code: str,
    message: str,
    *,
    no_effect: bool,
    retryable: bool,
    status: int | None = None,
    detail: list[dict[str, str]] | None = None,
    retry_after: float | None = None,
) -> ExtensionError:
    return ExtensionError(
        code,
        message,
        failure_class="provider",
        retryable=retryable,
        retry_after_seconds=retry_after,
        definitely_no_external_effect=no_effect,
        external_effect_status="failed" if no_effect else "timeout_unknown",
        http_status=status,
        data_errors=detail,
    )


def _unknown(message: str, *, status: int | None = None) -> ExtensionError:
    return _failure(
        errors.TIMEOUT_UNKNOWN,
        message + "; check in Odoo whether it was applied before running the step again",
        no_effect=False,
        retryable=False,
        status=status,
    )


def _xmlrpc_values(value: Any) -> Any:
    """``None`` as ``False``, Odoo's "empty" for every field type, at any depth.

    XML-RPC has no null (Odoo serves it with allow_none off), and JSON-2 accepts
    null where XML-RPC needs False; the same inputs therefore work on both APIs.
    """
    if value is None:
        return False
    if isinstance(value, Mapping):
        return {k: _xmlrpc_values(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_xmlrpc_values(v) for v in value)
    if isinstance(value, list):
        return [_xmlrpc_values(v) for v in value]
    return value


class OdooTransport:
    """Sends Odoo requests for one connection."""

    def __init__(
        self,
        connection: Connection,
        *,
        opener: Callable[..., Any] = open_pinned_url,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.connection = connection
        self._opener = opener
        self._sleep = sleeper
        self.clock = clock
        self._timeout_seconds = timeout_seconds
        self._last_sent: float | None = None
        self._uid: int | None = None
        #: True once any request may have left this process.
        self.attempted = False
        #: True once a mutating request may have left this process.
        self.mutated = False
        #: True once Odoo answered a mutating request successfully (it committed).
        self.committed = False
        #: Monotonic time after which no request may start (set by the operation).
        self.deadline: float | None = None
        self._call_timeout = timeout_seconds

    def remaining(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - self.clock()

    # -- public calls --------------------------------------------------------

    def call(
        self,
        model: str,
        method: str,
        *,
        ids: list[int] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        mutating: bool = False,
        risky: bool = False,
    ) -> Any:
        """Call ``model.method`` on ``ids`` (or on the model) with named arguments.

        ``risky``: a business method that may reach outside services before it
        fails; any server error on it is an unknown outcome.
        """
        if not _MODEL_RE.match(model) or not _METHOD_RE.match(method):
            raise ExtensionError(
                errors.INVALID_PAYLOAD,
                "Model and method names are lower-case Odoo identifiers",
                definitely_no_external_effect=True,
                external_effect_status="failed",
            )
        arguments = {k: v for k, v in (kwargs or {}).items() if v is not None}
        if self.connection.language:
            context = dict(arguments.get("context") or {})
            context.setdefault("lang", self.connection.language)
            arguments["context"] = context
        if self.connection.api == "json2":
            answer = self._json2(model, method, ids, arguments, mutating=mutating, risky=risky)
        else:
            answer = self._execute_kw(model, method, ids, arguments, mutating=mutating, risky=risky)
        if mutating:
            # Odoo committed this change: nothing later in the step may claim "no effect".
            self.committed = True
        return answer

    def version(self) -> dict[str, Any]:
        """Server version, unauthenticated (``/web/webclient/version_info``, 16-20)."""
        body = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}).encode()
        status, raw = self._post(
            "/web/webclient/version_info", body, "application/json", mutating=False
        )
        data = self._json(raw, status, mutating=False)
        result = field(data, "result")
        if not isinstance(result, Mapping):
            raise _failure(
                errors.INVALID_RESPONSE,
                "Odoo did not report its version",
                no_effect=True,
                retryable=False,
                status=status,
            )
        return dict(result)

    # -- JSON-2 ----------------------------------------------------------------

    def _json2(
        self,
        model: str,
        method: str,
        ids: list[int] | None,
        arguments: Mapping[str, Any],
        *,
        mutating: bool,
        risky: bool,
    ) -> Any:
        payload: dict[str, Any] = dict(arguments)
        if ids is not None:
            payload["ids"] = list(ids)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = f"/json/2/{quote(model, safe='.')}/{quote(method, safe='')}"
        status, raw = self._post(path, body, "application/json", mutating=mutating, risky=risky)
        return self._json(raw, status, mutating=mutating)

    # -- XML-RPC ---------------------------------------------------------------

    def _uid_for(self) -> int:
        if self._uid is None:
            conn = self.connection
            uid = self._xmlrpc(
                "/xmlrpc/2/common",
                "authenticate",
                (conn.database, conn.login, conn.api_key, {}),
                mutating=False,
                risky=False,
            )
            if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
                raise _failure(
                    errors.AUTHENTICATION_FAILED,
                    "Odoo refused the database, login or API key",
                    no_effect=True,
                    retryable=False,
                )
            self._uid = uid
        return self._uid

    def _execute_kw(
        self,
        model: str,
        method: str,
        ids: list[int] | None,
        arguments: Mapping[str, Any],
        *,
        mutating: bool,
        risky: bool,
    ) -> Any:
        uid = self._uid_for()
        args: list[Any] = [list(ids)] if ids is not None else []
        arguments = dict(arguments)
        if method == "create" and "vals_list" in arguments:
            # execute_kw routes create through call_kw_model_create, which takes the
            # values as the first positional argument, never by name.
            args.append(arguments.pop("vals_list"))
        params = (
            self.connection.database,
            uid,
            self.connection.api_key,
            model,
            method,
            args,
            dict(arguments),
        )
        return self._xmlrpc(
            "/xmlrpc/2/object", "execute_kw", params, mutating=mutating, risky=risky
        )

    def _xmlrpc(
        self, path: str, method: str, params: tuple[Any, ...], *, mutating: bool, risky: bool
    ) -> Any:
        try:
            body = xmlrpc_client.dumps(
                _xmlrpc_values(params), methodname=method, allow_none=False
            ).encode("utf-8")
        except (TypeError, OverflowError, ValueError) as exc:
            raise ExtensionError(
                errors.INVALID_PAYLOAD,
                "A value cannot be sent over XML-RPC (numbers must fit in 32 bits; use text, "
                "numbers, true/false, lists and objects)",
                definitely_no_external_effect=True,
                external_effect_status="failed",
            ) from exc
        status, raw = self._post(path, body, "text/xml", mutating=mutating, risky=risky)
        try:
            return parse_xmlrpc(raw)
        except xmlrpc_client.Fault as fault:
            if _NONE_RESULT in str(getattr(fault, "faultString", "")):
                # Odoo's XML-RPC cannot send None: it fails while encoding the
                # answer, after the method returned and its transaction committed.
                # The method succeeded and returned nothing.
                return None
            raise fault_error(fault, mutating=mutating, risky=risky) from None
        except Exception as exc:
            if mutating:
                raise _unknown(
                    f"Odoo accepted the request (HTTP {status}) but its answer could not be read",
                    status=status,
                ) from exc
            raise _failure(
                errors.INVALID_RESPONSE,
                "Odoo's XML-RPC answer could not be read safely",
                no_effect=True,
                retryable=False,
                status=status,
            ) from exc

    # -- HTTP --------------------------------------------------------------------

    def _post(
        self,
        path: str,
        body: bytes,
        content_type: str,
        *,
        mutating: bool,
        risky: bool = False,
    ) -> tuple[int, bytes]:
        if len(body) > MAX_REQUEST_BODY_BYTES:
            raise ExtensionError(
                errors.INVALID_PAYLOAD,
                "The request is larger than the 40 MB this extension sends to Odoo",
                definitely_no_external_effect=True,
                external_effect_status="failed",
            )
        headers = {
            "Content-Type": f"{content_type}; charset=utf-8",
            "Accept": content_type,
            "User-Agent": _USER_AGENT,
        }
        if path.startswith("/json/2/"):
            headers["Authorization"] = f"bearer {self.connection.api_key}"
        if self.connection.database:
            headers["X-Odoo-Database"] = self.connection.database
        url = self.connection.base_url + path
        for attempt in range(len(BACKOFF_SECONDS) + 1):
            remaining = self.remaining()
            if remaining < MIN_CALL_WINDOW_SECONDS:
                raise DeadlineReached
            self._call_timeout = min(self._timeout_seconds, remaining)
            request = Request(url, method="POST", data=body, headers=headers)
            try:
                return self._open(request, mutating=mutating, risky=risky)
            except ExtensionError as exc:
                if exc.code != errors.RATE_LIMITED or attempt == len(BACKOFF_SECONDS):
                    raise
                wait = BACKOFF_SECONDS[attempt]
                if exc.retry_after_seconds:
                    wait = max(wait, min(float(exc.retry_after_seconds), 30.0))
                if wait > self.remaining() - MIN_CALL_WINDOW_SECONDS:
                    raise
                self._sleep(wait)
                self._last_sent = None
        raise AssertionError("unreachable")  # pragma: no cover

    def _open(self, request: Request, *, mutating: bool, risky: bool) -> tuple[int, bytes]:
        if self._last_sent is not None:
            wait = MIN_INTERVAL_SECONDS - (self.clock() - self._last_sent)
            if wait > 0:
                self._sleep(wait)
        self._last_sent = self.clock()
        try:
            self.attempted = True
            self.mutated = self.mutated or mutating
            with self._opener(
                request, timeout_seconds=self._call_timeout, purpose="Odoo API"
            ) as response:
                status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
                raw = read_bounded(
                    response,
                    MAX_RESPONSE_BYTES,
                    mutating=mutating,
                    status=status,
                    expires=None
                    if self.deadline is None
                    else self.deadline + MIN_CALL_WINDOW_SECONDS,
                    clock=self.clock,
                )
        except HTTPError as exc:
            try:
                raw = exc.read(MAX_ERROR_BODY_BYTES + 4096)
            except Exception:  # pragma: no cover - upstream body is optional
                raw = b""
            raw = self._redact(raw)[:MAX_ERROR_BODY_BYTES]
            raise status_error(
                int(exc.code), raw, exc.headers, mutating=mutating, risky=risky
            ) from exc
        except PinnedPeerError as exc:
            if "cannot be resolved" in str(exc):
                raise _failure(
                    errors.CONNECTION_FAILED,
                    f"{self.connection.host} could not be resolved; check the Odoo address",
                    no_effect=True,
                    retryable=True,
                ) from exc
            raise _failure(
                errors.BLOCKED_ADDRESS,
                f"{self.connection.host} is not reachable under the host network policy "
                "(private and reserved addresses are refused)",
                no_effect=True,
                retryable=False,
            ) from exc
        except ValueError as exc:
            # The SDK refuses a URL it will not open (scheme, local host names).
            raise _failure(
                errors.BLOCKED_ADDRESS,
                "The Odoo address is not allowed by the host network policy",
                no_effect=True,
                retryable=False,
            ) from exc
        except TimeoutError as exc:
            if mutating:
                raise _unknown("Odoo did not answer the write in time") from exc
            raise _failure(
                errors.TIMEOUT, "Odoo did not answer in time", no_effect=True, retryable=True
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            if mutating:
                raise _unknown("The connection to Odoo failed during the write") from exc
            raise _failure(
                errors.CONNECTION_FAILED,
                "The connection to Odoo failed",
                no_effect=True,
                retryable=True,
            ) from exc
        raw = self._redact(raw)
        if status >= 300:
            raise status_error(
                status, raw[:MAX_ERROR_BODY_BYTES], None, mutating=mutating, risky=risky
            )
        return status, raw

    def _redact(self, raw: bytes) -> bytes:
        """Remove the key from a body before anything parses or truncates it."""
        for secret in self.connection.secret_fragments():
            for form in {secret, json.dumps(secret)[1:-1], _xml_escaped(secret)}:
                encoded = form.encode("utf-8")
                if encoded and encoded in raw:
                    raw = raw.replace(encoded, b"[redacted]")
        return raw

    def _json(self, raw: bytes, status: int, *, mutating: bool) -> Any:
        if not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if mutating:
                raise _unknown(
                    f"Odoo accepted the request (HTTP {status}) but its answer could not be read",
                    status=status,
                ) from exc
            raise _failure(
                errors.INVALID_RESPONSE,
                "Odoo answered with something that is not JSON",
                no_effect=True,
                retryable=False,
                status=status,
            ) from exc


def _xml_escaped(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_xmlrpc(raw: bytes) -> Any:
    """One XML-RPC answer, parsed with DTDs and entities refused.

    Raises ``xmlrpc_client.Fault`` for a fault answer.
    """
    unmarshaller = xmlrpc_client.Unmarshaller(use_datetime=False, use_builtin_types=True)
    parser = DefusedExpatParser(unmarshaller, forbid_dtd=True)
    parser.feed(raw)
    parser.close()
    values = unmarshaller.close()
    return values[0] if len(values) == 1 else values


def read_bounded(
    response: Any,
    limit: int,
    *,
    mutating: bool = False,
    status: int = 0,
    expires: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """Read at most ``limit`` bytes before ``expires``."""
    buffer = bytearray()
    while True:
        chunk = response.read(min(CHUNK_BYTES, limit + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if expires is not None and clock() > expires:
            if not mutating:
                raise DeadlineReached
            raise _unknown(
                f"Odoo accepted the request (HTTP {status}) but its answer did not arrive in time"
            )
        if len(buffer) > limit:
            if mutating:
                raise _unknown(
                    f"Odoo accepted the request (HTTP {status}) but its answer was too large to read"
                )
            raise _failure(
                errors.RESPONSE_TOO_LARGE,
                "Odoo's answer exceeds this extension's limit; narrow the domain or the fields",
                no_effect=True,
                retryable=False,
            )
    return bytes(buffer)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _missing_model(text: str) -> str:
    match = _MISSING_MODEL_RE.search(text or "")
    return next((g for g in match.groups() if g), "") if match else ""


def fault_error(fault: Any, *, mutating: bool, risky: bool) -> ExtensionError:
    """An XML-RPC fault as a safe error; a traceback never passes, only its last line."""
    code = getattr(fault, "faultCode", 0)
    raw_text = str(getattr(fault, "faultString", "") or "")
    missing = _missing_model(raw_text)
    if missing:
        return _failure(
            errors.NOT_FOUND,
            f"The model {missing} does not exist in this Odoo database",
            no_effect=True,
            retryable=False,
        )
    if code == FAULT_WARNING:
        return _failure(
            errors.UPSTREAM_VALIDATION_FAILED,
            f"Odoo refused it: {errors.safe_text(raw_text, 600)}",
            no_effect=True,
            retryable=False,
            detail=[{"path": "", "code": "UserError", "message": errors.safe_text(raw_text, 300)}],
        )
    if code == FAULT_ACCESS_DENIED:
        return _failure(
            errors.AUTHENTICATION_FAILED,
            "Odoo refused the login or API key",
            no_effect=True,
            retryable=False,
        )
    if code == FAULT_ACCESS_ERROR:
        return _failure(
            errors.ACCESS_DENIED,
            f"The API key's user may not do this in Odoo: {errors.safe_text(raw_text, 400)}",
            no_effect=True,
            retryable=False,
        )
    last = errors.safe_text(_last_line(raw_text), 400)
    if mutating and risky:
        return _unknown(f"Odoo failed while running the method ({last})")
    return _failure(
        errors.UPSTREAM_FAILURE,
        f"Odoo could not complete the request: {last}",
        no_effect=True,
        retryable=not mutating,
    )


def _odoo_error(raw: bytes) -> tuple[str, str] | None:
    """(exception name, message) of an Odoo JSON-2 error body; never its traceback."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, Mapping):
        return None
    name = field(body, "name")
    message = field(body, "message")
    if not isinstance(name, str) or not name:
        error = field(body, "error")
        data = field(error, "data") if isinstance(error, Mapping) else None
        name = field(data, "name") if isinstance(data, Mapping) else None
        message = field(data, "message") if isinstance(data, Mapping) else None
        if not isinstance(name, str) or not name:
            return None
    return errors.safe_text(name, 120), errors.safe_text(message, 600)


def status_error(
    status: int, raw: bytes, headers: Any, *, mutating: bool, risky: bool
) -> ExtensionError:
    """An HTTP error answer as a safe error, by what it proves about the write."""
    odoo = _odoo_error(raw)
    name, message = odoo if odoo else ("", "")
    detail = [{"path": "", "code": name, "message": message}] if name else None
    said = f": {message}" if message else ""
    retry_after = None
    try:
        retry_after = parse_retry_after(headers.get("Retry-After")) if headers is not None else None
    except (AttributeError, TypeError, ValueError):
        retry_after = None
    if 300 <= status < 400:
        return _failure(
            errors.REDIRECT_REJECTED,
            f"Odoo redirected the request (HTTP {status}) and it was not followed",
            no_effect=not mutating,
            retryable=False,
            status=status,
        )
    if status == 429:
        return _failure(
            errors.RATE_LIMITED,
            "Odoo is rate limiting these requests",
            no_effect=True,
            retryable=True,
            status=status,
            retry_after=retry_after,
        )
    if odoo is None:
        # Not Odoo's own answer (a proxy, a gateway, a disabled endpoint).
        if status == 404:
            return _failure(
                errors.NOT_FOUND,
                "The Odoo API endpoint was not found: check the address, the API choice "
                "(json2 needs Odoo 19+) and the database",
                no_effect=True,
                retryable=False,
                status=status,
            )
        if status == 401:
            return _failure(
                errors.AUTHENTICATION_FAILED,
                "Odoo refused the API key",
                no_effect=True,
                retryable=False,
                status=status,
            )
        if mutating:
            return _unknown(f"Odoo did not confirm the write (HTTP {status})", status=status)
        return _failure(
            errors.UPSTREAM_FAILURE,
            f"Odoo could not complete the request (HTTP {status})",
            no_effect=True,
            retryable=status >= 500,
            status=status,
        )
    if status == 401:
        return _failure(
            errors.AUTHENTICATION_FAILED,
            f"Odoo refused the API key (wrong, expired or revoked){said}",
            no_effect=True,
            retryable=False,
            status=status,
        )
    if status == 403:
        return _failure(
            errors.ACCESS_DENIED,
            f"The API key's user may not do this in Odoo{said}",
            no_effect=True,
            retryable=False,
            status=status,
            detail=detail,
        )
    if status == 404:
        return _failure(
            errors.NOT_FOUND,
            f"Odoo has no such model, method or record{said}",
            no_effect=True,
            retryable=False,
            status=status,
            detail=detail,
        )
    if status == 409:
        return _failure(
            errors.CONFLICT,
            f"Odoo is busy with this record (locked); try again later{said}",
            no_effect=True,
            retryable=True,
            status=status,
            detail=detail,
        )
    if status == 422 or 400 <= status < 500:
        return _failure(
            errors.UPSTREAM_VALIDATION_FAILED,
            f"Odoo refused it{said}",
            no_effect=True,
            retryable=False,
            status=status,
            detail=detail,
        )
    # A server error Odoo answered itself: the transaction was rolled back.
    if mutating and risky:
        return _unknown(
            f"Odoo failed while running the method (HTTP {status}{said})", status=status
        )
    return _failure(
        errors.UPSTREAM_FAILURE,
        f"Odoo could not complete the request (HTTP {status}){said}",
        no_effect=True,
        retryable=not mutating,
        status=status,
        detail=detail,
    )


__all__ = [
    "BACKOFF_SECONDS",
    "DeadlineReached",
    "MIN_CALL_WINDOW_SECONDS",
    "OdooTransport",
    "Reply",
    "fault_error",
    "parse_xmlrpc",
    "read_bounded",
    "status_error",
]
