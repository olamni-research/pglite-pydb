-- Feature 001-example-db-psycopg3-windows
-- The 10 stored procedures (implemented as PL/pgSQL functions -- see note below).
-- Contract reference: specs/001-example-db-psycopg3-windows/contracts/procedures.md
--
-- Upstream schema: JannikArndt PostgreSQLSampleDatabase webshop dump.
-- Tables: webshop.{colors, sizes, labels, products, articles, stock,
--                  customer, address, order, order_positions}
-- Enums:  public.category, public.gender
--
-- Every function SETs search_path = webshop, public so unqualified references
-- resolve to the dump's webshop.* tables first, then to the overlay tables
-- (audit_log, product_overlay) and enum types in public.
--
-- NOTE: Postgres CREATE PROCEDURE can't return SETOF / TABLE directly. The
-- catalog calls these "stored procedures" in the colloquial sense; we
-- implement them as FUNCTIONs so psycopg 3 can consume rowset results via
-- SELECT * FROM proc(...). The 10 contract entries map 1-to-1 to the
-- CREATE FUNCTION statements below.

-- 1. get_customer_by_email -----------------------------------------------------
CREATE OR REPLACE FUNCTION get_customer_by_email(p_email text)
RETURNS SETOF webshop.customer
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_email text := lower(coalesce(p_email, ''));
BEGIN
    RETURN QUERY
        SELECT c.*
        FROM   webshop.customer c
        WHERE  lower(c.email) = v_email;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'customer not found: %', p_email
            USING ERRCODE = 'P0002',
                  DETAIL  = 'No customer row matches the given email.';
    END IF;
END
$$;

