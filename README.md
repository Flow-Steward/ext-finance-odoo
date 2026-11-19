# Odoo integration for Flow Steward

<img src="icon.png" width="96" alt="Odoo integration for Flow Steward">

[![CI](https://img.shields.io/github/actions/workflow/status/Flow-Steward/ext-finance-odoo/ci.yml?branch=main&label=CI)](https://github.com/Flow-Steward/ext-finance-odoo/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/Flow-Steward/ext-finance-odoo?label=release)](https://github.com/Flow-Steward/ext-finance-odoo/releases/latest)

Automate Odoo 16–20 with Flow Steward workflows and AI agents: create invoices and sale orders, register payments, read stock — without writing code.

This Flow Steward extension is an Odoo integration for ERP workflow automation. It works through Odoo's external API (JSON-2 or XML-RPC) and covers partners, products, customer invoices, vendor bills, payments, sale orders, stock, prices and any other installed Odoo model, including CRM.

## What is this

Odoo is an ERP and business suite used by companies for CRM, sales, accounting, inventory and more. This extension is a connector: it adds ready Odoo steps to the Flow Steward workflow editor and the same operations as tools for AI agents. Read more about Flow Steward at <https://docs.flowsteward.org>.

## What you can automate

- Create customer invoices in Odoo from orders in your online shop — the customer and products are found or created first, no retyping.
- Turn incoming orders into Odoo quotations and confirm them into sale orders.
- Record supplier invoices as vendor bills with the original PDF attached.
- Register payments against posted invoices and bills, in full or in part.
- Send stock levels and product prices from Odoo to your shop or marketplace.
- Check a customer's balance before accepting a new order.
- Create CRM leads or any other record from web forms, and pass records changed since a date to other systems.

## Why teams use it

- **No duplicate documents.** Everyday steps look up before they create: partners by reference, VAT or e-mail, products by internal reference or barcode, invoices, bills and sale orders by their reference. A rerun returns the record already created.
- **Amounts without rounding errors.** Money and quantities are exact decimals from input to output.
- **A change is not sent twice.** Nothing ambiguous is retried: a timeout or an unclear answer to a write is reported as unknown, so the workflow can check Odoo first.
- **A test run changes nothing.** Every write is a declared external effect, suppressed by Flow Steward in test runs.
- **Any model, not only the everyday ones.** 23 steps: 13 everyday steps plus generic steps that search, read, count, group, create, update and delete records of any installed model and call its public methods.
- **The API key stays secret.** It is removed from every answer before the answer is read, and Odoo tracebacks are never passed on.

## What's inside

Everyday steps:

- **Test the connection** — checks the address, database and API key and reads the Odoo version.
- **Find or create partner** — finds a customer or supplier by reference, VAT or e-mail, creates it when missing.
- **Find or create product** — finds a product by internal reference or barcode, creates it when missing.
- **Create customer invoice** — an invoice with its lines, optionally posted and with a file attached.
- **Create vendor bill** — the same for a supplier's bill.
- **Register payment** — pays a posted invoice or bill through Odoo's payment wizard, in full or in part.
- **Create sale order** — a quotation with its lines, optionally confirmed into a sale order.
- **Read stock levels** — quantities on hand, reserved and available per product, location and lot.
- **Read product prices** — sales price, list price, cost and the pricelist rules for a product.
- **Read records changed since a date** — records of one kind, page by page, none skipped.
- **Read partner balance** — receivable and payable totals with open and overdue invoices and bills.
- **Attach file to a record** — a workflow file on an invoice, an order or a partner.
- **Download attachment** — a file attached in Odoo as a workflow file.

Generic steps work on any installed model; in the step picker their names start with "Advanced ·": Search records, Read records by id, Count records, Group and total records, Describe a model's fields, Create a record, Update records, Delete records, Call a model method and Export records to a JSONL file. Together that is 23 API operations available as steps, across accounting, sales, inventory, CRM and every other installed module. The [workflow action guide](docs/workflow-actions.md) shows examples.

## Get started

1. In Flow Steward, open **Extensions → Catalog**, find **Odoo** and install it.
2. In Odoo, as the user the steps should run as, create an API key: **Preferences → Account Security → New API Key**.
3. Open the extension's **Connection** page and add a connection: the Odoo address, the API (**JSON-2** for Odoo 19 and 20, **XML-RPC** for Odoo 16 to 18), the API key, and for XML-RPC also the database name and the login. Press **Test connection**.
4. Build your first workflow: for example, an order arrives → **Find or create partner** → **Create customer invoice** → **Register payment** when the money arrives.

## Requirements

- Odoo 16, 17, 18, 19 or 20, reachable over https on a public host name.
- JSON-2 needs Odoo 19 or later; XML-RPC works on Odoo 16 to 20. Odoo has deprecated XML-RPC since Odoo 19 and plans to remove it in Odoo 22 and Odoo Online 21.1.
- On Odoo Online, external API access needs the **Custom** plan; Odoo.sh and self-hosted Odoo have it.
- An Odoo user with the rights your workflows need, and its API key. Odoo 18 and later ask for a key duration; the key stops working after it.
- The models, fields and actions available depend on the installed Odoo modules.
- Flow Steward 0.9.16 or later.

## FAQ

**How do I create Odoo invoices from my online shop orders?**
Add a trigger for new orders, then **Create customer invoice** with the order number as the reference. It finds or creates the customer, and a rerun returns the same invoice.

**Can Flow Steward sync Odoo stock and prices to another system?**
Yes. **Read stock levels** and **Read product prices** return current values, and **Read records changed since a date** passes only what changed.

**Does it work with Odoo Online, Odoo.sh and self-hosted Odoo?**
Yes, for Odoo 16 to 20. Odoo Online needs the Custom plan for API access.

**Can I automate Odoo CRM or a custom module?**
Yes. The generic steps work on any installed model, for example `crm.lead`, with the field names **Advanced · Describe a model's fields** returns.

**Will a payment be registered twice if the workflow runs again?**
An invoice already paid, or the same payment (memo, amount, date) found again, is not paid twice. Give partial payments a memo: without one, a rerun registers the payment again.

## Links

- Flow Steward documentation: <https://docs.flowsteward.org>
- Flow Steward extension catalog: <https://github.com/Flow-Steward/extensions>
- Issues and support: <https://github.com/Flow-Steward/ext-finance-odoo/issues>
- Odoo external API (Odoo 19 and later): <https://www.odoo.com/documentation/19.0/developer/reference/external_api.html>
- Odoo external API (Odoo 16 to 18): <https://www.odoo.com/documentation/18.0/developer/reference/external_api.html>
- Changes: [CHANGELOG.md](CHANGELOG.md)

## For developers

The contracts and pages are generated from `tests/reference/op_specs.py`; version-specific field names live in `runtime/versions.py`. From the extension folder:

```bash
python3 tests/reference/build_contracts.py     # regenerate contracts and pages
tests/live/odoo_matrix.sh up 18                # disposable Odoo for the live tests
```

From a Flow Steward checkout (the checks run in the `fs-audit` container):

```bash
EXT=$PWD/workspace/extensions/flowsteward_odoo
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root "$EXT"
./flow-steward extensions validate flowsteward.odoo --root workspace/extensions --health --marketplace
./flow-steward extensions test flowsteward.odoo --root workspace/extensions
```

The live tests (`tests/live/`) are skipped unless `ODOO_LIVE_URL` is set; see the module docstring.

Odoo and its marks belong to Odoo S.A.; see [BRANDING.md](BRANDING.md) for the icon's source.
