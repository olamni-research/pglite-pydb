# Phase 1 Data Model

**Feature**: `001-example-db-psycopg3-windows`
**Date**: 2026-04-20

The data model has three layers:
1. **Upstream (immutable)** — tables loaded from the vendored JannikArndt dump. Rows MUST NOT be mutated by any procedure (FR-023).
2. **Example-owned (mutable)** — two tables created by our own overlay schema script, written to by the mutation procedures.
3. **Runtime/config (in-process)** — Python-side dataclasses that hold transport config and fixture state; not stored in the database.

---

## Layer 1 — Upstream sample schema (immutable)

Loaded verbatim from `data/sample_db.sql`. The upstream is the JannikArndt PostgreSQLSampleDatabase webshop dump. Tables live in schema `webshop`; two enum types live in `public`. The example depends on the following logical entities being present:

- **`webshop.products`** — primary entity (`id`, `name`, `labelid`, `category`, `gender`, `currentlyactive`, `created`, `updated`).
- **`webshop.articles`** — a concrete SKU (`id`, `productid`, `ean`, `colorid`, `size`, `description`, `originalprice`, `reducedprice`, …) belonging to a product.
- **`webshop.colors`**, **`webshop.sizes`**, **`webshop.labels`** — lookup tables referenced by articles/products.
- **`webshop.stock`** — stock counts per article.
- **`webshop.customer`** — buyers (`id`, `firstname`, `lastname`, `email`, `gender`, `dateofbirth`, `currentaddressid`, …).
- **`webshop.address`** — shipping/billing addresses linked to customers.
- **`webshop."order"`** — order header (`id`, `customer`, `ordertimestamp`, `shippingaddressid`, `total`, `shippingcost`, …). Note the quoted identifier because `order` is a reserved word.
- **`webshop.order_positions`** — line items (`id`, `orderid`, `articleid`, `amount`, `price`, …).
- Enums **`public.category`** (Apparel, Footwear, Sportswear, Traditional, Formal Wear, Accessories, Watches & Jewelry, Luggage, Cosmetics) and **`public.gender`** (male, female, unisex).

All 10 procedures `SET search_path = webshop, public` so unqualified references resolve first to `webshop.*` and then to the overlay tables / enum types in `public`.

**Invariant**: No `INSERT`, `UPDATE`, or `DELETE` on any `webshop.*` table after the initial load. Enforcement: `audit_log` and `product_overlay` are the only writable targets the role `example_user` is granted `INSERT`/`UPDATE` on (see Layer 2 grants). Read-only grant is sufficient for Layer 1.

---

## Layer 2 — Example-owned overlay schema (mutable)

Created by `sql/00_schema_overlay.sql` on first run, inside the same database as Layer 1.

### `audit_log`

Append-only log table. Written to by `bulk_log_articles_for_product`. Lives in schema `public`.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial PRIMARY KEY` | Auto-assigned. |
| `event_at` | `timestamptz NOT NULL DEFAULT now()` | When the row was logged. |
| `procedure_name` | `text NOT NULL` | Which procedure wrote the row (for debuggability). |
| `target_key` | `text NOT NULL` | Identifier of the entity the procedure acted on (e.g. product id rendered as text). |
| `payload` | `jsonb NOT NULL DEFAULT '{}'::jsonb` | Free-form extra detail (e.g. `{ean, description}` for article log rows). |

**Indexes**: `(procedure_name, event_at DESC)` for chronological lookups in tests.
**Lifecycle**: truncated on explicit reset (`run_example.py --reset`); otherwise grows monotonically.

### `product_overlay`

Key/value overlay of replacement display names. Written to by `rename_product_display_name`. Lives in schema `public`.

| Column | Type | Notes |
|---|---|---|
| `product_id` | `integer PRIMARY KEY` | References `webshop.products.id` (logical, not FK — upstream rows immutable). |
| `display_name` | `text NOT NULL` | Replacement name. |
| `updated_at` | `timestamptz NOT NULL DEFAULT now()` | Last write time. |

**Upsert**: the procedure uses `INSERT ... ON CONFLICT (product_id) DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = now()`.
**Lifecycle**: truncated on explicit reset.

### Role grants

- `example_user` has `USAGE` on `public` and `webshop`, `SELECT` on every upstream table in those schemas, and `SELECT, INSERT, UPDATE, DELETE` on `audit_log` + `product_overlay`.
- No role other than `example_user` is granted anything (FR-021 enforcement at the bridge layer — R6).

---

## Layer 3 — Runtime / in-process config

Python dataclasses in `examples/windows_sample_db/transport.py` and `examples/windows_sample_db/launcher.py`.

### `TransportConfig`

```python
@dataclass(frozen=True)
class TransportConfig:
    kind: Literal["tcp", "pipe"]
    host: str = "127.0.0.1"          # tcp only
    port: int = 54320                # tcp only, default chosen to avoid 5432 collision
    pipe_name: str = "pglite_example" # pipe only
    unique_pipe: bool = False        # if True, pipe_name is suffixed with pid+uuid
    data_dir: Path = ...             # repo-relative default filled by factory
```

Validation rules:
- `kind == "tcp"` ⇒ `host` and `port` must be set; `pipe_name` and `unique_pipe` ignored but allowed.
- `kind == "pipe"` ⇒ `pipe_name` must be non-empty and must not contain `\\`; `host`/`port` ignored.
- `data_dir` must be absolute; if it does not exist, it is created on first use.

### `LoaderState`

```python
@dataclass
class LoaderState:
    data_dir: Path
    dump_sha256: str            # expected checksum from .sha256 sidecar
    fresh_load_required: bool   # True if data_dir lacks PG_VERSION
    procedures_installed: bool  # True if the procedure-install marker file is present
```

Derived at startup; drives whether `loader.load()` re-runs FR-001/FR-002 work.

### `ProcedureInvocation`

```python
@dataclass(frozen=True)
class ProcedureInvocation:
    name: str                       # One of the 10 catalog names
    args: tuple[Any, ...]
    expect_error: str | None = None # If set, test asserts this SQLSTATE is raised
```

Used by the test-matrix fixture to drive happy-path and negative-path assertions against every procedure on every transport.

---

## State transitions

Data directory lifecycle:

```
[no directory]
    └─(first run)──► [initialized, dump loaded, procedures installed]
                          │
                          ├─(next run)──────► [warm, reused as-is]
                          │
                          ├─(--reset)───────► [wiped, re-enters "no directory"]
                          │
                          └─(dump-tampered)─► [refuse to start, report mismatch]
```

All transitions are driven by `loader.py` at process start; no background workers.
