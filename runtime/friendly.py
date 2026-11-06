"""Everyday operations: plain inputs, several Odoo calls in one step.

Rules every operation follows:

* the module a step needs (account, sale, stock) is checked before any write;
  a missing one is ``module_not_installed`` with no effect;
* lookups run before creates (partners, products, invoices, orders, payments),
  so a rerun after an unknown outcome finds what the first run made;
* money and quantities are ``Decimal`` from input to output; totals are exact
  decimal text; values sent to Odoo stay within 12 significant digits, which a
  JSON or XML-RPC float carries exactly;
* field names that differ between Odoo versions come from ``versions``;
* a composite step that fails part-way reports exactly what it created and is
  not retried (``timeout_unknown``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from . import domain, errors, versions
from .errors import ExtensionError
from .fields import field
from .inputs import flag, invalid, text, whole
from .transport import DeadlineReached

MONEY = Decimal("0.01")
PRICE = Decimal("0.0001")
QUANTITY = Decimal("0.0001")
#: At most 12 significant digits: exact as a binary float.
MAX_AMOUNT = Decimal("9999999999.99")
MAX_QUANTITY = Decimal("99999999.9999")
MAX_LINES = 200
PAGE = 80

_NUMBER_RE = re.compile(r"^-?[0-9]{0,15}(\.[0-9]{1,6})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")

#: Which installed module provides a model the everyday steps rely on.
MODULE_OF = {
    "account.move": "account",
    "account.payment": "account",
    "account.payment.register": "account",
    "account.tax": "account",
    "account.journal": "account",
    "sale.order": "sale",
    "stock.quant": "stock",
}
#: Lists "changes since" may read: entity -> model.
CHANGE_MODELS: dict[str, str] = {
    "partners": "res.partner",
    "products": "product.product",
    "invoices_and_bills": "account.move",
    "payments": "account.payment",
    "sale_orders": "sale.order",
    "stock_quants": "stock.quant",
}
MOVE_TYPES = {"create_customer_invoice": "out_invoice", "create_vendor_bill": "in_invoice"}


# ---------------------------------------------------------------------------
# Inputs


def _allowed(inputs: Mapping[str, Any], names: set[str]) -> None:
    unknown = set(inputs) - names - {"connection_ref"}
    if unknown:
        raise invalid(f"Unknown input: {', '.join(sorted(unknown))}")


def decimal_input(
    value: Any, name: str, step: Decimal, *, minimum: Decimal | None = None, maximum: Decimal
) -> Decimal:
    """A number: JSON numbers or text with "." as the only decimal separator."""
    if isinstance(value, bool):
        raise invalid(f"{name} must be a number")
    if isinstance(value, (int, float)):
        raw = repr(value) if isinstance(value, float) else str(value)
    elif isinstance(value, str) and value.strip() and _NUMBER_RE.fullmatch(value.strip()):
        raw = value.strip()
    else:
        raise invalid(f"{name} must be a number written with a '.' decimal point")
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise invalid(f"{name} must be a number") from exc
    if not number.is_finite():
        raise invalid(f"{name} must be a number")
    number = number.quantize(step, rounding=ROUND_HALF_UP)
    if minimum is not None and number < minimum:
        raise invalid(f"{name} must be at least {minimum}")
    if number > maximum:
        raise invalid(f"{name} must be at most {maximum}")
    return number


def wire(number: Decimal) -> int | float:
    return int(number) if number == number.to_integral_value() else float(number)


def _date(inputs: Mapping[str, Any], name: str) -> str:
    value = inputs.get(name)
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise invalid(f"{name} must be a date YYYY-MM-DD")
    try:
        date.fromisoformat(value.strip())
    except ValueError as exc:
        raise invalid(f"{name} is not a valid date") from exc
    return value.strip()


def _record_id(inputs: Mapping[str, Any], name: str, *, required: bool = False) -> int | None:
    value = inputs.get(name)
    if value in (None, ""):
        if required:
            raise invalid(f"{name} is required")
        return None
    return domain.ids([value], name=name)[0]


def _required_id(inputs: Mapping[str, Any], name: str) -> int:
    value = inputs.get(name)
    if value in (None, ""):
        raise invalid(f"{name} is required")
    return domain.ids([value], name=name)[0]


def decimal_of(record: Any, name: str) -> Decimal | None:
    value = field(record, name)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    return None


def decimal_text(number: Decimal | None, step: Decimal = MONEY) -> str | None:
    return None if number is None else str(number.quantize(step, rounding=ROUND_HALF_UP))


def many2one(record: Any, name: str) -> tuple[int | None, str | None]:
    """Odoo's [id, "Name"] pair (or False) as (id, name)."""
    value = field(record, name)
    if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], int):
        return value[0], str(value[1])
    return None, None


# ---------------------------------------------------------------------------
# Progress


class Progress:
    """The writes a composite step has completed, for an honest failure message."""

    def __init__(self) -> None:
        self.done: list[str] = []

    def add(self, item: str) -> None:
        self.done.append(item)

    def fail(self, exc: ExtensionError, step: str) -> ExtensionError:
        if not self.done:
            return exc
        message = (
            f"{step} failed: {exc.message}. Already done in Odoo: {'; '.join(self.done)}. "
            "Check Odoo before running this step again."
        )
        return ExtensionError(
            exc.code,
            message,
            failure_class="provider",
            retryable=False,
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
            http_status=exc.http_status,
            data_errors=exc.data_errors,
        )

    def guard(self, step: str, run: Callable[[], Any]) -> Any:
        try:
            return run()
        except ExtensionError as exc:
            raise self.fail(exc, step) from exc
        except DeadlineReached as exc:
            if not self.done:
                raise
            raise self.fail(
                ExtensionError(errors.TIMEOUT_UNKNOWN, "the step ran out of time"), step
            ) from exc


# ---------------------------------------------------------------------------
# Shared lookups


def require(context: Any, model: str) -> dict[str, Any]:
    """The model's fields, or ``module_not_installed`` when its module is missing."""
    try:
        return context.fields(model)
    except ExtensionError as exc:
        if exc.code != errors.NOT_FOUND:
            raise
        module = MODULE_OF.get(model, model.split(".", 1)[0])
        raise ExtensionError(
            errors.MODULE_NOT_INSTALLED,
            f"The Odoo app '{module}' is not installed in this database ({model} is missing)",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        ) from exc


