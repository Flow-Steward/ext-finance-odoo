# Live results

Run with `tests/live/odoo_matrix.sh up <version>` (official `odoo:<version>.0` images,
PostgreSQL 15, or 16 for Odoo 20; apps account, sale, stock; demo data) and
`tests/live/test_live_odoo.py` through the real runtime.

Latest run: 2026-11-06, extension commit `70d0f6c632d894c8aec58d62ef12d7fb2836cad1` (clean
tree). Machine log: [`logs/2026-11-06-70d0f6c.log`](logs/2026-11-06-70d0f6c.log).

Every run covers: test_connection; the generic round trip (create, read, update, clear a field
with null, search, count, group and total, group by month, describe fields, call a method,
delete); find or create partner (create, then found again); find or create product (stocked
goods, service); customer invoice with lines, attached PDF, posting, and a rerun that returns it;
a partial payment, its rerun (not paid twice), a payment with the same memo, date and amount on
another invoice (paid), and the rest; vendor bill; sale order with confirmation and its rerun;
stock levels; product prices; partner balance; attach a file and download it; export records;
changes since paged two at a time over all partners, none skipped.

| Odoo | API | Result | Image |
|---|---|---|---|
| 16.0 | XML-RPC | 11/11 passed | `odoo@sha256:0f36a5002a200bb1649771c2cb9403ca5392d7ac4bb23f9ad500b339df3f5a3a` |
| 17.0 | XML-RPC | 11/11 passed | `odoo@sha256:8779f8157ddcc37b84014c94646073f46fd4ad776b7a5d95fc3b0dc73a24a8fa` |
| 18.0 | XML-RPC | 11/11 passed | `odoo@sha256:f61d3e36eb699e63557bbcdf4d7bd659e51418c326009e6574565d87e1d9026d` |
| 19.0 | JSON-2 | 11/11 passed | `odoo@sha256:5e755c725b3c43cf0be16d579904e1418300db3a2c44952f928eaddbcb0cc16d` |
| 19.0 | XML-RPC | 11/11 passed | same |
| 20.0 | JSON-2 | 11/11 passed | `odoo@sha256:a97a7665e2b9fd890e12785491a2c91d779f800bcbc30873dada1c8748b28f1c` |
| 20.0 | XML-RPC | 11/11 passed | same |

PostgreSQL: `postgres@sha256:1149d80408a63791782d07a0e2bd3b06a7adbcdc86e8dcc18d6ee01bacfe04ab`
(15, Odoo 16-19), `postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`
(16-alpine, Odoo 20).

Mismatches found by these runs and fixed:

- XML-RPC `create` takes its values as the first positional argument (`call_kw_model_create`), not
  by name.
- Odoo 16-18 XML-RPC reports a missing model as a warning fault "Object x doesn't exist".
- Odoo's XML-RPC cannot encode a `None` answer; it fails after the method committed, so that fault
  is a success with no result.
- `write_date` is stored with microseconds (every res_partner row in every version) but read
  back to the second; the changes since cursor is built for that.
- Odoo 20 needs PostgreSQL 16 (`any_value`), reads `ir.attachment.raw` as `{filename, content}`, and
  removed `product.pricelist.item.percent_price`.

Not run: demo.odoo.com (Odoo Online 20.0). Its databases answer JSON-2, but creating an API key
there requires Odoo's interactive password re-check (res.users.identitycheck), so a key has to be
made by hand in its web interface.
