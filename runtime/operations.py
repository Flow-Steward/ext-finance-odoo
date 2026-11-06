"""Run one workflow operation against an Odoo database."""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from flowsteward_extension_sdk import (
    find_artifact_descriptor,
    read_artifact_bytes,
    write_artifact_stream,
)

from . import budget, catalog, domain, errors, friendly, versions
from .connection import Connection, connection_from_payload
from .errors import ExtensionError, error_response
from .inputs import flag as _bool
from .inputs import invalid as _invalid
from .inputs import text as _text
from .inputs import whole as _whole
from .transport import MIN_CALL_WINDOW_SECONDS, DeadlineReached, OdooTransport

DEFAULT_LIMIT = 80
MAX_LIMIT = 200
DEFAULT_MAX_PAGES = 10
MAX_PAGES = 100
EXPORT_MAX_PAGES = 2000
EXPORT_PAGE = 200
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
MAX_EXPORT_BYTES = 200 * 1024 * 1024
#: The entrypoint is given 600 s; a file-writing operation keeps the artifact
#: write's own timeout in reserve so it can always save what it read.
STEP_TIMEOUT_SECONDS = 600.0
STEP_MARGIN_SECONDS = 30.0
ARTIFACT_WRITE_TIMEOUT_SECONDS = 180.0
#: Must equal the content types granted in contracts/artifact_policies.yaml.
DOWNLOAD_CONTENT_TYPE = "application/octet-stream"
EXPORT_CONTENT_TYPE = "application/x-ndjson"
EXPORT_FILENAME = "odoo-export.jsonl"
#: read_group aggregate functions (PostgreSQL's, as Odoo documents them).
AGGREGATES: frozenset[str] = frozenset({"sum", "avg", "min", "max", "count", "count_distinct"})

_ALLOWED_ACTION_KEYS = frozenset(
    {"action_id", "operation_id", "page_id", "component_id", "context", "input", "target"}
)
_FILE_WRITING_OPS = frozenset({catalog.EXPORT_RECORDS, "download_attachment"})

TransportFactory = Callable[[Connection], OdooTransport]
ArtifactWriter = Callable[..., Mapping[str, Any]]


# ---------------------------------------------------------------------------
# Entry point


def handle_runtime(
    payload: Mapping[str, Any],
    *,
    transport_factory: TransportFactory = OdooTransport,
    artifact_reader: Callable[..., bytes] = read_artifact_bytes,
    artifact_writer: ArtifactWriter = write_artifact_stream,
) -> dict[str, Any]:
    """Dispatch one invocation through the closed registry."""
    transport: OdooTransport | None = None
    try:
        operation_id, raw_input = _envelope(payload)
        if operation_id not in catalog.OPERATION_IDS:
            raise ExtensionError(
                errors.UNSUPPORTED_OPERATION,
                "That operation is not part of this extension",
                definitely_no_external_effect=True,
                external_effect_status="failed",
            )
        operation_input = _as_input(raw_input)
        if operation_id in catalog.WRITE_OPERATION_IDS and _test_mode(payload):
            # The host suppresses test-mode writes before spawning the extension;
            # this is the backstop. Validated first, then nothing is sent.
            validate(operation_id, operation_input)
            return _ok(_neutral_result(operation_id))
        connection = connection_from_payload(
            payload, connection_ref=_text("connection_ref", operation_input.get("connection_ref"))
        )
        transport = transport_factory(connection)
        reserve = ARTIFACT_WRITE_TIMEOUT_SECONDS if operation_id in _FILE_WRITING_OPS else 0.0
        transport.deadline = (
            transport.clock() + STEP_TIMEOUT_SECONDS - STEP_MARGIN_SECONDS - reserve
        )
        context = Context(
            payload=payload,
            operation_input=operation_input,
            connection=connection,
            transport=transport,
            artifact_reader=artifact_reader,
            artifact_writer=artifact_writer,
        )
        handler = _HANDLERS.get(operation_id)
        if handler is not None:
            result = handler(context)
        else:
            result = friendly.run(context, operation_id)
        if operation_id in catalog.WRITE_OPERATION_IDS:
            return _ok(_mark_write(fit_write_result(result)))
        budget.ensure_fits(result)
        return _ok(result)
    except DeadlineReached:
        if transport is not None and transport.mutated:
            return error_response(
                errors.TIMEOUT_UNKNOWN,
                "The step ran out of time after a change was sent to Odoo; check Odoo before "
                "running it again",
                retry_facts={
                    "definitely_no_external_effect": False,
                    "external_effect_status": "timeout_unknown",
                },
            )
        return error_response(
            errors.TIMEOUT,
            "The step ran out of time before Odoo could be called",
            retry_facts={"definitely_no_external_effect": True, "external_effect_status": "failed"},
        )
    except ExtensionError as exc:
        _mark_unsent(exc, transport)
        if transport is not None and transport.committed and exc.definitely_no_external_effect:
            # Backstop for every composite step: a change Odoo already committed
            # cannot be reported as "nothing happened", whatever failed after it.
            return error_response(
                errors.TIMEOUT_UNKNOWN,
                f"{exc.message}. A change was already saved in Odoo before this failed; "
                "check Odoo before running the step again",
                retry_facts={
                    "definitely_no_external_effect": False,
                    "external_effect_status": "timeout_unknown",
                },
                data_errors=exc.data_errors,
            )
        return error_response(
            exc.code, exc.message, retry_facts=exc.retry_facts(), data_errors=exc.data_errors
        )
    except Exception:
        if transport is not None and transport.mutated:
            return error_response(
                errors.TIMEOUT_UNKNOWN,
                "The operation failed after a change was sent to Odoo; check Odoo before "
                "running it again",
                retry_facts={
                    "definitely_no_external_effect": False,
                    "external_effect_status": "timeout_unknown",
                },
            )
        unsent = transport is None or not transport.attempted
        response = error_response(errors.INTERNAL_ERROR, "The operation could not be completed")
        if unsent:
            response["definitely_no_external_effect"] = True
            response["external_effect_status"] = "failed"
        return response


