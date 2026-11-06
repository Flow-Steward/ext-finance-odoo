"""Contracts of every operation: inputs, outputs and texts (the source build_contracts reads).

Outputs list exactly the keys the runtime returns; build_contracts adds the two
external-effect outputs to every write.
"""

from __future__ import annotations

from typing import Any

CONNECTION = {"name": "connection_ref", "value_type": "connection_ref", "required": True}
MODEL_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,6}$"
DATE: dict[str, Any] = {"pattern": r"^\d{4}-\d{2}-\d{2}$", "maxLength": 10}

#: Words shown for coded select values.
OPTION_LABELS: dict[str, dict[Any, str]] = {
    "kind": {"goods": "Goods", "service": "Service"},
    "entity": {
        "partners": "Partners (customers, suppliers, contacts)",
        "products": "Products",
        "invoices_and_bills": "Invoices and bills",
        "payments": "Payments",
        "sale_orders": "Sale orders",
        "stock_quants": "Stock (quants)",
    },
}


def text(
    name: str, title: str, description: str, *, required: bool = False, **extra: Any
) -> dict[str, Any]:
    schema = {"type": "string", "title": title, "description": description, "maxLength": 200}
    schema.update(extra)
    return {"name": name, "value_type": "text", "required": required, "schema": schema}


def whole(
    name: str,
    title: str,
    description: str,
    *,
    required: bool = False,
    minimum: int = 1,
    maximum: int = 10**9,
) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "integer",
        "required": required,
        "schema": {
            "type": "integer",
            "minimum": minimum,
            "maximum": maximum,
            "title": title,
            "description": description,
        },
    }


def flag(name: str, title: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "boolean",
        "required": False,
        "schema": {"type": "boolean", "title": title, "description": description},
    }


def amount(name: str, title: str, description: str, *, required: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "text",
        "required": required,
        "schema": {"type": ["string", "number"], "title": title, "description": description},
    }


def choice(
    name: str, title: str, description: str, values: list[str], *, required: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "text",
        "required": required,
        "schema": {"type": "string", "enum": values, "title": title, "description": description},
    }


def json_input(
    name: str,
    title: str,
    description: str,
    *,
    kind: str = "object",
    required: bool = False,
    **schema: Any,
) -> dict[str, Any]:
    body = {"type": kind, "title": title, "description": description, **schema}
    return {
        "name": name,
        "value_type": "array" if kind == "array" else "object",
        "required": required,
        "schema": body,
    }


def file_input(name: str, *, required: bool) -> dict[str, Any]:
    return {"name": name, "value_type": "artifact_handle", "required": required}


def out(
    name: str, value_type: str, schema_type: Any = None, description: str = ""
) -> dict[str, Any]:
    if value_type == "artifact_handle":
        return {"name": name, "value_type": value_type}
    schema: dict[str, Any] = {"type": schema_type} if schema_type is not None else {}
    if description:
        schema["description"] = description
    return {"name": name, "value_type": value_type, "schema": schema}


MODEL = text(
    "model",
    "Model",
    "The technical model name, for example res.partner or account.move.",
    required=True,
    maxLength=80,
    pattern=MODEL_PATTERN,
)
DOMAIN = json_input(
    "domain",
    "Domain",
    'Conditions in Odoo\'s domain form: [[field, operator, value], …] with optional "&", "|", "!". '
    "Field names are checked against the model; operators: = != < <= > >= in, not in, like, ilike, "
    "not like, not ilike, =like, =ilike, child_of, parent_of.",
    kind="array",
    maxItems=50,
)
FIELDS = json_input(
    "fields",
    "Fields",
    'The field names to return, for example ["name", "email"]. Default: id and display name.',
    kind="array",
    maxItems=200,
    items={"type": "string"},
)
IDS = json_input(
    "ids",
    "Record ids",
    "The ids of the records, for example [7, 12].",
    kind="array",
    required=True,
    maxItems=1000,
    items={"type": "integer"},
)
ORDER = text("order", "Order", "Sort order, for example write_date desc, id.")
LIMIT = whole("limit", "Limit", "Records per page, 1 to 200. Default 80.", maximum=200)
OFFSET = whole(
    "offset", "Offset", "How many records to skip; pass next_offset to continue.", minimum=0
)
RECORD = out("record", "object", ["object", "null"], "The first record, or null.")
RECORDS = out("records", "array", "array", "The records, with the requested fields.")

