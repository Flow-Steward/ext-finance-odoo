# Changelog

## 0.1.0 — unreleased

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
  create.
- Not offered: the computed pricelist price (private in Odoo), private methods, the database
  service.
- Icon: the official Odoo icon from the odoo/odoo repository
  ([odoo-icon-192x192.png](https://github.com/odoo/odoo/blob/19.0/addons/web/static/img/odoo-icon-192x192.png)),
  scaled to 128×128, used to identify the integration.