def _mark_unsent(exc: ExtensionError, transport: OdooTransport | None) -> None:
    """A failure before any request left the process cannot have had an effect."""
    if transport is not None and transport.attempted:
        return
    if exc.definitely_no_external_effect is None:
        exc.definitely_no_external_effect = True
    if exc.definitely_no_external_effect and exc.external_effect_status is None:
        exc.external_effect_status = "failed"


class Context:
    """One invocation: the connection, the transport and what was learned so far."""

    def __init__(
        self,
        *,
        payload: Mapping[str, Any],
        operation_input: Mapping[str, Any],
        connection: Connection,
        transport: OdooTransport,
        artifact_reader: Callable[..., bytes],
        artifact_writer: ArtifactWriter,
    ) -> None:
        self.payload = payload
        self.operation_input = operation_input
        self.connection = connection
        self.transport = transport
        self.artifact_reader = artifact_reader
        self.artifact_writer = artifact_writer
        self._version: int | None = connection.odoo_version
        self._fields: dict[str, dict[str, Any]] = {}

    def clock(self) -> float:
        clock = getattr(self.transport, "clock", None)
        return float(clock()) if callable(clock) else time.monotonic()

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
        return self.transport.call(
            model, method, ids=ids, kwargs=kwargs, mutating=mutating, risky=risky
        )

    def version(self) -> int:
        """The Odoo major version: the connection's setting, or one cheap read."""
        if self._version is None:
            info = self.transport.version()
            self._version = versions.major(info.get("server_version_info"))
        return self._version

    def fields(self, model: str) -> dict[str, Any]:
        """``fields_get`` of a model (type, relation, string), read once per step.

        A model that does not exist answers 404 / KeyError: reported as the
        module that provides it not being installed when a step needs it.
        """
        if model not in self._fields:
            answer = self.call(
                model, "fields_get", kwargs={"attributes": ["type", "relation", "string"]}
            )
            if not isinstance(answer, Mapping):
                raise ExtensionError(
                    errors.INVALID_RESPONSE,
                    f"Odoo did not describe the fields of {model}",
                    definitely_no_external_effect=True,
                    external_effect_status="failed",
                )
            self._fields[model] = dict(answer)
        return self._fields[model]


# ---------------------------------------------------------------------------
# Envelope and inputs