def search_read(
    context: Any,
    model: str,
    query: list[Any],
    fields: list[str],
    *,
    limit: int = PAGE,
    offset: int = 0,
    order: str | None = None,
) -> list[dict[str, Any]]:
    rows = context.call(
        model,
        "search_read",
        kwargs={
            "domain": query,
            "fields": fields,
            "limit": limit,
            "offset": offset,
            "order": order,
        },
    )
    if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
        raise ExtensionError(
            errors.INVALID_RESPONSE,
            f"Odoo's answer for {model} is not a list of records",
            definitely_no_external_effect=True,
            external_effect_status="failed",
        )
    return [dict(r) for r in rows]


def _create_one(
    context: Any, model: str, values: dict[str, Any], *, odoo_context: dict[str, Any] | None = None
) -> int:
    kwargs: dict[str, Any] = {"vals_list": [values]}
    if odoo_context:
        kwargs["context"] = odoo_context
    answer = context.call(model, "create", kwargs=kwargs, mutating=True)
    new_id = answer[0] if isinstance(answer, list) and answer else answer
    if isinstance(new_id, bool) or not isinstance(new_id, int):
        raise ExtensionError(
            errors.TIMEOUT_UNKNOWN,
            f"Odoo answered the create on {model} without a record id",
            definitely_no_external_effect=False,
            external_effect_status="timeout_unknown",
        )
    return new_id


def _id_by(context: Any, model: str, query: list[Any], what: str) -> int:
    rows = search_read(context, model, query, ["id"], limit=2)
    if not rows:
        raise invalid(f"Odoo has no {what}")
    if len(rows) > 1:
        raise invalid(f"Several Odoo records match {what}; give its id")
    return int(rows[0]["id"])


PARTNER_LOOKUP = (("ref", "ref"), ("vat", "vat"), ("email", "email"))
PARTNER_INPUTS = {
    "ref",
    "vat",
    "email",
    "name",
    "is_company",
    "phone",
    "street",
    "city",
    "zip",
    "country_code",
}


def _partner_identifiers(inputs: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str, str]]:
    found = []
    for name, odoo_field in PARTNER_LOOKUP:
        value = text(prefix + name, inputs.get(prefix + name), limit=120)
        if value:
            found.append((prefix + name, odoo_field, value))
    return found


def _partner_values(
    context: Any | None, inputs: Mapping[str, Any], prefix: str = ""
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": text(prefix + "name", inputs.get(prefix + "name"), limit=200, required=True),
        "is_company": flag(prefix + "is_company", inputs.get(prefix + "is_company"), True),
    }
    for name in ("ref", "vat", "email", "phone", "street", "city", "zip"):
        value = text(prefix + name, inputs.get(prefix + name), limit=200)
        if value:
            values[name] = value
    country = text(prefix + "country_code", inputs.get(prefix + "country_code"), limit=2)
    if country:
        if not _COUNTRY_RE.fullmatch(country):
            raise invalid(f"{prefix}country_code must be a two-letter code such as LT")
        if context is not None:
            values["country_id"] = _id_by(
                context,
                "res.country",
                [["code", "=", country.upper()]],
                f"country {country.upper()}",
            )
    return values


def find_or_create_partner(
    context: Any, inputs: Mapping[str, Any], progress: Progress, *, prefix: str = ""
) -> dict[str, Any]:
    identifiers = _partner_identifiers(inputs, prefix)
    if not identifiers:
        raise invalid(f"Give {prefix}ref, {prefix}vat or {prefix}email to look the partner up")
    for name, odoo_field, value in identifiers:
        rows = search_read(
            context,
            "res.partner",
            [[odoo_field, "=", value]],
            ["id", "name", "ref", "vat", "email"],
            limit=2,
        )
        if len(rows) > 1:
            raise invalid(f"Several Odoo partners have {name} {value}; give the partner id")
        if rows:
            record = rows[0]
            for other, other_field, other_value in identifiers:
                found = field(record, other_field)
                if (
                    other != name
                    and isinstance(found, str)
                    and found
                    and found.strip().casefold() != other_value.casefold()
                ):
                    raise invalid(
                        f"The partner found by {name} has {other} {found}, not {other_value}; "
                        "nothing was created"
                    )
            return {
                "partner_id": int(record["id"]),
                "created": False,
                "matched_by": name,
                "record": record,
            }
    values = _partner_values(context, inputs, prefix)
    new_id = progress.guard(
        "Creating the partner", lambda: _create_one(context, "res.partner", values)
    )
    progress.add(f"partner {values['name']} (id {new_id}) created")
    return {
        "partner_id": new_id,
        "created": True,
        "matched_by": None,
        "record": {"id": new_id, **values},
    }


def resolve_partner(
    context: Any, inputs: Mapping[str, Any], progress: Progress
) -> tuple[int, bool]:
    partner_id = _record_id(inputs, "partner_id")
    if partner_id is not None:
        return partner_id, False
    found = find_or_create_partner(context, inputs, progress, prefix="partner_")
    return int(found["partner_id"]), bool(found["created"])


def resolve_product(context: Any, line: Mapping[str, Any], where: str) -> int:
    product_id = line.get("product_id")
    if product_id not in (None, ""):
        return domain.ids([product_id], name=f"{where}.product_id")[0]
    code = text(f"{where}.product_code", line.get("product_code"), limit=120, required=True)
    return _id_by(context, "product.product", [["default_code", "=", code]], f"product {code}")


# ---------------------------------------------------------------------------
# Partners and products


def partner_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    _allowed(inputs, PARTNER_INPUTS)
    return find_or_create_partner(context, inputs, progress)


PRODUCT_INPUTS = {"product_code", "barcode", "name", "kind", "storable", "list_price"}


