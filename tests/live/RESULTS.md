# Live results

Run with `tests/live/odoo_matrix.sh up <version>` (official `odoo:<version>.0` images,
PostgreSQL 15, or 16 for Odoo 20; apps account, sale, stock; demo data) and
`tests/live/test_live_odoo.py` through the real runtime. Date: 2026-11-06.

Every run covers: test_connection; the generic round trip (create, read, update, search, count,
group and total, describe fields, call a method, delete); find or create partner (create, then
found again); find or create product (stocked goods, service); customer invoice with lines,
attached PDF, posting, and a rerun that returns it; register payment and its rerun; vendor bill;
sale order with confirmation and its rerun; stock levels; product prices; changes since;
partner balance; attach a file and download it; export records.

| Odoo | API | Result |
|---|---|---|
| 16.0 | XML-RPC | 10/10 passed |
| 17.0 | XML-RPC | 10/10 passed |
| 18.0 | XML-RPC | 10/10 passed |
| 19.0 | JSON-2 | 10/10 passed |
| 19.0 | XML-RPC | 10/10 passed |
| 20.0 | JSON-2 | 10/10 passed |
| 20.0 | XML-RPC | 10/10 passed |

Mismatches found by these runs and fixed:

- XML-RPC `create` takes its values as the first positional argument (`call_kw_model_create`), not
  by name.
- Odoo 16-18 XML-RPC reports a missing model as a warning fault "Object x doesn't exist".
- Odoo's XML-RPC cannot encode a `None` answer; it fails after the method committed, so that fault
  is a success with no result.
- Odoo 20 needs PostgreSQL 16 (`any_value`), reads `ir.attachment.raw` as `{filename, content}`, and
  removed `product.pricelist.item.percent_price`.

Not run: demo.odoo.com (Odoo Online 20.0). Its databases answer JSON-2, but creating an API key
there requires Odoo's interactive password re-check (res.users.identitycheck), so a key has to be
made by hand in its web interface.