def _envelope(payload: Mapping[str, Any]) -> tuple[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("mode") != "action":
        raise _invalid("Request mode must be action")
    action = payload.get("action")
    if not isinstance(action, Mapping) or set(action) - _ALLOWED_ACTION_KEYS:
        raise _invalid("The action envelope is invalid")
    operation_id = action.get("action_id") or action.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise _invalid("An operation id is required")
    return operation_id.strip(), action.get("input")


def _as_input(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _invalid("The operation input must be an object")
    return {str(k): v for k, v in raw.items() if v is not None}


def _test_mode(payload: Mapping[str, Any]) -> bool:
    context = payload.get("runtime_context")
    if not isinstance(context, Mapping):
        return False
    value = context.get("test_mode", False)
    if not isinstance(value, bool):
        raise _invalid("runtime_context.test_mode must be boolean")
    return value


def _allowed(inputs: Mapping[str, Any], names: set[str]) -> None:
    unknown = set(inputs) - names - {"connection_ref"}
    if unknown:
        raise _invalid(f"Unknown input: {', '.join(sorted(unknown))}")


def model_name(inputs: Mapping[str, Any]) -> str:
    model = _text("model", inputs.get("model"), required=True, limit=80)
    if not catalog.MODEL_RE.fullmatch(model):
        raise _invalid("model must be a technical model name such as res.partner")
    return model


# ---------------------------------------------------------------------------
# Results


def _neutral_result(operation_id: str) -> dict[str, Any]:
    """Every declared output of a write, for a test run where nothing was sent."""
    result = dict(
        catalog.NEUTRAL_OUTPUTS.get(operation_id) or friendly.neutral_result(operation_id)
    )
    # Nothing was sent. "failed" + definitely-no-effect is the pair Core accepts
    # for "no external effect happened"; an extension may not report "suppressed".
    result["external_effect_status"] = "failed"
    result["definitely_no_external_effect"] = True
    return result


def _mark_write(result: dict[str, Any]) -> dict[str, Any]:
    result["external_effect_status"] = "succeeded"
    result["definitely_no_external_effect"] = False
    return result


def _ok(result: Mapping[str, Any]) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "result": dict(result),
        "error_code": None,
        "error": None,
        "errors": [],
    }
    if "external_effect_status" in result:
        response["external_effect_status"] = result["external_effect_status"]
        response["definitely_no_external_effect"] = result["definitely_no_external_effect"]
    return response


def fit_write_result(result: dict[str, Any]) -> dict[str, Any]:
    """Shrink a write's echo until it fits; the write itself already happened."""
    if budget.fits(result):
        return result
    for key in ("record", "records", "result", "response"):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k: v for k, v in value.items() if k in {"id", "name", "display_name"}}
        elif isinstance(value, list):
            result[key] = []
    if not budget.fits(result):
        for key in ("record", "records", "result", "response"):
            if key in result:
                result[key] = None
    return result


def validate(operation_id: str, inputs: Mapping[str, Any]) -> None:
    """Check a write's inputs without calling Odoo (test-mode backstop)."""
    inputs = {k: v for k, v in inputs.items() if k != "connection_ref"}
    if operation_id in catalog.FRIENDLY_IDS:
        friendly.validate(operation_id, inputs)
        return
    _allowed(inputs, catalog.GENERIC_INPUTS[operation_id])
    model_name(inputs)
    if operation_id == "create_record":
        domain.values(inputs.get("values"), None)
    elif operation_id == "update_records":
        domain.ids(inputs.get("ids"))
        domain.values(inputs.get("values"), None)
    elif operation_id == "delete_records":
        domain.ids(inputs.get("ids"))
    elif operation_id == "call_method":
        _method(inputs)


# ---------------------------------------------------------------------------
# Generic operations