PARTNER_LOOKUP = [
    whole(
        "partner_id", "Partner id", "The partner's id in Odoo. Or look it up with the fields below."
    ),
    text(
        "partner_ref",
        "Partner reference",
        "The partner's internal reference (ref), looked up first.",
    ),
    text("partner_vat", "Partner VAT", "The partner's tax id (vat), looked up second."),
    text("partner_email", "Partner e-mail", "The partner's e-mail, looked up last."),
    text(
        "partner_name",
        "Partner name",
        "Creates the partner with this name when none of the above matches.",
    ),
]
LINE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_id": {"type": "integer", "description": "The product's id."},
        "product_code": {
            "type": "string",
            "maxLength": 120,
            "description": "The product's internal reference (default_code).",
        },
        "description": {"type": "string", "maxLength": 1000, "description": "The line text."},
        "quantity": {"type": ["number", "string"], "description": "Quantity. Default 1."},
        "price_unit": {
            "type": ["number", "string"],
            "description": "Unit price. Default: the product's price.",
        },
        "discount": {"type": ["number", "string"], "description": "Discount in percent."},
        "taxes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
            "description": "Tax names. Default: the product's taxes.",
        },
    },
}
ORDER_LINE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_id": {"type": "integer"},
        "product_code": {"type": "string", "maxLength": 120},
        "quantity": {"type": ["number", "string"], "description": "Quantity. Default 1."},
        "price_unit": {
            "type": ["number", "string"],
            "description": "Unit price. Default: the pricelist's.",
        },
        "discount": {"type": ["number", "string"]},
        "description": {"type": "string", "maxLength": 1000},
        "unit": {
            "type": "string",
            "maxLength": 60,
            "description": "Unit of measure name, for example Units.",
        },
    },
}


def invoice(operation_id: str, noun: str) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "display_name": f"Create {noun}",
        "kind": "action",
        "module": "account",
        "calls": [
            "res.partner search_read / create",
            "account.move search_read / create",
            "ir.attachment create",
            "account.move action_post",
        ],
        "description": (
            f"Creates a {noun} (account.move) with its lines in one request, after finding — or "
            "creating — the partner; can attach a file and post it. With a reference, a rerun "
            f"returns the {noun} already created for the same partner and date instead of a second one."
        ),
        "inputs": [
            CONNECTION,
            *PARTNER_LOOKUP,
            text(
                "reference",
                "Reference",
                f"The {noun}'s reference (ref). Makes reruns safe: the step looks it up first.",
            ),
            text("invoice_date", "Date", "The invoice date, YYYY-MM-DD. Default: today.", **DATE),
            text(
                "due_date",
                "Due date",
                "The due date, YYYY-MM-DD. Default: from the payment terms.",
                **DATE,
            ),
            text(
                "currency_code",
                "Currency",
                "A currency code such as EUR. Default: the company's.",
                maxLength=3,
            ),
            text("notes", "Notes", "Terms and conditions printed on the document.", maxLength=2000),
            json_input(
                "lines",
                "Lines",
                "The lines, 1 to 200: product or description, quantity, price, discount, taxes.",
                kind="array",
                required=True,
                minItems=1,
                maxItems=200,
                items=LINE_ITEM,
            ),
            flag("post", "Post", f"Post (confirm) the {noun} once it is created."),
            file_input("document_file", required=False),
        ],
        "outputs": [
            out("invoice_id", "integer", ["integer", "null"], "The document's id."),
            out("number", "text", ["string", "null"], 'Its number ("/" until posted).'),
            out("state", "text", ["string", "null"], "draft, posted or cancel."),
            out("created", "boolean", "boolean", "False when an existing document was returned."),
            out("partner_id", "integer", ["integer", "null"], "The partner's id."),
            out(
                "partner_created", "boolean", "boolean", "True when this step created the partner."
            ),
            out("total_untaxed", "text", ["string", "null"], "Untaxed amount, exact decimal."),
            out("total_tax", "text", ["string", "null"], "Tax amount, exact decimal."),
            out("total", "text", ["string", "null"], "Total, exact decimal."),
            out("amount_due", "text", ["string", "null"], "Amount still due, exact decimal."),
            out("attachment_id", "integer", ["integer", "null"], "The attached file's id."),
            out("posted", "boolean", ["boolean", "null"], "Whether the document is posted."),
            RECORD,
        ],
    }


