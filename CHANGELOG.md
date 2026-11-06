# Changelog

## 0.1.0 — 2026-11-06

First release.

- Odoo 16 to 20 through Odoo's documented external APIs: JSON-2 (Odoo 19+, bearer API key) and
  XML-RPC (Odoo 16-20, database, login and API key). XML-RPC answers are parsed with DTDs and
  entities refused.
- Everyday steps: Find or create partner, Find or create product, Create customer invoice, Create
  vendor bill (with an attached file and posting), Register payment, Create sale order (and
  confirm), Stock levels, Product prices, Changes since, Partner balance, Attach file to a
  record, Download attachment.
- Generic steps on any model: search, read, count, group and total, describe fields, create,
  update, delete, call a public method, export to a JSONL file.
- Field names that differ between Odoo versions come from one table, checked against the Odoo
  sources of each version and against live Odoo 16, 17, 18, 19 and 20 databases.
- Every write is a declared external effect, suppressed in test runs; ambiguous outcomes of
  business methods are reported as unknown and not retried; everyday steps look up before they
  create. Products, taxes, currencies and units are looked up before a partner is created; a
  step that fails after a write names what it already did in Odoo.
- Register payment treats a payment as a rerun only when it has the same memo, date, amount,
  currency and direction and is linked to this invoice; a payment of another invoice never
  stops this one from being paid.
- Changes since resumes with a (next_since, next_after_id) cursor that skips no record, and
  includes archived records;
  Partner balance reports overdue receivable and payable apart.
- Group and total accepts date grouping such as `invoice_date:month`.
- On XML-RPC, `null` values are sent as `false`; numbers that do not fit in 32 bits are refused
  before sending.
- Not offered: the computed pricelist price (private in Odoo), private methods, the database
  service.
- Icon: the official Odoo icon from the odoo/odoo repository
  ([odoo-icon-192x192.png](https://github.com/odoo/odoo/blob/19.0/addons/web/static/img/odoo-icon-192x192.png)),
  scaled to 128×128, used to identify the integration.