def _product_values(version: int, inputs: Mapping[str, Any]) -> dict[str, Any]:
    kind = text("kind", inputs.get("kind"), limit=10) or "goods"
    if kind not in {"goods", "service"}:
        raise invalid("kind must be goods or service")
    storable = flag("storable", inputs.get("storable"), False)
    if storable and kind == "service":
        raise invalid("A service cannot be stocked")
    values: dict[str, Any] = {
        "name": text("name", inputs.get("name"), limit=200, required=True),
        **versions.product_type_values(version, service=kind == "service", storable=storable),
    }
    for name, odoo_field in (("product_code", "default_code"), ("barcode", "barcode")):
        value = text(name, inputs.get(name), limit=120)
        if value:
            values[odoo_field] = value
    if inputs.get("list_price") is not None:
        values["list_price"] = wire(
            decimal_input(
                inputs["list_price"], "list_price", PRICE, minimum=Decimal(0), maximum=MAX_QUANTITY
            )
        )
    return values


def product_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    _allowed(inputs, PRODUCT_INPUTS)
    lookups = [
        (name, odoo_field, value)
        for name, odoo_field in (("product_code", "default_code"), ("barcode", "barcode"))
        if (value := text(name, inputs.get(name), limit=120))
    ]
    if not lookups:
        raise invalid("Give product_code or barcode")
    for name, odoo_field, value in lookups:
        rows = search_read(
            context,
            "product.product",
            [[odoo_field, "=", value]],
            ["id", "display_name", "default_code", "barcode"],
            limit=2,
        )
        if len(rows) > 1:
            raise invalid(f"Several Odoo products have {name} {value}")
        if rows:
            record = rows[0]
            for other, other_field, other_value in lookups:
                found = field(record, other_field)
                if other != name and isinstance(found, str) and found and found != other_value:
                    raise invalid(
                        f"The product found by {name} has {other} {found}, not {other_value}"
                    )
            return {
                "product_id": int(record["id"]),
                "created": False,
                "matched_by": name,
                "record": record,
            }
    if flag("storable", inputs.get("storable"), False):
        require(context, "stock.quant")
    values = _product_values(context.version(), inputs)
    new_id = progress.guard(
        "Creating the product", lambda: _create_one(context, "product.product", values)
    )
    progress.add(f"product {values['name']} (id {new_id}) created")
    return {
        "product_id": new_id,
        "created": True,
        "matched_by": None,
        "record": {"id": new_id, **values},
    }


# ---------------------------------------------------------------------------
# Invoices and bills

INVOICE_INPUTS = {
    "partner_id",
    "partner_ref",
    "partner_vat",
    "partner_email",
    "partner_name",
    "reference",
    "invoice_date",
    "due_date",
    "currency_code",
    "notes",
    "lines",
    "post",
    "document_file",
}
_LINE_KEYS = {
    "product_id",
    "product_code",
    "description",
    "quantity",
    "price_unit",
    "discount",
    "taxes",
}


