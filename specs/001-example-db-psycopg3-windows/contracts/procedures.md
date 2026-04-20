# Stored Procedure Contracts

**Feature**: `001-example-db-psycopg3-windows`
**SQL source**: `examples/windows_sample_db/sql/10_procedures.sql`
**Naming**: All procedures live in the `public` schema. They `SET search_path = webshop, public` so they find the vendored `webshop.*` tables and the example-owned overlay tables (which live in `public`).
**Ownership**: Owned by the superuser that loaded the schema; `EXECUTE` is granted to `example_user`.

The upstream dump is the JannikArndt PostgreSQLSampleDatabase (a fashion webshop): tables `webshop.{colors, sizes, labels, products, articles, stock, customer, address, order, order_positions}`, plus enums `public.category` (Apparel, Footwear, Sportswear, Traditional, Formal Wear, Accessories, Watches & Jewelry, Luggage, Cosmetics) and `public.gender`.

Every entry below is the test contract: tests MUST assert the happy-path signature *and* the documented error code.

---

## 1. `get_customer_by_email(p_email text) RETURNS SETOF webshop.customer`

- **Returns**: 0 or 1 row from `webshop.customer` whose `email` matches `p_email` (case-insensitive compare).
- **Happy path**: `get_customer_by_email('<known email>')` returns that customer's row.
- **Errors**: `RAISE EXCEPTION USING ERRCODE = 'P0002'` if no row is found. Tests assert psycopg raises the matching `lookup_error` class.

## 2. `list_articles_for_product(p_product_id int, p_page_size int, p_page_number int) RETURNS TABLE(article_id int, ean text, color_id int, size int, description text, total_count bigint)`

- **Returns**: Paginated articles for a product. `p_page_number` is 1-based. `total_count` is the full match count (repeated per row) so pagination UI has it.
- **Happy path**: `(product_id=1, size=10, page=1)` returns up to 10 rows with `total_count >= row_count`.
- **Errors**: `RAISE EXCEPTION USING ERRCODE = '22023'` if `p_page_size <= 0` or `p_page_number <= 0`. `P0002` if `p_product_id` does not exist in `webshop.products`.

## 3. `count_articles_per_product() RETURNS TABLE(product_id int, article_count bigint)`

- **Returns**: One row per product that has at least one article, with `COUNT(*)` of `webshop.articles`. Aggregated via `GROUP BY`. Ordered by `product_id`.
- **Happy path**: Every `productid` that appears in `webshop.articles` appears once with `article_count >= 1`.
- **Errors**: None expected (no inputs).

## 4. `top_products_by_revenue(p_n int) RETURNS TABLE(product_id int, product_name text, revenue numeric)`

- **Returns**: Top `p_n` products ordered by realized revenue (`SUM(op.price::numeric * op.amount)`) across `webshop.order_positions`, joined via `webshop.articles` to `webshop.products`. Tie-break by `product_id ASC`. `revenue` is in the dump's monetary unit, cast to `numeric` for test-friendly comparison.
- **Happy path**: `(n=5)` returns exactly 5 rows; `revenue` strictly descending (modulo ties).
- **Errors**: `RAISE EXCEPTION USING ERRCODE = '22023'` if `p_n <= 0` or `p_n > 500`.

## 5. `list_orders_for_customer(p_customer_id int) RETURNS TABLE(order_id int, ordered_at timestamptz, total numeric)`

- **Returns**: All rows from `webshop."order"` with `customer = p_customer_id`, newest first. `total` cast to `numeric`.
- **Happy path**: `(customer_id=<known id>)` returns a non-empty set for a customer with orders, empty set for a customer with none.
- **Errors**: `P0002` if `p_customer_id` does not exist in `webshop.customer`. Empty result for a valid customer with zero orders is NOT an error.

## 6. `articles_in_category(p_category text) RETURNS TABLE(article_id int, product_id int, product_name text, description text)`

