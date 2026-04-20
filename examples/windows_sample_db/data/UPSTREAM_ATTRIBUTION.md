# Upstream Attribution

The file `sample_db.sql` in this directory is a verbatim concatenation of nine
SQL files from the [JannikArndt/PostgreSQLSampleDatabase][1] repository, a
webshop sample database ("1000 customers, 2000 orders, 1000 products with
17730 different articles").

- **Upstream repository**: https://github.com/JannikArndt/PostgreSQLSampleDatabase
- **Pinned commit**: `78df7422e16f5bf8e21c84da70894b092f799e29`
- **Upstream branch at pin**: `master`
- **Upstream author**: Jannik Arndt
- **License status**: No `LICENSE` file exists at the pinned commit. Written
  redistribution permission was obtained directly from the author — see
  [`UPSTREAM_LICENSE`](./UPSTREAM_LICENSE).

## Concatenation order

Follows the upstream `restore.sh` script so loading the concatenated file is
equivalent to running `restore.sh`:

| # | Upstream path              | Upstream size (bytes) |
|--:|----------------------------|----------------------:|
| 1 | `data/create.sql`          | 11,812 |
| 2 | `data/products.sql`        | 76,268 |
| 3 | `data/articles.sql`        | 2,452,524 |
| 4 | `data/labels.sql`          | 35,346 |
| 5 | `data/customer.sql`        | 101,623 |
| 6 | `data/address.sql`         | 87,920 |
| 7 | `data/order.sql`           | 181,551 |
| 8 | `data/order_positions.sql` | 344,532 |
| 9 | `data/stock.sql`           | 815,065 |

## Integrity

- **Concatenated file**: `sample_db.sql` — 4,107,833 bytes
- **SHA-256**: `cb39578f81ca96bcfc79c463440bbbc3be51770212d4c1b5d699e88f23422caa`
- See [`sample_db.sql.sha256`](./sample_db.sql.sha256).

## Regenerating

The one-shot helper [`_vendor_fetch.py`](./_vendor_fetch.py) re-fetches the
nine files from `raw.githubusercontent.com` at the pinned SHA and rewrites
`sample_db.sql` + `sample_db.sql.sha256`. Run it only when intentionally
re-pinning to a new upstream commit.

[1]: https://github.com/JannikArndt/PostgreSQLSampleDatabase
