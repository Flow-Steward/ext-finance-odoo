"""Render the UI pages that restate the operation registry, so they cannot drift.

* ``ui/pages/odoo-api.yaml`` — the everyday operations in full detail, how operations
  behave, the generic model operations, and coverage
* ``ui/pages/changelog.yaml`` — ``CHANGELOG.md``

The platform renders page bodies as simple markdown: headings, paragraphs, lists,
bold, code and ``[label](https://…)`` links. Tables and bare URLs are not rendered,
so no page uses them (``tests/test_contracts.py`` checks that).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ADVANCED_PREFIX = "Advanced · "
JSON2_DOCS = "https://www.odoo.com/documentation/19.0/developer/reference/external_api.html"
XMLRPC_DOCS = "https://www.odoo.com/documentation/18.0/developer/reference/external_api.html"
RPC_DEPRECATION = (
    "https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html"
)
ORM_DOCS = "https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html"
HIDDEN_RESULTS = frozenset({"external_effect_status", "definitely_no_external_effect"})

BEHAVIOUR = """\
### Odoo versions and APIs

- Odoo 16, 17 and 18 are reached through XML-RPC; Odoo 19 and 20 through the JSON-2 API (or XML-RPC, which Odoo still offers until Odoo 22). The connection says which.
- Field names that changed between versions are chosen per version by the extension (for example the unit on sale order lines, the stock unit, product types, attachment content, the payment memo).
- Every step works with the rights of the API key's user.

### Inputs

- Models are Odoo's technical names (res.partner, account.move). Field names in domains, field lists and values are checked against the model before anything is sent.
- Domains are lists of [field, operator, value] with "&", "|" and "!". Only the standard operators are accepted.
- Numbers are written with "." as the decimal point; amounts and quantities are exact decimals.

### Writes, retries and errors

- Flow Steward remembers every create, update, delete, post, confirm, payment and method call it made, so a retried step does not send it twice. Test runs never write.
- Each Odoo call runs in its own database transaction. When Odoo itself refuses a call — a validation error, missing rights, a missing record, a locked record — nothing was saved and the step says so.
- A business method (posting an invoice, confirming an order, registering a payment, any method you call) may contact outside services, such as e-invoicing, before it fails. A server error on one of these, a timeout or a lost connection is reported as an unknown outcome and is not retried: check the record in Odoo first.
- Everyday steps look up before they create (partners, products, invoices with a reference, orders with a reference, payments with a memo), so running one again after an unknown outcome finds what the first run made.
- If a step that writes several things fails half-way, it says exactly what was already created.

### Lists and files

- Lists come in pages (limit, offset). A step output has a size limit: a long read stops at a page boundary and returns next_offset. For whole tables use Export records.
- Files travel as workflow files: attach one to any record, or download an attachment.
"""


class _PageDumper(yaml.SafeDumper):
    """Multi-line strings as ``|`` blocks, so the page files read like markdown."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_PageDumper.add_representer(str, _represent_str)


def dump_page(path: Path, header: str, page: dict[str, Any]) -> None:
    text = yaml.dump(page, Dumper=_PageDumper, sort_keys=False, allow_unicode=True, width=1000)
    path.write_text(header + text, encoding="utf-8")


def _names(fields: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in fields) if fields else "none"


def _inputs(op: dict[str, Any]) -> str:
    parts = [
        f"`{i['name']}`" + ("" if i.get("required") else " (optional)")
        for i in op["inputs"]
        if i["name"] != "connection_ref"
    ]
    return ", ".join(parts) if parts else "none"


def _results(op: dict[str, Any]) -> list[str]:
    return [
        n.removeprefix("result.")
        for n in op["result_fields"]
        if n.removeprefix("result.") not in HIDDEN_RESULTS
    ]


def _section(op: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    title = str(op["display_name"]).removeprefix(ADVANCED_PREFIX)
    kind = "write" if op["operation_kind"] == "action" else "read"
    lines = [f"### {title}", "", str(op["description"]), ""]
    lines.append(f"- Action id: `{op['operation_id']}`")
    lines.append(f"- Kind: {kind}")
    lines.append(f"- Odoo calls: {', '.join(f'`{c}`' for c in spec.get('calls', []))}")
    if spec.get("module"):
        lines.append(f"- Needs the Odoo app: `{spec['module']}`")
    lines.append(f"- Inputs: {_inputs(op)}")
    lines.append(f"- Results: {_names(_results(op))}")
    lines.append("")
    return lines


COVERAGE = f"""\
- **Covered:** every model of the database through the generic steps — search, read, count, group and total, describe fields, create, update, delete, call a public method, export — on Odoo 16 to 20, plus the everyday steps above.
- **APIs:** [JSON-2]({JSON2_DOCS}) (Odoo 19+) and [XML-RPC]({XMLRPC_DOCS}) (Odoo 16-20; [deprecated since 19, removed in Odoo 22]({RPC_DEPRECATION})). The ORM methods are Odoo's own ([ORM reference]({ORM_DOCS})).
- **Not offered: the computed pricelist price.** Odoo computes it in private methods (names starting with _), which Odoo does not let any API call. Product prices returns the product's prices and the pricelist rules instead.
- **Not offered: private methods.** Odoo refuses them over every API.
- **Not offered: the database service** (create, copy or drop databases); Odoo removed it from the API in Odoo 20.
- **Not offered: Odoo's own MCP server.** It is a separate integration (Odoo 20, read-only by default); this extension uses the documented external APIs for typed workflow steps.
"""


def api_page(ops: list[dict[str, Any]], specs: list[dict[str, Any]]) -> dict[str, Any]:
    spec_by_id = {s["operation_id"]: s for s in specs}
    everyday = [op for op in ops if not str(op["display_name"]).startswith(ADVANCED_PREFIX)]
    advanced = [op for op in ops if str(op["display_name"]).startswith(ADVANCED_PREFIX)]
    lines = [
        "These steps take plain inputs and make the Odoo calls for you. The generic steps after them "
        "work on any model, named **Advanced · …** in the step picker.",
        "",
    ]
    for op in everyday:
        lines.extend(_section(op, spec_by_id[op["operation_id"]]))
    generic: list[str] = []
    for op in advanced:
        generic.extend(_section(op, spec_by_id[op["operation_id"]]))
    return {
        "page_id": "odoo-api",
        "title": "Odoo API",
        "description": "Every Odoo step: what it does, which Odoo calls it makes, its inputs and results.",
        "components": [
            {
                "component_id": "operations_everyday",
                "type": "markdown",
                "title": "Everyday operations",
                "body": "\n".join(lines).rstrip() + "\n",
            },
            {
                "component_id": "operations_behaviour",
                "type": "markdown",
                "title": "How operations behave",
                "body": BEHAVIOUR,
            },
            {
                "component_id": "operations_generic",
                "type": "markdown",
                "title": "Any model",
                "body": "\n".join(generic).rstrip() + "\n",
            },
            {
                "component_id": "operations_coverage",
                "type": "markdown",
                "title": "Coverage",
                "body": COVERAGE,
            },
        ],
    }


def changelog_page() -> dict[str, Any]:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return {
        "page_id": "changelog",
        "title": "Changelog",
        "description": "What changed in each release of this extension.",
        "components": [
            {
                "component_id": "changelog_entries",
                "type": "markdown",
                "title": "Release history",
                "body": "\n".join(line.rstrip() for line in lines).strip() + "\n",
            }
        ],
    }