def test_connection(context: Context) -> dict[str, Any]:
    _allowed(context.operation_input, set())
    info = context.transport.version()
    major = versions.major(info.get("server_version_info"))
    if context.connection.odoo_version and context.connection.odoo_version != major:
        raise ExtensionError(
            errors.INVALID_CONNECTION,
            f"The connection says Odoo {context.connection.odoo_version} but the server runs "
            f"Odoo {major}; set the version to auto or {major}",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    if context.connection.api == "json2" and major < 19:
        raise ExtensionError(
            errors.INVALID_CONNECTION,
            f"The JSON-2 API needs Odoo 19 or later; this server runs Odoo {major}: choose XML-RPC",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    context._version = major
    # One authenticated read proves the key, the database and the user.
    user = context.call("res.users", "context_get")
    return {
        "connected": True,
        "odoo_version": major,
        "server_version": errors.safe_text(info.get("server_version"), 40) or None,
        "api": context.connection.api,
        "language": errors.safe_text(user.get("lang"), 20) if isinstance(user, Mapping) else None,
    }


def _limit(inputs: Mapping[str, Any]) -> int:
    return _whole("limit", inputs.get("limit"), default=DEFAULT_LIMIT, maximum=MAX_LIMIT)


def search_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["search_records"])
    model = model_name(inputs)
    known = context.fields(model)
    query = domain.domain(inputs.get("domain"), known)
    fields = domain.field_list(inputs.get("fields"), known) or ["id", "display_name"]
    order = domain.order(inputs.get("order"), known)
    limit = _limit(inputs)
    offset = _whole("offset", inputs.get("offset"), default=0, minimum=0, maximum=10**9)
    fetch_all = _bool("fetch_all_pages", inputs.get("fetch_all_pages"), False)
    max_pages = _whole(
        "max_pages", inputs.get("max_pages"), default=DEFAULT_MAX_PAGES, maximum=MAX_PAGES
    )
    read = read_pages(
        context,
        model,
        query,
        fields,
        order=order,
        limit=limit,
        offset=offset,
        max_pages=max_pages if fetch_all else 1,
    )

    def build(done: dict[str, Any]) -> dict[str, Any]:
        records = done["records"]
        return {
            "model": model,
            "records": records,
            "record": records[0] if records else None,
            "count": len(records),
            "next_offset": done["next_offset"],
            "has_more": done["has_more"],
        }

    return fit_pages(read, build)


def read_pages(
    context: Context,
    model: str,
    query: list[Any],
    fields: list[str],
    *,
    order: str | None,
    limit: int,
    offset: int,
    max_pages: int,
) -> dict[str, Any]:
    """search_read page by page until a short page, max_pages, the budget or the deadline."""
    records: list[Any] = []
    sizes: list[int] = []
    meter = budget.Meter()
    has_more = False
    position = offset
    while len(sizes) < max_pages:
        try:
            rows = context.call(
                model,
                "search_read",
                kwargs={
                    "domain": query,
                    "fields": fields,
                    "offset": position,
                    "limit": limit,
                    "order": order,
                },
            )
        except DeadlineReached:
            if not sizes:
                raise
            has_more = True
            break
        if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
            raise ExtensionError(
                errors.INVALID_RESPONSE,
                f"Odoo's search_read answer for {model} is not a list of records",
                definitely_no_external_effect=True,
                external_effect_status="failed",
            )
        chunk = [rows, rows[0]] if not sizes and rows else rows
        if not meter.admit(chunk):
            if not sizes:
                raise budget.too_large()
            has_more = True
            break
        records.extend(rows)
        sizes.append(len(rows))
        position += len(rows)
        has_more = len(rows) >= limit
        if not has_more:
            break
    return {
        "records": records,
        "page_sizes": sizes,
        "first_offset": offset,
        "next_offset": position if has_more else None,
        "has_more": has_more,
    }


def fit_pages(
    read: dict[str, Any], build: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    """The result built from ``read``, dropping whole trailing pages until it fits."""
    while True:
        result = build(read)
        if budget.fits(result):
            return result
        sizes = read["page_sizes"]
        if len(sizes) <= 1:
            raise budget.too_large()
        dropped = sizes.pop()
        read["records"] = read["records"][: len(read["records"]) - dropped]
        read["next_offset"] = int(read.get("first_offset") or 0) + sum(sizes)
        read["has_more"] = True


def read_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["read_records"])
    model = model_name(inputs)
    known = context.fields(model)
    record_ids = domain.ids(inputs.get("ids"), limit=MAX_LIMIT)
    fields = domain.field_list(inputs.get("fields"), known) or ["id", "display_name"]
    rows = context.call(model, "read", ids=record_ids, kwargs={"fields": fields})
    if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
        raise ExtensionError(
            errors.INVALID_RESPONSE,
            f"Odoo's answer for {model} read is not a list of records",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    records = [dict(r) for r in rows]
    return {
        "model": model,
        "records": records,
        "record": records[0] if records else None,
        "count": len(records),
    }


def count_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["count_records"])
    model = model_name(inputs)
    query = domain.domain(inputs.get("domain"), context.fields(model))
    count = context.call(model, "search_count", kwargs={"domain": query})
    if isinstance(count, bool) or not isinstance(count, int):
        raise ExtensionError(
            errors.INVALID_RESPONSE,
            "Odoo's search_count answer is not a number",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    return {"model": model, "count": count}


def group_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["group_records"])
    model = model_name(inputs)
    known = context.fields(model)
    query = domain.domain(inputs.get("domain"), known)
    groupby = _groupby(inputs.get("groupby"), known)
    aggregates = _aggregates(inputs.get("aggregates"), known)
    limit = _whole("limit", inputs.get("limit"), default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
    if versions.name("read_group_style", context.version()) == "aggregates":
        rows = context.call(
            model,
            "read_group",
            kwargs={"domain": query, "groupby": groupby, "aggregates": aggregates, "limit": limit},
        )
        names = [*groupby, *aggregates]
        groups = [
            dict(zip(names, row, strict=False))
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, (list, tuple))
        ]
    else:
        # The legacy form counts every group itself (__count / <field>_count).
        legacy = [a for a in aggregates if a != "__count"]
        rows = context.call(
            model,
            "read_group",
            kwargs={
                "domain": query,
                "fields": legacy,
                "groupby": groupby,
                "limit": limit,
                "lazy": False,
            },
        )
        groups = [
            {k: v for k, v in row.items() if not k.startswith("__")}
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, Mapping)
        ]
    return {"model": model, "groups": groups, "count": len(groups)}


