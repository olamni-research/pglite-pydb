"""T034 — stable spot-check values drawn from the JannikArndt webshop dump.

Values are derived from the pinned upstream commit
(``78df7422e16f5bf8e21c84da70894b092f799e29``). If the dump is re-vendored,
these numbers need a refresh alongside the new SHA.
"""

from __future__ import annotations


# Row counts — stable under the pinned upstream commit.
PRODUCTS_TOTAL = 1049
ARTICLES_TOTAL = 18522

# Categories present in the dump (subset of the ``public.category`` enum
# that actually has at least one product).
NONEMPTY_CATEGORIES = (
    "Apparel",
    "Footwear",
    "Sportswear",
    "Traditional",
    "Formal Wear",
    "Accessories",
    "Watches & Jewelry",
    "Luggage",
    "Cosmetics",
)

# Products table starts at id=50 (upstream leaves 1..49 unused). The
# first row ``id=50`` is 'Backpack Andiamo' — it has several articles
# across sizes, so pagination tests have rows to return.
PRODUCT_ID_WITH_ARTICLES = 50
PRODUCT_ID_ABSENT = 999_999