- **Returns**: All articles whose parent product's `category` equals `p_category` (matched against `public.category` enum). Ordered by `product_id, article_id`.
- **Happy path**: `(category='Footwear')` returns a non-empty set.
- **Errors**: `RAISE EXCEPTION USING ERRCODE = '22023'` if `p_category` is NULL, empty, or not a member of `public.category`'s enum labels (validated via `pg_enum` lookup before cast, so an unknown label produces our typed error rather than the engine's `22P02`).

## 7. `customer_order_report(p_customer_id int) RETURNS TABLE(customer_id int, email text, order_count int, total_spent numeric)`

- **Returns**: Single-row consolidated profile. Joins `webshop.customer` with aggregates over `webshop."order"`. `total_spent` sums `order.total` cast to `numeric`; returns `0` when the customer has no orders.
- **Happy path**: `(customer_id=<known id>)` returns one row; `order_count >= 0`.
- **Errors**: `P0002` if `p_customer_id` is not a known customer.

## 8. `bulk_log_articles_for_product(p_product_id int) RETURNS int`

- **Side effect**: Inserts one row into `audit_log` for every article in `webshop.articles` for the given product, with `procedure_name = 'bulk_log_articles_for_product'`, `target_key = p_product_id::text`, and `payload` containing `{ean, description}` of the article.
- **Returns**: The number of rows inserted.
- **Happy path**: `(product_id=1)` returns a count equal to the number of `webshop.articles` rows with `productid = 1`; subsequent `SELECT count(*) FROM audit_log WHERE target_key = '1' AND procedure_name = 'bulk_log_articles_for_product'` equals that count.
- **Errors**: `P0002` if `p_product_id` is not a known product; no partial writes on error (wrapped in a single transaction).

## 9. `rename_product_display_name(p_product_id int, p_new_name text) RETURNS void`

- **Side effect**: Upserts into `product_overlay` (`INSERT ... ON CONFLICT (product_id) DO UPDATE`). Does **not** touch `webshop.products` (FR-023).
- **Returns**: `void`. Callers inspect `product_overlay` to confirm.
- **Happy path**: `(product_id=1, new_name='Classic Runner')` → `product_overlay.display_name = 'Classic Runner'`, `updated_at` bumped.
- **Errors**: `P0002` if `p_product_id` is not a known product; `22023` if `p_new_name` is empty or > 200 chars.

## 10. `assert_product_exists(p_product_id int) RETURNS void`

- **Side effect**: None.
- **Returns**: `void` on success.
- **Happy path**: `(product_id=1)` returns cleanly.
- **Errors**: **Always raises** `RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'product not found: %', DETAIL = ...` when the product is absent. This is the spec's "intentional error demonstrator" procedure (spec catalog item 10); tests MUST verify that psycopg receives a typed exception carrying the SQLSTATE and message intact.

---

## Cross-cutting contracts

- **search_path**: Every function body executes under `SET search_path = webshop, public` so unqualified references resolve to `webshop.*` tables first and the overlay tables / enum types in `public` next.
- **Transport equivalence (spec SC-003)**: For every procedure above, invoking it over TCP and over the named pipe with identical inputs MUST return identical result sets (same row count, same column values, same scalar returns). The test matrix enforces this.
- **Role scoping (FR-021)**: Every procedure must be callable by `example_user`. `EXECUTE` grants are issued at install time in `10_procedures.sql`.
- **Upstream immutability (FR-023)**: The only tables that mutation procedures (`#8`, `#9`) touch are `audit_log` and `product_overlay`. A post-test assertion compares the `md5` of key upstream tables (`webshop.products`, `webshop.articles`, `webshop.customer`, `webshop."order"`, `webshop.order_positions`) before and after the suite to prove this invariant.
- **Error taxonomy**: All expected errors raise a SQLSTATE the client can match on — either `P0002` (no_data_found-style) or `22023` (invalid_parameter_value). No generic `RAISE EXCEPTION` without an `ERRCODE` is allowed in `10_procedures.sql`.
