# Odoo for Flow Steward

`flowsteward.odoo` connects Flow Steward workflows to **Odoo 16, 17, 18, 19 and 20** — Odoo
Online, Odoo.sh or your own server — through Odoo's documented external APIs:

- **JSON-2** (Odoo 19+): `POST /json/2/<model>/<method>` with `Authorization: bearer <API key>`
  ([docs](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html));
- **XML-RPC** (Odoo 16-20): `/xmlrpc/2/common` and `/xmlrpc/2/object`, database + login + API key
  ([docs](https://www.odoo.com/documentation/18.0/developer/reference/external_api.html)).
  Deprecated since Odoo 19 and scheduled for removal in Odoo 22 and Odoo Online 21.1
  ([notice](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html)).

Odoo's own MCP server (Odoo 20, read-only unless an administrator exposes server actions) is a
separate integration; this extension provides typed, deterministic workflow steps with write
safety on the versions customers run.

## Steps

Everyday steps (listed first in the step picker): Find or create partner, Find or create product,
Create customer invoice, Create vendor bill, Register payment, Create sale order, Stock levels,
Product prices, Changes since, Partner balance, Attach file to a record, Download attachment.

Generic steps on any model ("Advanced · …"): Search records, Read records by id, Count records,
Group and total records, Describe a model's fields, Create a record, Update records, Delete
records, Call a model method, Export records to a JSONL file.

## Getting a key

In Odoo, as the user the steps should run as: **Preferences → Account Security → New API Key**.
Odoo 18+ also asks for a duration. The key is shown once. On Odoo Online, API access needs the
**Custom** plan. XML-RPC also needs the database name and the user's login.

## Writes and failures

- Every write is a declared external effect, suppressed in test runs.
- Each Odoo call is one database transaction, rolled back on error (JSON-2 docs, "Transaction").
- Plain CRUD (`create`, `write`, `unlink`): any error Odoo answers itself means nothing was saved.
- Business methods (`action_post`, `action_confirm`, the payment wizard's
  `action_create_payments`, any `call_method`) may call outside services (e-invoicing, mail, IAP)
  before failing: only Odoo's refusals — UserError/ValidationError (422, XML-RPC fault 2),
  AccessError (403, fault 4), AccessDenied (fault 3), MissingError (404), LockError (409) — count as
  "nothing done"; any other server error is `timeout_unknown`.
- Timeouts, lost connections, proxy errors without an Odoo body and unreadable answers to writes
  are `timeout_unknown`. Nothing ambiguous is retried.
- Everyday steps look up before they create: partners (ref, VAT, e-mail), products (internal
  reference, barcode), invoices and bills with a reference (same partner and date), sale orders with
  a customer reference, payments with a memo. A partial payment without a memo cannot be told
  from a second payment, so running it again pays again.
- Products, taxes, currencies and units are looked up before a partner is created, so a wrong
  code stops the step with nothing written.
- Odoo tracebacks (`debug`, XML-RPC fault stacks) are never passed on; the API key is removed from
  every answer before it is parsed.
- XML-RPC answers are parsed with defusedxml (DTDs and entities refused) and sent through the
  SDK's pinned opener; the server address must be https and a public host name.

## Development

From the extension folder:

```bash
python3 tests/reference/build_contracts.py     # regenerate contracts and pages
tests/live/odoo_matrix.sh up 18                # disposable Odoo for the live tests
```

From the Flow Steward checkout (all checks run in the `fs-audit` container):

```bash
EXT=$PWD/workspace/extensions/flowsteward_odoo
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$EXT"
./flow-steward extensions validate flowsteward.odoo --root <bundle folder> --health --marketplace
```

The live tests (`tests/live/`) are skipped unless `ODOO_LIVE_URL` is set; see the module docstring.

## Icon

`icon.png` is the Odoo icon, © Odoo S.A., used to identify the integration. Source: the odoo/odoo
repository, `addons/web/static/img/odoo-icon-192x192.png`, scaled to 128×128.