def invoice_lines(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = inputs.get("lines")
    if not isinstance(raw, list) or not raw:
        raise invalid("lines must be a list with at least one line")
    if len(raw) > MAX_LINES:
        raise invalid(f"A step takes at most {MAX_LINES} lines")
    lines = []
    for index, item in enumerate(raw):
        where = f"lines[{index}]"
        if not isinstance(item, Mapping) or set(item) - _LINE_KEYS:
            raise invalid(f"{where} may have only: {', '.join(sorted(_LINE_KEYS))}")
        if item.get("product_id") in (None, "") and not item.get("product_code"):
            if not item.get("description"):
                raise invalid(f"{where} needs product_id, product_code or description")
        line: dict[str, Any] = {
            "quantity": wire(
                decimal_input(
                    item.get("quantity", 1),
                    f"{where}.quantity",
                    QUANTITY,
                    minimum=Decimal(0),
                    maximum=MAX_QUANTITY,
                )
            )
        }
        if item.get("price_unit") is not None:
            line["price_unit"] = wire(
                decimal_input(
                    item["price_unit"], f"{where}.price_unit", PRICE, maximum=MAX_QUANTITY
                )
            )
        if item.get("discount") is not None:
            line["discount"] = wire(
                decimal_input(
                    item["discount"],
                    f"{where}.discount",
                    MONEY,
                    minimum=Decimal(0),
                    maximum=Decimal(100),
                )
            )
        description = text(f"{where}.description", item.get("description"), limit=1000)
        if description:
            line["name"] = description
        taxes = item.get("taxes")
        if taxes is not None:
            if not isinstance(taxes, list) or len(taxes) > 10:
                raise invalid(f"{where}.taxes must be a list of tax names")
            line["_taxes"] = [text(f"{where}.taxes", t, limit=120, required=True) for t in taxes]
        line["_product"] = {
            k: item[k] for k in ("product_id", "product_code") if item.get(k) not in (None, "")
        }
        lines.append(line)
    return lines


def _tax_ids(context: Any, names: list[str], use: str, cache: dict[str, int]) -> list[int]:
    ids = []
    for name in names:
        if name not in cache:
            cache[name] = _id_by(
                context,
                "account.tax",
                [["name", "=", name], ["type_tax_use", "=", use]],
                f"{use} tax {name}",
            )
        ids.append(cache[name])
    return ids


def _move_result(
    record: Mapping[str, Any],
    *,
    created: bool,
    partner_created: bool,
    attachment_id: int | None,
    posted: bool | None,
) -> dict[str, Any]:
    return {
        "invoice_id": record.get("id"),
        "number": record.get("name") if isinstance(record.get("name"), str) else None,
        "state": record.get("state") if isinstance(record.get("state"), str) else None,
        "created": created,
        "partner_id": many2one(record, "partner_id")[0],
        "partner_created": partner_created,
        "total_untaxed": decimal_text(decimal_of(record, "amount_untaxed")),
        "total_tax": decimal_text(decimal_of(record, "amount_tax")),
        "total": decimal_text(decimal_of(record, "amount_total")),
        "amount_due": decimal_text(decimal_of(record, "amount_residual")),
        "attachment_id": attachment_id,
        "posted": posted,
        "record": dict(record),
    }


MOVE_FIELDS = [
    "id",
    "name",
    "state",
    "ref",
    "partner_id",
    "invoice_date",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "amount_residual",
    "currency_id",
]


def _same_document(
    context: Any,
    model: str,
    query: list[Any],
    fields: list[str],
    date_field: str,
    wanted: str,
    what: str,
    progress: Progress,
) -> dict[str, Any] | None:
    """The document a previous run created: same reference, partner and date, exactly one."""
    rows = progress.guard(
        f"Looking up the {what}", lambda: search_read(context, model, query, fields, limit=20)
    )
    if not rows:
        return None
    same_day = [r for r in rows if str(field(r, date_field) or "")[:10] == wanted]
    if len(same_day) == 1:
        return same_day[0]
    if len(same_day) > 1:
        ids = ", ".join(str(r.get("id")) for r in same_day[:5])
        raise progress.fail(
            invalid(
                f"{len(same_day)} {what}s match the reference and date ({ids}); nothing was created"
            ),
            f"Looking up the {what}",
        )
    other = rows[0]
    raise progress.fail(
        invalid(
            f"A {what} with this reference already exists for the partner (id {other.get('id')}, "
            f"date {str(field(other, date_field) or 'unknown')[:10]}); nothing was created"
        ),
        f"Looking up the {what}",
    )


def _post_move(context: Any, move_id: int) -> None:
    # action_post may run e-invoicing or other outside services: risky.
    context.call("account.move", "action_post", ids=[move_id], mutating=True, risky=True)


def invoice_op(
    context: Any, operation_id: str, inputs: Mapping[str, Any], progress: Progress
) -> dict[str, Any]:
    _allowed(inputs, INVOICE_INPUTS)
    move_type = MOVE_TYPES[operation_id]
    what = "customer invoice" if move_type == "out_invoice" else "vendor bill"
    lines = invoice_lines(inputs)
    invoice_date = _date(inputs, "invoice_date") or date.today().isoformat()
    due_date = _date(inputs, "due_date")
    reference = text("reference", inputs.get("reference"), limit=200)
    post = flag("post", inputs.get("post"), False)
    handle = text("document_file", inputs.get("document_file"), limit=512)
    file_body: tuple[bytes, str] | None = None
    if handle:
        from .operations import read_input_file

        file_body = read_input_file(context, handle)
    require(context, "account.move")
    partner_id, partner_created = resolve_partner(context, inputs, progress)
    if reference:
        found = _same_document(
            context,
            "account.move",
            [
                ["move_type", "=", move_type],
                ["partner_id", "=", partner_id],
                ["ref", "=", reference],
                ["state", "!=", "cancel"],
            ],
            MOVE_FIELDS,
            "invoice_date",
            invoice_date,
            what,
            progress,
        )
        if found is not None:
            posted: bool | None = found.get("state") == "posted"
            if post and found.get("state") == "draft":
                move_id = int(found["id"])
                progress.guard(f"Posting the existing {what}", lambda: _post_move(context, move_id))
                found = _read_move(context, move_id) or found
                posted = True
            return _move_result(
                found,
                created=False,
                partner_created=partner_created,
                attachment_id=None,
                posted=posted,
            )
    use = "sale" if move_type == "out_invoice" else "purchase"
    cache: dict[str, int] = {}
    commands = []
    for index, line in enumerate(lines):
        values = {k: v for k, v in line.items() if not k.startswith("_")}
        if line["_product"]:
            values["product_id"] = resolve_product(context, line["_product"], f"lines[{index}]")
        if "_taxes" in line:
            values["tax_ids"] = [[6, 0, _tax_ids(context, line["_taxes"], use, cache)]]
        commands.append([0, 0, values])
    header: dict[str, Any] = {
        "move_type": move_type,
        "partner_id": partner_id,
        "invoice_date": invoice_date,
        "invoice_line_ids": commands,
    }
    if due_date:
        header["invoice_date_due"] = due_date
    if reference:
        header["ref"] = reference
    notes = text("notes", inputs.get("notes"), limit=2000)
    if notes:
        header["narration"] = notes
    currency = text("currency_code", inputs.get("currency_code"), limit=3)
    if currency:
        if not _CURRENCY_RE.fullmatch(currency):
            raise invalid("currency_code must be a three-letter code such as EUR")
        header["currency_id"] = _id_by(
            context,
            "res.currency",
            [["name", "=", currency.upper()]],
            f"currency {currency.upper()}",
        )
    move_id = progress.guard(
        f"Creating the {what}", lambda: _create_one(context, "account.move", header)
    )
    progress.add(f"{what} id {move_id} created as a draft with {len(lines)} lines")
    attachment_id = None
    if file_body is not None:
        attachment_id = progress.guard(
            "Attaching the file",
            lambda: attach(
                context,
                "account.move",
                move_id,
                file_body[0],
                file_body[1] or f"{what}.pdf",
                main=True,
            ),
        )
        progress.add(f"file attached (attachment id {attachment_id})")
    posted = False
    if post:
        progress.guard(f"Posting the {what}", lambda: _post_move(context, move_id))
        posted = True
        progress.add(f"{what} posted")
    record = _read_move(context, move_id) or {"id": move_id}
    return _move_result(
        record,
        created=True,
        partner_created=partner_created,
        attachment_id=attachment_id,
        posted=posted,
    )


def _read_move(context: Any, move_id: int) -> dict[str, Any] | None:
    try:
        rows = context.call("account.move", "read", ids=[move_id], kwargs={"fields": MOVE_FIELDS})
    except (ExtensionError, DeadlineReached):
        return None
    return (
        dict(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else None
    )


# ---------------------------------------------------------------------------
# Attachments


def attach(context: Any, model: str, record_id: int, body: bytes, name: str, *, main: bool) -> int:
    import base64

    content_field = versions.name("attachment_content", context.version())
    values = {
        "name": name[:200] or "file",
        "res_model": model,
        "res_id": record_id,
        content_field: base64.b64encode(body).decode("ascii"),
    }
    attachment_id = _create_one(context, "ir.attachment", values)
    if main and "message_main_attachment_id" in context.fields(model):
        context.call(
            model,
            "write",
            ids=[record_id],
            kwargs={"vals": {"message_main_attachment_id": attachment_id}},
            mutating=True,
        )
    return attachment_id


ATTACH_INPUTS = {"model", "record_id", "document_file", "file_name", "set_main"}


def attach_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    from .operations import model_name, read_input_file

    _allowed(inputs, ATTACH_INPUTS)
    model = model_name(inputs)
    record_id = _required_id(inputs, "record_id")
    body, stored_name = read_input_file(
        context, text("document_file", inputs.get("document_file"), limit=512, required=True)
    )
    name = text("file_name", inputs.get("file_name"), limit=200) or stored_name or "file"
    main = flag("set_main", inputs.get("set_main"), False)
    fields = context.fields(model)
    if main and "message_main_attachment_id" not in fields:
        raise invalid(f"{model} has no main attachment; set set_main to false")
    attachment_id = progress.guard(
        "Attaching the file", lambda: attach(context, model, record_id, body, name, main=main)
    )
    return {
        "attachment_id": attachment_id,
        "model": model,
        "record_id": record_id,
        "main_set": main,
    }


DOWNLOAD_INPUTS = {"attachment_id", "model", "record_id", "file_name"}


def download_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    from . import catalog
    from .operations import model_name, save_attachment_content

    _allowed(inputs, DOWNLOAD_INPUTS)
    content_field = versions.name("attachment_content", context.version())
    fields = ["id", "name", "mimetype", "file_size", content_field]
    attachment_id = _record_id(inputs, "attachment_id")
    if attachment_id is not None:
        rows = context.call("ir.attachment", "read", ids=[attachment_id], kwargs={"fields": fields})
    else:
        model = model_name(inputs)
        record_id = _record_id(inputs, "record_id", required=True)
        query: list[Any] = [["res_model", "=", model], ["res_id", "=", record_id]]
        name = text("file_name", inputs.get("file_name"), limit=200)
        if name:
            query.append(["name", "=", name])
        rows = search_read(context, "ir.attachment", query, fields, limit=1, order="id desc")
    if not isinstance(rows, list) or not rows:
        return {
            "found": False,
            "document_file": None,
            "attachment_id": None,
            "file_name": None,
            "mimetype": None,
            "size_bytes": None,
        }
    row = rows[0]
    handle, size = save_attachment_content(context, row, binding_key=catalog.DOWNLOAD_BINDING)
    return {
        "found": True,
        "document_file": handle,
        "attachment_id": row.get("id"),
        "file_name": row.get("name") if isinstance(row.get("name"), str) else None,
        "mimetype": row.get("mimetype") if isinstance(row.get("mimetype"), str) else None,
        "size_bytes": size,
    }


# ---------------------------------------------------------------------------
# Payments

PAYMENT_INPUTS = {"invoice_id", "amount", "payment_date", "journal_code", "memo"}


def payment_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    _allowed(inputs, PAYMENT_INPUTS)
    invoice_id = _required_id(inputs, "invoice_id")
    amount = (
        decimal_input(inputs["amount"], "amount", MONEY, minimum=MONEY, maximum=MAX_AMOUNT)
        if inputs.get("amount") is not None
        else None
    )
    payment_date = _date(inputs, "payment_date") or date.today().isoformat()
    memo = text("memo", inputs.get("memo"), limit=200)
    journal_code = text("journal_code", inputs.get("journal_code"), limit=20)
    require(context, "account.payment.register")
    rows = context.call(
        "account.move",
        "read",
        ids=[invoice_id],
        kwargs={"fields": ["state", "payment_state", "amount_residual", "partner_id", "move_type"]},
    )
    if not isinstance(rows, list) or not rows:
        raise invalid(f"Odoo has no invoice or bill with id {invoice_id}")
    invoice = rows[0]
    if invoice.get("state") != "posted":
        raise invalid("Only a posted invoice or bill can be paid; post it first")
    residual = decimal_of(invoice, "amount_residual") or Decimal(0)
    if residual <= 0 or invoice.get("payment_state") in {"paid", "in_payment", "reversed"}:
        return {
            "invoice_id": invoice_id,
            "created": False,
            "payment_state": invoice.get("payment_state"),
            "amount_due": decimal_text(residual),
        }
    pay = amount if amount is not None else residual
    if memo:
        memo_field = versions.name("payment_memo", context.version())
        partner_id = many2one(invoice, "partner_id")[0]
        same = search_read(
            context,
            "account.payment",
            [
                ["partner_id", "=", partner_id],
                [memo_field, "=", memo],
                ["date", "=", payment_date],
                ["state", "!=", "cancel"],
            ],
            ["id", "amount"],
            limit=5,
        )
        if any(decimal_of(r, "amount") == pay for r in same):
            return {
                "invoice_id": invoice_id,
                "created": False,
                "payment_state": invoice.get("payment_state"),
                "amount_due": decimal_text(residual),
            }
    values: dict[str, Any] = {"amount": wire(pay), "payment_date": payment_date}
    if memo:
        values["communication"] = memo
    if journal_code:
        values["journal_id"] = _id_by(
            context,
            "account.journal",
            [["code", "=", journal_code], ["type", "in", ["bank", "cash"]]],
            f"bank or cash journal {journal_code}",
        )
    wizard_context = {"active_model": "account.move", "active_ids": [invoice_id]}
    wizard = _create_one(context, "account.payment.register", values, odoo_context=wizard_context)
    # action_create_payments posts and reconciles; it may reach outside services.
    context.call(
        "account.payment.register",
        "action_create_payments",
        ids=[wizard],
        kwargs={"context": wizard_context},
        mutating=True,
        risky=True,
    )
    progress.add(f"payment of {decimal_text(pay)} registered on invoice {invoice_id}")
    after = context.call(
        "account.move",
        "read",
        ids=[invoice_id],
        kwargs={"fields": ["payment_state", "amount_residual"]},
    )
    state = after[0] if isinstance(after, list) and after else {}
    return {
        "invoice_id": invoice_id,
        "created": True,
        "payment_state": state.get("payment_state"),
        "amount_due": decimal_text(decimal_of(state, "amount_residual")),
    }


# ---------------------------------------------------------------------------
# Sale orders

ORDER_INPUTS = {
    "partner_id",
    "partner_ref",
    "partner_vat",
    "partner_email",
    "partner_name",
    "reference",
    "order_date",
    "lines",
    "confirm",
}
_ORDER_LINE_KEYS = {
    "product_id",
    "product_code",
    "quantity",
    "price_unit",
    "discount",
    "description",
    "unit",
}
ORDER_FIELDS = [
    "id",
    "name",
    "state",
    "client_order_ref",
    "partner_id",
    "date_order",
    "amount_untaxed",
    "amount_tax",
    "amount_total",
    "currency_id",
]


def order_op(context: Any, inputs: Mapping[str, Any], progress: Progress) -> dict[str, Any]:
    _allowed(inputs, ORDER_INPUTS)
    raw = inputs.get("lines")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LINES:
        raise invalid(f"lines must be a list of 1 to {MAX_LINES} lines")
    order_date = _date(inputs, "order_date") or date.today().isoformat()
    reference = text("reference", inputs.get("reference"), limit=200)
    confirm = flag("confirm", inputs.get("confirm"), False)
    parsed = []
    for index, item in enumerate(raw):
        where = f"lines[{index}]"
        if not isinstance(item, Mapping) or set(item) - _ORDER_LINE_KEYS:
            raise invalid(f"{where} may have only: {', '.join(sorted(_ORDER_LINE_KEYS))}")
        if item.get("product_id") in (None, "") and not item.get("product_code"):
            raise invalid(f"{where} needs product_id or product_code")
        values: dict[str, Any] = {
            "product_uom_qty": wire(
                decimal_input(
                    item.get("quantity", 1),
                    f"{where}.quantity",
                    QUANTITY,
                    minimum=QUANTITY,
                    maximum=MAX_QUANTITY,
                )
            )
        }
        if item.get("price_unit") is not None:
            values["price_unit"] = wire(
                decimal_input(
                    item["price_unit"], f"{where}.price_unit", PRICE, maximum=MAX_QUANTITY
                )
            )
        if item.get("discount") is not None:
            values["discount"] = wire(
                decimal_input(
                    item["discount"],
                    f"{where}.discount",
                    MONEY,
                    minimum=Decimal(0),
                    maximum=Decimal(100),
                )
            )
        description = text(f"{where}.description", item.get("description"), limit=1000)
        if description:
            values["name"] = description
        parsed.append((values, item, text(f"{where}.unit", item.get("unit"), limit=60)))
    require(context, "sale.order")
    partner_id, partner_created = resolve_partner(context, inputs, progress)
    if reference:
        found = _same_document(
            context,
            "sale.order",
            [
                ["partner_id", "=", partner_id],
                ["client_order_ref", "=", reference],
                ["state", "!=", "cancel"],
            ],
            ORDER_FIELDS,
            "date_order",
            order_date,
            "sale order",
            progress,
        )
        if found is not None:
            confirmed = found.get("state") == "sale"
            if confirm and found.get("state") in {"draft", "sent"}:
                existing_id = int(found["id"])
                progress.guard(
                    "Confirming the existing sale order", lambda: _confirm(context, existing_id)
                )
                confirmed = True
                found = _read_order(context, int(found["id"])) or found
            return _order_result(
                found, created=False, partner_created=partner_created, confirmed=confirmed
            )
    uom_field = versions.name("sale_line_uom", context.version())
    commands = []
    for index, (values, item, unit) in enumerate(parsed):
        values = dict(values)
        values["product_id"] = resolve_product(context, item, f"lines[{index}]")
        if unit:
            values[uom_field] = _id_by(context, "uom.uom", [["name", "=", unit]], f"unit {unit}")
        commands.append([0, 0, values])
    header: dict[str, Any] = {
        "partner_id": partner_id,
        "order_line": commands,
        "date_order": f"{order_date} 00:00:00",
    }
    if reference:
        header["client_order_ref"] = reference
    order_id = progress.guard(
        "Creating the sale order", lambda: _create_one(context, "sale.order", header)
    )
    progress.add(f"sale order id {order_id} created with {len(commands)} lines")
    confirmed = False
    if confirm:
        progress.guard("Confirming the sale order", lambda: _confirm(context, order_id))
        confirmed = True
        progress.add("sale order confirmed")
    record = _read_order(context, order_id) or {"id": order_id}
    return _order_result(record, created=True, partner_created=partner_created, confirmed=confirmed)


def _confirm(context: Any, order_id: int) -> None:
    context.call("sale.order", "action_confirm", ids=[order_id], mutating=True, risky=True)


def _read_order(context: Any, order_id: int) -> dict[str, Any] | None:
    try:
        rows = context.call("sale.order", "read", ids=[order_id], kwargs={"fields": ORDER_FIELDS})
    except (ExtensionError, DeadlineReached):
        return None
    return (
        dict(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else None
    )


def _order_result(
    record: Mapping[str, Any], *, created: bool, partner_created: bool, confirmed: bool
) -> dict[str, Any]:
    return {
        "order_id": record.get("id"),
        "number": record.get("name") if isinstance(record.get("name"), str) else None,
        "state": record.get("state") if isinstance(record.get("state"), str) else None,
        "created": created,
        "partner_id": many2one(record, "partner_id")[0],
        "partner_created": partner_created,
        "total": decimal_text(decimal_of(record, "amount_total")),
        "confirmed": confirmed,
        "record": dict(record),
    }


# ---------------------------------------------------------------------------
# Reads


def _paged(inputs: Mapping[str, Any]) -> tuple[int, int]:
    limit = whole("limit", inputs.get("limit"), default=PAGE, maximum=200)
    offset = whole("offset", inputs.get("offset"), default=0, minimum=0, maximum=10**9)
    return limit, offset


def _product_filter(context: Any, inputs: Mapping[str, Any]) -> int | None:
    product_id = _record_id(inputs, "product_id")
    if product_id is not None:
        return product_id
    code = text("product_code", inputs.get("product_code"), limit=120)
    if code:
        return _id_by(context, "product.product", [["default_code", "=", code]], f"product {code}")
    return None


def stock_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    _allowed(inputs, {"product_id", "product_code", "location_id", "limit", "offset"})
    limit, offset = _paged(inputs)
    require(context, "stock.quant")
    query: list[Any] = [["location_id.usage", "=", "internal"]]
    product_id = _product_filter(context, inputs)
    if product_id is not None:
        query.append(["product_id", "=", product_id])
    location_id = _record_id(inputs, "location_id")
    if location_id is not None:
        query.append(["location_id", "child_of", location_id])
    uom_field = versions.name("quant_uom", context.version())
    rows = search_read(
        context,
        "stock.quant",
        query,
        [
            "product_id",
            "location_id",
            "lot_id",
            "quantity",
            "reserved_quantity",
            "available_quantity",
            uom_field,
        ],
        limit=limit,
        offset=offset,
        order="product_id, location_id, id",
    )
    items = []
    totals: dict[tuple[int | None, int | None], dict[str, Any]] = {}
    for row in rows:
        product, product_name = many2one(row, "product_id")
        uom, uom_name = many2one(row, uom_field)
        available = decimal_of(row, "available_quantity") or Decimal(0)
        key = (product, uom)
        group = totals.setdefault(
            key,
            {
                "product_id": product,
                "product": product_name,
                "unit": uom_name,
                "available": Decimal(0),
                "on_hand": Decimal(0),
            },
        )
        group["available"] += available
        group["on_hand"] += decimal_of(row, "quantity") or Decimal(0)
        items.append(
            {
                "product_id": product,
                "product": product_name,
                "location": many2one(row, "location_id")[1],
                "lot": many2one(row, "lot_id")[1],
                "unit": uom_name,
                "on_hand": decimal_text(decimal_of(row, "quantity"), QUANTITY),
                "reserved": decimal_text(decimal_of(row, "reserved_quantity"), QUANTITY),
                "available": decimal_text(available, QUANTITY),
            }
        )
    has_more = len(rows) >= limit
    return {
        "items": items,
        "count": len(items),
        "totals": [
            {
                **g,
                "available": decimal_text(g["available"], QUANTITY),
                "on_hand": decimal_text(g["on_hand"], QUANTITY),
            }
            for g in totals.values()
        ],
        "has_more": has_more,
        "next_offset": offset + len(rows) if has_more else None,
    }


def prices_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    _allowed(inputs, {"product_id", "product_code", "pricelist_id"})
    product_id = _product_filter(context, inputs)
    if product_id is None:
        raise invalid("Give product_id or product_code")
    rows = context.call(
        "product.product",
        "read",
        ids=[product_id],
        kwargs={
            "fields": [
                "id",
                "display_name",
                "lst_price",
                "list_price",
                "standard_price",
                "currency_id",
                "uom_id",
                "product_tmpl_id",
            ]
        },
    )
    if not isinstance(rows, list) or not rows:
        raise invalid(f"Odoo has no product with id {product_id}")
    product = rows[0]
    template_id = many2one(product, "product_tmpl_id")[0]
    query: list[Any] = ["|", ["product_id", "=", product_id], ["product_tmpl_id", "=", template_id]]
    pricelist_id = _record_id(inputs, "pricelist_id")
    if pricelist_id is not None:
        query.append(["pricelist_id", "=", pricelist_id])
    percent_field = versions.name("pricelist_percent", context.version())
    rule_fields = [
        "pricelist_id",
        "applied_on",
        "compute_price",
        "fixed_price",
        "price_discount",
        "min_quantity",
        "date_start",
        "date_end",
    ]
    if percent_field:
        rule_fields.append(percent_field)
    rules = search_read(context, "product.pricelist.item", query, rule_fields, limit=200)
    return {
        "product_id": product_id,
        "product": product.get("display_name"),
        "sales_price": decimal_text(decimal_of(product, "lst_price"), PRICE),
        "list_price": decimal_text(decimal_of(product, "list_price"), PRICE),
        "cost": decimal_text(decimal_of(product, "standard_price"), PRICE),
        "currency": many2one(product, "currency_id")[1],
        "unit": many2one(product, "uom_id")[1],
        "pricelist_rules": [
            {
                "pricelist": many2one(r, "pricelist_id")[1],
                "applied_on": r.get("applied_on"),
                "compute_price": r.get("compute_price"),
                "fixed_price": decimal_text(decimal_of(r, "fixed_price"), PRICE),
                "percent_price": decimal_text(decimal_of(r, percent_field), MONEY)
                if percent_field
                else None,
                "price_discount": decimal_text(decimal_of(r, "price_discount"), MONEY),
                "min_quantity": decimal_text(decimal_of(r, "min_quantity"), QUANTITY),
                "date_start": r.get("date_start") or None,
                "date_end": r.get("date_end") or None,
            }
            for r in rules
        ],
    }


def changes_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    _allowed(inputs, {"entity", "since", "fields", "limit", "offset"})
    entity = text("entity", inputs.get("entity"), limit=40, required=True)
    model = CHANGE_MODELS.get(entity)
    if model is None:
        raise invalid(f"entity must be one of: {', '.join(sorted(CHANGE_MODELS))}")
    since = text("since", inputs.get("since"), limit=19, required=True)
    if not _SINCE_RE.fullmatch(since):
        raise invalid("since must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS (UTC)")
    try:
        date.fromisoformat(since[:10])
    except ValueError as exc:
        raise invalid("since is not a valid date") from exc
    known = require(context, model)
    fields = domain.field_list(inputs.get("fields"), known) or ["id", "display_name", "write_date"]
    if "write_date" not in fields:
        fields.append("write_date")
    limit, offset = _paged(inputs)
    rows = search_read(
        context,
        model,
        [["write_date", ">", since if " " in since else f"{since} 00:00:00"]],
        fields,
        limit=limit,
        offset=offset,
        order="write_date asc, id asc",
    )
    has_more = len(rows) >= limit
    return {
        "model": model,
        "records": rows,
        "count": len(rows),
        "has_more": has_more,
        "next_offset": offset + len(rows) if has_more else None,
    }


BALANCE_INPUTS = {"partner_id", "partner_ref", "partner_vat", "partner_email", "limit", "offset"}


def balance_op(context: Any, inputs: Mapping[str, Any]) -> dict[str, Any]:
    _allowed(inputs, BALANCE_INPUTS)
    limit, offset = _paged(inputs)
    require(context, "account.move")
    partner_id = _record_id(inputs, "partner_id")
    if partner_id is None:
        identifiers = _partner_identifiers(inputs, "partner_")
        if not identifiers:
            raise invalid("Give partner_id, partner_ref, partner_vat or partner_email")
        _name, odoo_field, value = identifiers[0]
        partner_id = _id_by(
            context, "res.partner", [[odoo_field, "=", value]], f"partner with {odoo_field} {value}"
        )
    partner = context.call(
        "res.partner",
        "read",
        ids=[partner_id],
        kwargs={"fields": ["id", "display_name", "credit", "debit"]},
    )
    record = partner[0] if isinstance(partner, list) and partner else {}
    rows = search_read(
        context,
        "account.move",
        [
            ["commercial_partner_id", "=", partner_id],
            ["state", "=", "posted"],
            ["payment_state", "in", ["not_paid", "partial"]],
            ["move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]],
        ],
        [
            "id",
            "name",
            "move_type",
            "invoice_date",
            "invoice_date_due",
            "amount_total",
            "amount_residual",
            "amount_residual_signed",
            "currency_id",
        ],
        limit=limit,
        offset=offset,
        order="invoice_date_due asc, id asc",
    )
    today = date.today().isoformat()
    receivable = payable = overdue = Decimal(0)
    documents = []
    for row in rows:
        signed = decimal_of(row, "amount_residual_signed") or Decimal(0)
        if str(row.get("move_type", "")).startswith("out_"):
            receivable += signed
        else:
            payable += signed
        due = row.get("invoice_date_due") if isinstance(row.get("invoice_date_due"), str) else None
        if due and due < today:
            overdue += signed
        documents.append(
            {
                "id": row.get("id"),
                "number": row.get("name"),
                "type": row.get("move_type"),
                "date": row.get("invoice_date") or None,
                "due_date": due,
                "total": decimal_text(decimal_of(row, "amount_total")),
                "amount_due": decimal_text(decimal_of(row, "amount_residual")),
                "amount_due_company_currency": decimal_text(signed),
                "currency": many2one(row, "currency_id")[1],
            }
        )
    has_more = len(rows) >= limit
    return {
        "partner_id": partner_id,
        "partner": record.get("display_name") if isinstance(record, Mapping) else None,
        "receivable": decimal_text(decimal_of(record, "credit")),
        "payable": decimal_text(decimal_of(record, "debit")),
        "open_receivable": decimal_text(receivable),
        "open_payable": decimal_text(payable),
        "overdue": decimal_text(overdue),
        "documents": documents,
        "count": len(documents),
        "complete": not has_more,
        "next_offset": offset + len(rows) if has_more else None,
    }


# ---------------------------------------------------------------------------
# Dispatch, validation, test-mode results


def run(context: Any, operation_id: str) -> dict[str, Any]:
    inputs = {k: v for k, v in context.operation_input.items() if k != "connection_ref"}
    progress = Progress()
    if operation_id == "find_or_create_partner":
        return partner_op(context, inputs, progress)
    if operation_id == "find_or_create_product":
        return product_op(context, inputs, progress)
    if operation_id in MOVE_TYPES:
        return invoice_op(context, operation_id, inputs, progress)
    if operation_id == "register_payment":
        return payment_op(context, inputs, progress)
    if operation_id == "create_sale_order":
        return order_op(context, inputs, progress)
    if operation_id == "attach_file":
        return attach_op(context, inputs, progress)
    if operation_id == "download_attachment":
        return download_op(context, inputs)
    if operation_id == "stock_levels":
        return stock_op(context, inputs)
    if operation_id == "product_prices":
        return prices_op(context, inputs)
    if operation_id == "changes_since":
        return changes_op(context, inputs)
    return balance_op(context, inputs)


def validate(operation_id: str, inputs: Mapping[str, Any]) -> None:
    """Check an everyday write's inputs without calling Odoo."""
    if operation_id == "find_or_create_partner":
        _allowed(inputs, PARTNER_INPUTS)
        if not _partner_identifiers(inputs):
            raise invalid("Give ref, vat or email to look the partner up")
    elif operation_id == "find_or_create_product":
        _allowed(inputs, PRODUCT_INPUTS)
        _product_values(18, {**inputs, "name": inputs.get("name") or "x"})
    elif operation_id in MOVE_TYPES:
        _allowed(inputs, INVOICE_INPUTS)
        invoice_lines(inputs)
        _date(inputs, "invoice_date")
        _date(inputs, "due_date")
        if _record_id(inputs, "partner_id") is None and not _partner_identifiers(
            inputs, "partner_"
        ):
            raise invalid("Give partner_id, or partner_ref / partner_vat / partner_email")
    elif operation_id == "register_payment":
        _allowed(inputs, PAYMENT_INPUTS)
        _record_id(inputs, "invoice_id", required=True)
        if inputs.get("amount") is not None:
            decimal_input(inputs["amount"], "amount", MONEY, minimum=MONEY, maximum=MAX_AMOUNT)
    elif operation_id == "create_sale_order":
        _allowed(inputs, ORDER_INPUTS)
        if not isinstance(inputs.get("lines"), list) or not inputs["lines"]:
            raise invalid("lines must be a non-empty list")
    elif operation_id == "attach_file":
        _allowed(inputs, ATTACH_INPUTS)
        _record_id(inputs, "record_id", required=True)
        text("document_file", inputs.get("document_file"), limit=512, required=True)


#: Every declared output of each everyday write, as a test run returns it.
NEUTRAL_OUTPUTS: dict[str, dict[str, Any]] = {
    "find_or_create_partner": {
        "partner_id": None,
        "created": False,
        "matched_by": None,
        "record": None,
    },
    "find_or_create_product": {
        "product_id": None,
        "created": False,
        "matched_by": None,
        "record": None,
    },
    "create_customer_invoice": {
        "invoice_id": None,
        "number": None,
        "state": None,
        "created": False,
        "partner_id": None,
        "partner_created": False,
        "total_untaxed": None,
        "total_tax": None,
        "total": None,
        "amount_due": None,
        "attachment_id": None,
        "posted": False,
        "record": None,
    },
    "register_payment": {
        "invoice_id": None,
        "created": False,
        "payment_state": None,
        "amount_due": None,
    },
    "create_sale_order": {
        "order_id": None,
        "number": None,
        "state": None,
        "created": False,
        "partner_id": None,
        "partner_created": False,
        "total": None,
        "confirmed": False,
        "record": None,
    },
    "attach_file": {"attachment_id": None, "model": None, "record_id": None, "main_set": False},
}
NEUTRAL_OUTPUTS["create_vendor_bill"] = dict(NEUTRAL_OUTPUTS["create_customer_invoice"])


def neutral_result(operation_id: str) -> dict[str, Any]:
    return dict(NEUTRAL_OUTPUTS[operation_id])


__all__ = [
    "CHANGE_MODELS",
    "MODULE_OF",
    "NEUTRAL_OUTPUTS",
    "Progress",
    "neutral_result",
    "run",
    "validate",
]
