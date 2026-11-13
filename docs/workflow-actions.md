# Odoo workflow actions

Choose an everyday action for common invoices, clients, products and payments.
Choose **Advanced ·** actions when you need a particular provider method or record shape.
Action IDs in parentheses remain compatible with existing workflows.

## Connect and check access

Create an API key for the Odoo user whose access the workflow needs. Add a project connection for the server and API mode; XML-RPC also needs database and login. Select the supported Odoo version or allow detection as configured. Run **Test the connection** (`test_connection`). Installed modules and user permissions determine which models and fields exist.

## Create an invoice from an approved order

1. Add **Create customer invoice** (`create_customer_invoice`) and select the project connection.
2. Set **Partner id** from an earlier partner lookup, or supply the partner reference, VAT or email. When creating a missing partner, also supply its name. Set **Reference** to your source invoice reference and **Date** to `2026-01-15`.
3. Set **Lines** to a JSON array, binding values from earlier steps as needed:

   ```json
   [{"product_code":"PRODUCT01","quantity":2,"price_unit":12.5}]
   ```

Replace PRODUCT01 with an existing product internal reference. The product supplies company-specific accounts and taxes unless overridden. Leave **Post** disabled to create a draft; enable it only when the invoice should be posted. With a reference, a rerun matches the existing invoice for the same partner and date. For partial payments, give **Register payment** a stable memo: repeating a partial payment without a memo registers it again.

Use the purchase invoice/vendor bill action for supplier documents; check its
partner, product and tax settings before execution. File inputs accept workflow
files from earlier steps. Download actions return workflow files for later email,
archive or document steps, rather than local filesystem paths.

## Read stock, prices, balances and changes

- **Read stock levels** (`stock_levels`): select a product and the warehouse/location filters needed for the company.
- **Read product prices** (`product_prices`): read product pricing; client/pricelist options depend on the provider.
- **Read partner balance** (`partner_balance`): read existing debt/balance information before a follow-up workflow.
- **Read records changed since a date** (`changes_since`): choose **What** and set **Since** to `2026-01-01 00:00:00`.

Bind `next_since` to **Since** and `next_after_id` to **After id** on the next read. Match repeated boundary-second records by `id`; do not advance only the timestamp.

**Advanced · Export records to a JSONL file** (`export_records`) reads records matching a model/domain into a workflow file. Select only the fields required downstream and follow the returned continuation information for incomplete exports.

## Work with advanced records

Use model actions for **Search records**, reads, counts, groups and field descriptions when an everyday action does not cover the model. Model names use Odoo technical names, such as `res.partner`; domains are JSON arrays, for example `[["is_company","=",true]]`. Field names, domains and public methods must exist in the connected Odoo version and installed modules. **Advanced · Call a model method** can run public methods such as `action_post`; it may change records.

Read actions still contact the provider during a workflow test. Write actions are
suppressed in test mode. A lost response after a write can leave an unknown
outcome; inspect provider records and reported effects before running it again.