#: Date grouping steps every supported version (16-20) accepts after "field:".
GRANULARITIES = frozenset({"hour", "day", "week", "month", "quarter", "year"})


def _groupby(raw: Any, known: Mapping[str, Any]) -> list[str]:
    """Fields to group by; a date or datetime field may carry ``:month`` etc."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 10:
        raise _invalid("groupby must be a list of at most 10 fields, e.g. ['date:month']")
    result = []
    for index, item in enumerate(raw):
        where = f"groupby[{index}]"
        if not isinstance(item, str):
            raise _invalid(f"{where} must be a field name")
        name, _, granularity = item.partition(":")
        domain.field_list([name], known, name=where)
        if ":" in item:
            if granularity not in GRANULARITIES:
                raise _invalid(
                    f"{where} grouping must be one of {', '.join(sorted(GRANULARITIES))}"
                )
            kind = known.get(name, {}).get("type") if isinstance(known.get(name), Mapping) else None
            if kind not in (None, "date", "datetime"):
                raise _invalid(f"{where}: only a date or datetime field takes :{granularity}")
        result.append(item)
    return result


def _aggregates(raw: Any, known: Mapping[str, Any]) -> list[str]:
    if raw is None:
        return ["__count"]
    if not isinstance(raw, list) or len(raw) > 20:
        raise _invalid("aggregates must be a list such as ['amount_total:sum']")
    result = []
    for index, item in enumerate(raw):
        if item == "__count":
            result.append(item)
            continue
        if not isinstance(item, str) or item.count(":") != 1:
            raise _invalid(f"aggregates[{index}] must be field:function, e.g. amount_total:sum")
        name, function = item.split(":")
        domain.field_list([name], known, name=f"aggregates[{index}]")
        if function not in AGGREGATES:
            raise _invalid(
                f"aggregates[{index}] function must be one of {', '.join(sorted(AGGREGATES))}"
            )
        result.append(item)
    return result


def describe_fields(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["describe_fields"])
    model = model_name(inputs)
    answer = context.call(
        model,
        "fields_get",
        kwargs={"attributes": ["string", "type", "relation", "required", "readonly", "selection"]},
    )
    described = dict(answer) if isinstance(answer, Mapping) else {}
    return {"model": model, "fields": described, "count": len(described)}


def create_record(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["create_record"])
    model = model_name(inputs)
    values = domain.values(inputs.get("values"), context.fields(model))
    answer = context.call(model, "create", kwargs={"vals_list": [values]}, mutating=True)
    new_id = answer[0] if isinstance(answer, list) and answer else answer
    if isinstance(new_id, bool) or not isinstance(new_id, int):
        raise ExtensionError(
            errors.TIMEOUT_UNKNOWN,
            f"Odoo answered the create on {model} without a record id; check Odoo",
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
        )
    return {"model": model, "id": new_id}


def update_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["update_records"])
    model = model_name(inputs)
    record_ids = domain.ids(inputs.get("ids"))
    values = domain.values(inputs.get("values"), context.fields(model))
    answer = context.call(model, "write", ids=record_ids, kwargs={"vals": values}, mutating=True)
    return {"model": model, "ids": record_ids, "updated": bool(answer)}


def delete_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["delete_records"])
    model = model_name(inputs)
    record_ids = domain.ids(inputs.get("ids"))
    answer = context.call(model, "unlink", ids=record_ids, mutating=True)
    return {"model": model, "ids": record_ids, "deleted": bool(answer)}


def _method(inputs: Mapping[str, Any]) -> tuple[str, list[int] | None, dict[str, Any]]:
    method = _text("method", inputs.get("method"), required=True, limit=80)
    if not catalog.METHOD_RE.fullmatch(method):
        raise _invalid("method must be a public Odoo method name (lower case, not starting with _)")
    record_ids = domain.ids(inputs.get("ids")) if inputs.get("ids") is not None else None
    kwargs = inputs.get("kwargs") or {}
    if not isinstance(kwargs, Mapping) or len(kwargs) > 50:
        raise _invalid("kwargs must be an object of named arguments")
    for key in kwargs:
        if not isinstance(key, str) or not catalog.METHOD_RE.fullmatch(key):
            raise _invalid("kwargs names must be lower-case identifiers")
    if "context" in kwargs and not isinstance(kwargs["context"], Mapping):
        raise _invalid('kwargs.context must be an object such as {"lang": "en_US"}')
    return method, record_ids, dict(kwargs)


def call_method(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["call_method"])
    model = model_name(inputs)
    method, record_ids, kwargs = _method(inputs)
    # Any method may reach outside services: a server error is an unknown outcome.
    answer = context.call(model, method, ids=record_ids, kwargs=kwargs, mutating=True, risky=True)
    result = {"model": model, "method": method, "result": answer}
    if not budget.fits(result):
        result["result"] = None
    return result


def export_records(context: Context) -> dict[str, Any]:
    inputs = context.operation_input
    _allowed(inputs, catalog.GENERIC_INPUTS["export_records"])
    model = model_name(inputs)
    known = context.fields(model)
    query = domain.domain(inputs.get("domain"), known)
    fields = domain.field_list(inputs.get("fields"), known) or ["id", "display_name"]
    order = domain.order(inputs.get("order"), known) or "id"
    offset = _whole("start_offset", inputs.get("start_offset"), default=0, minimum=0, maximum=10**9)
    max_pages = _whole(
        "max_pages", inputs.get("max_pages"), default=EXPORT_MAX_PAGES, maximum=EXPORT_MAX_PAGES
    )
    chunks: list[bytes] = []
    size = count = pages = 0
    complete = False
    stopped: str | None = None
    while pages < max_pages:
        try:
            rows = context.call(
                model,
                "search_read",
                kwargs={
                    "domain": query,
                    "fields": fields,
                    "offset": offset,
                    "limit": EXPORT_PAGE,
                    "order": order,
                },
            )
            if not isinstance(rows, list):
                raise ExtensionError(
                    errors.INVALID_RESPONSE,
                    "Odoo's search_read answer is not a list",
                    definitely_no_external_effect=True,
                    external_effect_status="failed",
                )
        except DeadlineReached:
            if pages == 0:
                raise
            stopped = "The step ran out of time"
            break
        except ExtensionError as exc:
            if pages == 0:
                raise
            stopped = f"{exc.code}: {exc.message}"
            break
        page = [(json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows]
        page_bytes = sum(len(line) for line in page)
        if size + page_bytes > MAX_EXPORT_BYTES:
            if pages == 0:
                raise budget.too_large()
            stopped = "The export file reached 200 MB"
            break
        pages += 1
        chunks.extend(page)
        size += page_bytes
        count += len(page)
        offset += len(rows)
        if len(rows) < EXPORT_PAGE:
            complete = True
            break
    handle = write_artifact(
        context,
        chunks,
        binding_key=catalog.EXPORT_BINDING,
        content_type=EXPORT_CONTENT_TYPE,
        what="The export file",
    )
    return {
        "artifact_handle": handle,
        "filename": EXPORT_FILENAME,
        "content_type": EXPORT_CONTENT_TYPE,
        "record_count": count,
        "pages_fetched": pages,
        "complete": complete,
        "resume_offset": None if complete else offset,
        "stopped_by": stopped,
    }


# ---------------------------------------------------------------------------
# Files


def write_artifact(
    context: Context, chunks: list[bytes], *, binding_key: str, content_type: str, what: str
) -> str:
    try:
        written = context.artifact_writer(
            dict(context.payload),
            chunks,
            binding_key=binding_key,
            content_type=content_type,
            timeout_seconds=ARTIFACT_WRITE_TIMEOUT_SECONDS,
        )
    except ExtensionError:
        raise
    except Exception as exc:
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE,
            f"{what} could not be saved",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc
    handle = (
        _text("artifact_handle", written.get("artifact_handle"))
        if isinstance(written, Mapping)
        else ""
    )
    if not handle:
        raise ExtensionError(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE,
            f"{what} could not be saved",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    return handle


def read_input_file(context: Context, handle: str) -> tuple[bytes, str]:
    """A workflow file's bytes and name, size-checked before and while reading."""
    name = ""
    declared = None
    try:
        descriptor = find_artifact_descriptor(dict(context.payload), artifact_id=handle)
    except Exception:
        descriptor = {}
    if isinstance(descriptor, Mapping):
        size = descriptor.get("size_bytes")
        declared = size if isinstance(size, int) and not isinstance(size, bool) else None
        for key in ("filename", "name"):
            value = descriptor.get(key)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise _invalid("The file is larger than 25 MB")
    remaining = context.transport.remaining() - MIN_CALL_WINDOW_SECONDS
    if remaining <= 0:
        raise DeadlineReached
    try:
        body = context.artifact_reader(
            dict(context.payload),
            artifact_id=handle,
            timeout_seconds=min(ARTIFACT_WRITE_TIMEOUT_SECONDS, remaining),
        )
    except ExtensionError:
        raise
    except Exception as exc:
        raise ExtensionError(
            errors.ARTIFACT_INPUT_UNAVAILABLE,
            "The file could not be read from Flow Steward storage",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise _invalid("The file is empty")
    if len(body) > MAX_UPLOAD_BYTES:
        raise _invalid("The file is larger than 25 MB")
    return bytes(body), name


def save_attachment_content(
    context: Context, attachment: Mapping[str, Any], *, binding_key: str
) -> tuple[str, int]:
    """Decode an attachment's base64 content (datas / raw) and save it as a workflow file."""
    content_field = versions.name("attachment_content", context.version())
    encoded = attachment.get(content_field)
    if isinstance(encoded, Mapping):
        # Odoo 20 reads the raw field as {"filename": ..., "content": base64}.
        encoded = encoded.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ExtensionError(
            errors.NOT_FOUND,
            "The attachment has no stored content (it may be a URL attachment)",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    try:
        body = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ExtensionError(
            errors.INVALID_RESPONSE,
            "Odoo returned attachment content that is not valid Base64",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ExtensionError(
            errors.RESPONSE_TOO_LARGE,
            "The attachment is larger than 40 MB",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    handle = write_artifact(
        context,
        [body],
        binding_key=binding_key,
        content_type=DOWNLOAD_CONTENT_TYPE,
        what="The file",
    )
    return handle, len(body)


_HANDLERS: dict[str, Callable[[Context], dict[str, Any]]] = {
    catalog.TEST_CONNECTION: test_connection,
    "search_records": search_records,
    "read_records": read_records,
    "count_records": count_records,
    "group_records": group_records,
    "describe_fields": describe_fields,
    "create_record": create_record,
    "update_records": update_records,
    "delete_records": delete_records,
    "call_method": call_method,
    catalog.EXPORT_RECORDS: export_records,
}


__all__ = [
    "Context",
    "DOWNLOAD_CONTENT_TYPE",
    "EXPORT_CONTENT_TYPE",
    "fit_pages",
    "handle_runtime",
    "read_input_file",
    "read_pages",
    "save_attachment_content",
    "validate",
    "write_artifact",
]