-- 2. list_articles_for_product -------------------------------------------------
CREATE OR REPLACE FUNCTION list_articles_for_product(
    p_product_id  int,
    p_page_size   int,
    p_page_number int
)
RETURNS TABLE (
    article_id  int,
    ean         text,
    color_id    int,
    size        int,
    description text,
    total_count bigint
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_total  bigint;
    v_offset int;
    v_exists boolean;
BEGIN
    IF p_page_size IS NULL OR p_page_size <= 0 THEN
        RAISE EXCEPTION 'invalid page_size: %', p_page_size
            USING ERRCODE = '22023';
    END IF;
    IF p_page_number IS NULL OR p_page_number <= 0 THEN
        RAISE EXCEPTION 'invalid page_number: %', p_page_number
            USING ERRCODE = '22023';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM webshop.products p WHERE p.id = p_product_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'product not found: %', p_product_id
            USING ERRCODE = 'P0002';
    END IF;

    v_offset := (p_page_number - 1) * p_page_size;

    SELECT count(*) INTO v_total
    FROM   webshop.articles a
    WHERE  a.productid = p_product_id;

    RETURN QUERY
        SELECT a.id, a.ean, a.colorid, a.size, a.description, v_total
        FROM   webshop.articles a
        WHERE  a.productid = p_product_id
        ORDER  BY a.id
        LIMIT  p_page_size
        OFFSET v_offset;
END
$$;

-- 3. count_articles_per_product ------------------------------------------------
CREATE OR REPLACE FUNCTION count_articles_per_product()
RETURNS TABLE (
    product_id    int,
    article_count bigint
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
BEGIN
    RETURN QUERY
        SELECT a.productid    AS product_id,
               count(*)       AS article_count
        FROM   webshop.articles a
        WHERE  a.productid IS NOT NULL
        GROUP  BY a.productid
        ORDER  BY a.productid;
END
$$;

-- 4. top_products_by_revenue ---------------------------------------------------
CREATE OR REPLACE FUNCTION top_products_by_revenue(p_n int)
RETURNS TABLE (
    product_id   int,
    product_name text,
    revenue      numeric
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
BEGIN
    IF p_n IS NULL OR p_n <= 0 OR p_n > 500 THEN
        RAISE EXCEPTION 'invalid n: % (must be 1..500)', p_n
            USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
        SELECT p.id                                               AS product_id,
               p.name                                             AS product_name,
               COALESCE(SUM((op.price::numeric) * op.amount), 0)  AS revenue
        FROM   webshop.products         p
        JOIN   webshop.articles         a  ON a.productid  = p.id
        JOIN   webshop.order_positions  op ON op.articleid = a.id
        GROUP  BY p.id, p.name
        ORDER  BY revenue DESC NULLS LAST, p.id ASC
        LIMIT  p_n;
END
$$;

-- 5. list_orders_for_customer --------------------------------------------------
CREATE OR REPLACE FUNCTION list_orders_for_customer(p_customer_id int)
RETURNS TABLE (
    order_id   int,
    ordered_at timestamptz,
    total      numeric
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_exists boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM webshop.customer c WHERE c.id = p_customer_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'customer not found: %', p_customer_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN QUERY
        SELECT o.id              AS order_id,
               o.ordertimestamp  AS ordered_at,
               o.total::numeric  AS total
        FROM   webshop."order" o
        WHERE  o.customer = p_customer_id
        ORDER  BY o.ordertimestamp DESC, o.id DESC;
END
$$;

-- 6. articles_in_category ------------------------------------------------------
CREATE OR REPLACE FUNCTION articles_in_category(p_category text)
RETURNS TABLE (
    article_id   int,
    product_id   int,
    product_name text,
    description  text
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_valid  boolean;
    v_cat    public.category;
BEGIN
    IF p_category IS NULL OR length(p_category) = 0 THEN
        RAISE EXCEPTION 'invalid category: %', p_category
            USING ERRCODE = '22023';
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM   pg_enum e
        JOIN   pg_type t ON t.oid = e.enumtypid
        JOIN   pg_namespace n ON n.oid = t.typnamespace
        WHERE  n.nspname = 'public'
        AND    t.typname = 'category'
        AND    e.enumlabel = p_category
    ) INTO v_valid;

    IF NOT v_valid THEN
        RAISE EXCEPTION 'invalid category: % (not a member of public.category)', p_category
            USING ERRCODE = '22023';
    END IF;

    v_cat := p_category::public.category;

    RETURN QUERY
        SELECT a.id          AS article_id,
               p.id          AS product_id,
               p.name        AS product_name,
               a.description AS description
        FROM   webshop.articles a
        JOIN   webshop.products p ON p.id = a.productid
        WHERE  p.category = v_cat
        ORDER  BY p.id, a.id;
END
$$;

-- 7. customer_order_report -----------------------------------------------------
CREATE OR REPLACE FUNCTION customer_order_report(p_customer_id int)
RETURNS TABLE (
    customer_id int,
    email       text,
    order_count int,
    total_spent numeric
)
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    r_customer webshop.customer%ROWTYPE;
BEGIN
    SELECT * INTO r_customer
    FROM   webshop.customer c
    WHERE  c.id = p_customer_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'customer not found: %', p_customer_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN QUERY
        SELECT
            r_customer.id,
            r_customer.email,
            (SELECT count(*)::int
                FROM webshop."order" o WHERE o.customer = p_customer_id),
            (SELECT COALESCE(SUM(o.total::numeric), 0)
                FROM webshop."order" o WHERE o.customer = p_customer_id);
END
$$;

-- 8. bulk_log_articles_for_product ---------------------------------------------
CREATE OR REPLACE FUNCTION bulk_log_articles_for_product(p_product_id int)
RETURNS int
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_exists   boolean;
    v_inserted int;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM webshop.products p WHERE p.id = p_product_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'product not found: %', p_product_id
            USING ERRCODE = 'P0002';
    END IF;

    WITH rows_inserted AS (
        INSERT INTO audit_log (procedure_name, target_key, payload)
        SELECT 'bulk_log_articles_for_product',
               p_product_id::text,
               jsonb_build_object(
                   'ean',         a.ean,
                   'description', a.description
               )
        FROM   webshop.articles a
        WHERE  a.productid = p_product_id
        RETURNING 1
    )
    SELECT count(*)::int INTO v_inserted FROM rows_inserted;

    RETURN v_inserted;
END
$$;

-- 9. rename_product_display_name -----------------------------------------------
CREATE OR REPLACE FUNCTION rename_product_display_name(
    p_product_id int,
    p_new_name   text
)
RETURNS void
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_exists boolean;
BEGIN
    IF p_new_name IS NULL OR length(p_new_name) = 0 OR length(p_new_name) > 200 THEN
        RAISE EXCEPTION 'invalid new_name: length=% (must be 1..200)',
                        coalesce(length(p_new_name), 0)
            USING ERRCODE = '22023';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM webshop.products p WHERE p.id = p_product_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'product not found: %', p_product_id
            USING ERRCODE = 'P0002';
    END IF;

    INSERT INTO product_overlay (product_id, display_name)
    VALUES (p_product_id, p_new_name)
    ON CONFLICT (product_id) DO UPDATE
        SET display_name = EXCLUDED.display_name,
            updated_at   = now();
END
$$;

-- 10. assert_product_exists ----------------------------------------------------
CREATE OR REPLACE FUNCTION assert_product_exists(p_product_id int)
RETURNS void
LANGUAGE plpgsql
SET search_path = webshop, public
AS $$
DECLARE
    v_exists boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM webshop.products p WHERE p.id = p_product_id
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'product not found: %', p_product_id
            USING ERRCODE = 'P0002',
                  DETAIL  = 'assert_product_exists: given id is absent from webshop.products.';
    END IF;
END
$$;

-- ============================================================================
-- Grants: every procedure is callable by example_user.
-- ============================================================================

GRANT EXECUTE ON FUNCTION get_customer_by_email(text)                       TO example_user;
GRANT EXECUTE ON FUNCTION list_articles_for_product(int, int, int)          TO example_user;
GRANT EXECUTE ON FUNCTION count_articles_per_product()                      TO example_user;
GRANT EXECUTE ON FUNCTION top_products_by_revenue(int)                      TO example_user;
GRANT EXECUTE ON FUNCTION list_orders_for_customer(int)                     TO example_user;
GRANT EXECUTE ON FUNCTION articles_in_category(text)                        TO example_user;
GRANT EXECUTE ON FUNCTION customer_order_report(int)                        TO example_user;
GRANT EXECUTE ON FUNCTION bulk_log_articles_for_product(int)                TO example_user;
GRANT EXECUTE ON FUNCTION rename_product_display_name(int, text)            TO example_user;
GRANT EXECUTE ON FUNCTION assert_product_exists(int)                        TO example_user;