def operations() -> list[dict[str, Any]]:
    """Everyday operations first, then the generic ones (named Advanced · …)."""
    return [
        {
            "operation_id": "test_connection",
            "display_name": "Test the connection",
            "kind": "source",
            "calls": ["/web/webclient/version_info", "res.users context_get"],
            "description": "Reads the Odoo version and makes one authenticated read to check the address, database and API key.",
            "inputs": [CONNECTION],
            "outputs": [
                out("connected", "boolean", "boolean"),
                out("odoo_version", "integer", "integer", "The Odoo major version, 16 to 20."),
                out("server_version", "text", ["string", "null"]),
                out("api", "text", "string", "json2 or xmlrpc."),
                out("language", "text", ["string", "null"], "The API user's language."),
            ],
        },
        {
            "operation_id": "find_or_create_partner",
            "display_name": "Find or create partner",
            "kind": "action",
            "calls": ["res.partner search_read", "res.partner create"],
            "description": (
                "Looks the partner (customer or supplier) up by reference, then VAT, then e-mail; "
                "several matches or conflicting values are an error. Creates it only when none matches."
            ),
            "inputs": [
                CONNECTION,
                text("ref", "Reference", "The partner's internal reference, looked up first."),
                text("vat", "VAT", "The tax id, looked up second."),
                text("email", "E-mail", "The e-mail address, looked up last."),
                text("name", "Name", "The name, needed when the partner has to be created."),
                flag("is_company", "Company", "Create a company (default) rather than a person."),
                text("phone", "Phone", "The phone number of a new partner."),
                text("street", "Street", "The street of a new partner."),
                text("city", "City", "The city of a new partner."),
                text("zip", "Postcode", "The postcode of a new partner."),
                text(
                    "country_code",
                    "Country",
                    "Two-letter country code of a new partner, for example LT.",
                    maxLength=2,
                ),
            ],
            "outputs": [
                out("partner_id", "integer", ["integer", "null"], "The partner's id."),
                out("created", "boolean", "boolean", "True when this step created the partner."),
                out("matched_by", "text", ["string", "null"], "ref, vat or email."),
                RECORD,
            ],
        },
        {
            "operation_id": "find_or_create_product",
            "display_name": "Find or create product",
            "kind": "action",
            "calls": ["product.product search_read", "product.product create"],
            "description": (
                "Looks the product up by internal reference, then barcode; creates it when none matches, "
                "as goods (optionally stocked) or a service, with the right fields for the Odoo version."
            ),
            "inputs": [
                CONNECTION,
                text(
                    "product_code",
                    "Internal reference",
                    "The product's internal reference (default_code), looked up first.",
                ),
                text("barcode", "Barcode", "The barcode, looked up second."),
                text("name", "Name", "The product name, needed when it has to be created."),
                choice("kind", "Kind", "Goods or a service. Default: goods.", ["goods", "service"]),
                flag(
                    "storable",
                    "Track stock",
                    "Track stock for new goods (needs the Inventory app).",
                ),
                amount(
                    "list_price",
                    "Sales price",
                    "The sales price of a new product, for example 12.50.",
                ),
            ],
            "outputs": [
                out("product_id", "integer", ["integer", "null"], "The product's id."),
                out("created", "boolean", "boolean", "True when this step created the product."),
                out("matched_by", "text", ["string", "null"], "product_code or barcode."),
                RECORD,
            ],
        },
        invoice("create_customer_invoice", "customer invoice"),
        invoice("create_vendor_bill", "vendor bill"),
        {
            "operation_id": "register_payment",
            "display_name": "Register payment",
            "kind": "action",
            "module": "account",
            "calls": [
                "account.move read",
                "account.payment search_read",
                "account.payment.register create",
                "account.payment.register action_create_payments",
            ],
            "description": (
                "Pays a posted invoice or bill in full or in part with Odoo's payment wizard, which "
                "creates, posts and reconciles the payment. An invoice already paid, or the same payment "
                "(memo, amount, date) found again, is not paid twice."
            ),
            "inputs": [
                CONNECTION,
                whole(
                    "invoice_id", "Invoice id", "The posted invoice or bill to pay.", required=True
                ),
                amount("amount", "Amount", "The amount paid. Default: the amount still due."),
                text(
                    "payment_date", "Date", "The payment date, YYYY-MM-DD. Default: today.", **DATE
                ),
                text(
                    "journal_code",
                    "Journal",
                    "The bank or cash journal's short code. Default: Odoo's choice.",
                    maxLength=20,
                ),
                text(
                    "memo",
                    "Memo",
                    "The payment memo; with it a rerun finds the same payment instead of paying twice.",
                ),
            ],
            "outputs": [
                out("invoice_id", "integer", ["integer", "null"]),
                out(
                    "created",
                    "boolean",
                    "boolean",
                    "False when the invoice was already paid or the same payment exists.",
                ),
                out(
                    "payment_state",
                    "text",
                    ["string", "null"],
                    "not_paid, partial, in_payment or paid.",
                ),
                out("amount_due", "text", ["string", "null"], "Amount still due, exact decimal."),
            ],
        },
        {
            "operation_id": "create_sale_order",
            "display_name": "Create sale order",
            "kind": "action",
            "module": "sale",
            "calls": [
                "res.partner search_read / create",
                "sale.order search_read / create",
                "sale.order action_confirm",
            ],
            "description": (
                "Creates a quotation with its lines after finding — or creating — the customer, and can "
                "confirm it into a sale order. With a reference, a rerun returns the order already created."
            ),
            "inputs": [
                CONNECTION,
                *PARTNER_LOOKUP,
                text(
                    "reference",
                    "Customer reference",
                    "The customer's reference (client_order_ref). Makes reruns safe.",
                ),
                text(
                    "order_date",
                    "Order date",
                    "The order date, YYYY-MM-DD. Default: today.",
                    **DATE,
                ),
                json_input(
                    "lines",
                    "Lines",
                    "The lines, 1 to 200: product, quantity, price, discount, unit.",
                    kind="array",
                    required=True,
                    minItems=1,
                    maxItems=200,
                    items=ORDER_LINE,
                ),
                flag("confirm", "Confirm", "Confirm the quotation into a sale order."),
            ],
            "outputs": [
                out("order_id", "integer", ["integer", "null"]),
                out("number", "text", ["string", "null"], "The order number."),
                out("state", "text", ["string", "null"], "draft, sent, sale or cancel."),
                out("created", "boolean", "boolean", "False when an existing order was returned."),
                out("partner_id", "integer", ["integer", "null"]),
                out("partner_created", "boolean", "boolean"),
                out("total", "text", ["string", "null"], "Total, exact decimal."),
                out("confirmed", "boolean", "boolean"),
                RECORD,
            ],
        },
        {
            "operation_id": "stock_levels",
            "display_name": "Stock levels",
            "kind": "source",
            "module": "stock",
            "calls": ["stock.quant search_read"],
            "description": "Quantities on hand, reserved and available in internal locations, per product, location and lot.",
            "inputs": [
                CONNECTION,
                whole("product_id", "Product id", "Only the product with this id."),
                text(
                    "product_code",
                    "Internal reference",
                    "Only the product with this internal reference.",
                ),
                whole("location_id", "Location id", "Only this location and its sub-locations."),
                LIMIT,
                OFFSET,
            ],
            "outputs": [
                out(
                    "items",
                    "array",
                    "array",
                    "Product, location, lot, unit, on hand, reserved, available.",
                ),
                out("count", "integer", "integer"),
                out(
                    "totals",
                    "array",
                    "array",
                    "Available and on hand per product and unit, exact decimals.",
                ),
                out("has_more", "boolean", "boolean"),
                out("next_offset", "integer", ["integer", "null"]),
            ],
        },
        {
            "operation_id": "product_prices",
            "display_name": "Product prices",
            "kind": "source",
            "calls": ["product.product read", "product.pricelist.item search_read"],
            "description": (
                "A product's sales price, list price and cost, and the pricelist rules for it. Odoo's "
                "computed pricelist price is a private method and cannot be called through the API."
            ),
            "inputs": [
                CONNECTION,
                whole("product_id", "Product id", "The id of the product to price."),
                text("product_code", "Internal reference", "Or the product's internal reference."),
                whole("pricelist_id", "Pricelist id", "Only the rules of this pricelist."),
            ],
            "outputs": [
                out("product_id", "integer", ["integer", "null"]),
                out("product", "text", ["string", "null"]),
                out("sales_price", "text", ["string", "null"], "lst_price, exact decimal."),
                out("list_price", "text", ["string", "null"]),
                out("cost", "text", ["string", "null"], "standard_price."),
                out("currency", "text", ["string", "null"]),
                out("unit", "text", ["string", "null"]),
                out(
                    "pricelist_rules",
                    "array",
                    "array",
                    "Pricelist, how the price is computed, fixed price, discounts, minimum quantity, dates.",
                ),
            ],
        },
        {
            "operation_id": "changes_since",
            "display_name": "Changes since",
            "kind": "source",
            "calls": ["<model> search_read on write_date"],
            "description": "Records of one kind changed after a moment (write_date), oldest first.",
            "inputs": [
                CONNECTION,
                choice(
                    "entity",
                    "What",
                    "The kind of records to read.",
                    sorted(OPTION_LABELS["entity"]),
                    required=True,
                ),
                text(
                    "since",
                    "Since",
                    "YYYY-MM-DD or YYYY-MM-DD HH:MM:SS, in UTC.",
                    required=True,
                    maxLength=19,
                    pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$",
                ),
                FIELDS,
                LIMIT,
                OFFSET,
            ],
            "outputs": [
                out("model", "text", "string"),
                RECORDS,
                out("count", "integer", "integer"),
                out("has_more", "boolean", "boolean"),
                out("next_offset", "integer", ["integer", "null"]),
            ],
        },
        {
            "operation_id": "partner_balance",
            "display_name": "Partner balance",
            "kind": "source",
            "module": "account",
            "calls": ["res.partner read", "account.move search_read"],
            "description": "What a partner owes and is owed: Odoo's receivable and payable totals, and the open invoices and bills with what is still due and what is overdue.",
            "inputs": [CONNECTION, *PARTNER_LOOKUP[:4], LIMIT, OFFSET],
            "outputs": [
                out("partner_id", "integer", ["integer", "null"]),
                out("partner", "text", ["string", "null"]),
                out(
                    "receivable",
                    "text",
                    ["string", "null"],
                    "Odoo's total receivable (credit), exact decimal.",
                ),
                out(
                    "payable",
                    "text",
                    ["string", "null"],
                    "Odoo's total payable (debit), exact decimal.",
                ),
                out(
                    "open_receivable",
                    "text",
                    ["string", "null"],
                    "Due on the open customer documents read.",
                ),
                out(
                    "open_payable",
                    "text",
                    ["string", "null"],
                    "Due on the open vendor documents read.",
                ),
                out("overdue", "text", ["string", "null"], "The part past its due date."),
                out(
                    "documents",
                    "array",
                    "array",
                    "Each open document: number, type, dates, total and amount due.",
                ),
                out("count", "integer", "integer"),
                out(
                    "complete",
                    "boolean",
                    "boolean",
                    "False when more open documents exist than were read.",
                ),
                out("next_offset", "integer", ["integer", "null"]),
            ],
        },
        {
            "operation_id": "attach_file",
            "display_name": "Attach file to a record",
            "kind": "action",
            "calls": ["ir.attachment create", "<model> write message_main_attachment_id"],
            "description": "Attaches a workflow file to any record (an invoice, an order, a partner) and can make it the record's main attachment.",
            "inputs": [
                CONNECTION,
                MODEL,
                whole("record_id", "Record id", "The record to attach the file to.", required=True),
                file_input("document_file", required=True),
                text(
                    "file_name", "File name", "The name in Odoo. Default: the workflow file's name."
                ),
                flag(
                    "set_main",
                    "Main attachment",
                    "Make it the record's main attachment (the document preview).",
                ),
            ],
            "outputs": [
                out("attachment_id", "integer", ["integer", "null"]),
                out("model", "text", ["string", "null"]),
                out("record_id", "integer", ["integer", "null"]),
                out("main_set", "boolean", "boolean"),
            ],
        },
        {
            "operation_id": "download_attachment",
            "display_name": "Download attachment",
            "kind": "source",
            "calls": ["ir.attachment read / search_read"],
            "description": "Saves a file attached in Odoo as a workflow file: by attachment id, or the newest file on a record (optionally by name).",
            "inputs": [
                CONNECTION,
                whole("attachment_id", "Attachment id", "The id of the attachment to download."),
                text(
                    "model",
                    "Model",
                    "Or the record's model, for example account.move.",
                    maxLength=80,
                    pattern=MODEL_PATTERN,
                ),
                whole("record_id", "Record id", "And the record's id."),
                text(
                    "file_name", "File name", "Take the file with this name. Default: the newest."
                ),
            ],
            "outputs": [
                out("found", "boolean", "boolean"),
                out("document_file", "artifact_handle"),
                out("attachment_id", "integer", ["integer", "null"]),
                out("file_name", "text", ["string", "null"]),
                out("mimetype", "text", ["string", "null"]),
                out("size_bytes", "integer", ["integer", "null"]),
            ],
        },
        # Generic model operations
        {
            "operation_id": "search_records",
            "display_name": "Advanced · Search records",
            "kind": "source",
            "calls": ["<model> fields_get", "<model> search_read"],
            "description": "Reads the records of any model that match a domain (search_read), page by page.",
            "inputs": [
                CONNECTION,
                MODEL,
                DOMAIN,
                FIELDS,
                ORDER,
                LIMIT,
                OFFSET,
                flag(
                    "fetch_all_pages", "Fetch all pages", "Read page after page, up to max_pages."
                ),
                whole(
                    "max_pages",
                    "Max pages",
                    "Upper bound for fetch_all_pages. Default 10.",
                    maximum=100,
                ),
            ],
            "outputs": [
                out("model", "text", "string"),
                RECORDS,
                RECORD,
                out("count", "integer", "integer"),
                out("next_offset", "integer", ["integer", "null"]),
                out("has_more", "boolean", "boolean"),
            ],
        },
        {
            "operation_id": "read_records",
            "display_name": "Advanced · Read records by id",
            "kind": "source",
            "calls": ["<model> read"],
            "description": "Reads records of any model by id.",
            "inputs": [CONNECTION, MODEL, IDS, FIELDS],
            "outputs": [
                out("model", "text", "string"),
                RECORDS,
                RECORD,
                out("count", "integer", "integer"),
            ],
        },
        {
            "operation_id": "count_records",
            "display_name": "Advanced · Count records",
            "kind": "source",
            "calls": ["<model> search_count"],
            "description": "Counts the records of any model that match a domain.",
            "inputs": [CONNECTION, MODEL, DOMAIN],
            "outputs": [out("model", "text", "string"), out("count", "integer", "integer")],
        },
        {
            "operation_id": "group_records",
            "display_name": "Advanced · Group and total records",
            "kind": "source",
            "calls": ["<model> read_group"],
            "description": "Groups records by fields and totals them (read_group), in the form each Odoo version uses.",
            "inputs": [
                CONNECTION,
                MODEL,
                DOMAIN,
                json_input(
                    "groupby",
                    "Group by",
                    'Fields to group by, for example ["partner_id", "invoice_date:month"].',
                    kind="array",
                    maxItems=10,
                    items={"type": "string"},
                ),
                json_input(
                    "aggregates",
                    "Totals",
                    "field:function pairs (sum, avg, min, max, count, count_distinct), or __count. Default: __count.",
                    kind="array",
                    maxItems=20,
                    items={"type": "string"},
                ),
                LIMIT,
            ],
            "outputs": [
                out("model", "text", "string"),
                out("groups", "array", "array", "One object per group."),
                out("count", "integer", "integer"),
            ],
        },
        {
            "operation_id": "describe_fields",
            "display_name": "Advanced · Describe a model's fields",
            "kind": "source",
            "calls": ["<model> fields_get"],
            "description": "Lists a model's fields with their labels, types, relations and whether they are required or read-only.",
            "inputs": [CONNECTION, MODEL],
            "outputs": [
                out("model", "text", "string"),
                out("fields", "object", "object"),
                out("count", "integer", "integer"),
            ],
        },
        {
            "operation_id": "create_record",
            "display_name": "Advanced · Create a record",
            "kind": "action",
            "calls": ["<model> create"],
            "description": "Creates one record of any model with field values (names checked against the model).",
            "inputs": [
                CONNECTION,
                MODEL,
                json_input(
                    "values",
                    "Values",
                    'Field values, for example {"name": "UAB X", "is_company": true}.',
                    required=True,
                ),
            ],
            "outputs": [
                out("model", "text", "string"),
                out("id", "integer", ["integer", "null"], "The new record's id."),
            ],
        },
        {
            "operation_id": "update_records",
            "display_name": "Advanced · Update records",
            "kind": "action",
            "calls": ["<model> write"],
            "description": "Writes the same field values on records of any model.",
            "inputs": [
                CONNECTION,
                MODEL,
                IDS,
                json_input("values", "Values", "Field values to write.", required=True),
            ],
            "outputs": [
                out("model", "text", "string"),
                out("ids", "array", "array"),
                out("updated", "boolean", "boolean"),
            ],
        },
        {
            "operation_id": "delete_records",
            "display_name": "Advanced · Delete records",
            "kind": "action",
            "calls": ["<model> unlink"],
            "description": "Deletes records of any model.",
            "inputs": [CONNECTION, MODEL, IDS],
            "outputs": [
                out("model", "text", "string"),
                out("ids", "array", "array"),
                out("deleted", "boolean", "boolean"),
            ],
        },
        {
            "operation_id": "call_method",
            "display_name": "Advanced · Call a model method",
            "kind": "action",
            "calls": ["<model> <method>"],
            "description": (
                "Calls a public method of any model (for example action_post, action_confirm), on records "
                "or on the model. A server error is reported as an unknown outcome, never retried."
            ),
            "inputs": [
                CONNECTION,
                MODEL,
                text(
                    "method",
                    "Method",
                    "The method name, for example action_post. Names starting with _ are private and refused.",
                    required=True,
                    maxLength=80,
                    pattern=r"^[a-z][a-z0-9_]{0,80}$",
                ),
                json_input(
                    "ids",
                    "Record ids",
                    "The records to call it on. Leave empty for a model method.",
                    kind="array",
                    maxItems=1000,
                    items={"type": "integer"},
                ),
                json_input(
                    "kwargs",
                    "Arguments",
                    'Named arguments, for example {"context": {"lang": "lt_LT"}}.',
                ),
            ],
            "outputs": [
                out("model", "text", "string"),
                out("method", "text", "string"),
                out("result", "json", None, "What the method returned."),
            ],
        },
        {
            "operation_id": "export_records",
            "display_name": "Advanced · Export records to a JSONL file",
            "kind": "source",
            "calls": ["<model> search_read"],
            "description": "Reads every record of any model that matches a domain, 200 at a time, into a JSON-lines workflow file.",
            "inputs": [
                CONNECTION,
                MODEL,
                DOMAIN,
                FIELDS,
                ORDER,
                whole(
                    "start_offset",
                    "Start offset",
                    "Where to start; pass resume_offset of an unfinished export.",
                    minimum=0,
                ),
                whole(
                    "max_pages",
                    "Max pages",
                    "Upper bound of pages of 200. Default 2000.",
                    maximum=2000,
                ),
            ],
            "outputs": [
                out("artifact_handle", "artifact_handle"),
                out("filename", "text", "string"),
                out("content_type", "text", "string"),
                out("record_count", "integer", "integer"),
                out("pages_fetched", "integer", "integer"),
                out("complete", "boolean", "boolean"),
                out("resume_offset", "integer", ["integer", "null"]),
                out(
                    "stopped_by",
                    "text",
                    ["string", "null"],
                    "Why the export stopped early (time, or an error on a later page).",
                ),
            ],
        },
    ]
