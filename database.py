"""Sample database, schema description, and a read-only query runner.

This module is the data foundation for the AI Data Analyst. It seeds a small,
deterministic SQLite demo store, exposes a schema description for the LLM, and
runs SELECT queries under a read-only connection.

Safety invariant: execution is read-only. `run_select` opens the DB with
`mode=ro`, so the SQLite engine itself rejects any write. There is no keyword
denylist — the only textual checks are structural (must be a single SELECT/WITH
statement) and exist purely to return a friendly error, not to enforce safety.
"""

import os
import sqlite3

DB_PATH = "store.db"
MAX_ROWS = 1000


def build_sample_db(path=DB_PATH):
    """Create the demo database from scratch.

    WARNING: this is destructive. It DROPs the four tables and rewrites the
    file at `path`, discarding any existing data. Entry points should call
    `ensure_sample_db` (seed-if-missing) instead, to avoid clobbering a real DB.

    All rows are fixed literals (no randomness) so results are reproducible.
    """
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()

        cur.executescript(
            """
            DROP TABLE IF EXISTS order_items;
            DROP TABLE IF EXISTS orders;
            DROP TABLE IF EXISTS products;
            DROP TABLE IF EXISTS customers;

            CREATE TABLE customers (
                id          INTEGER PRIMARY KEY,
                name        TEXT    NOT NULL,
                region      TEXT    NOT NULL,
                signup_date TEXT    NOT NULL
            );

            CREATE TABLE products (
                id         INTEGER PRIMARY KEY,
                name       TEXT    NOT NULL,
                category   TEXT    NOT NULL,
                unit_price REAL    NOT NULL
            );

            CREATE TABLE orders (
                id          INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                order_date  TEXT    NOT NULL,
                status      TEXT    NOT NULL
            );

            CREATE TABLE order_items (
                id         INTEGER PRIMARY KEY,
                order_id   INTEGER NOT NULL REFERENCES orders(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity   INTEGER NOT NULL,
                unit_price REAL    NOT NULL
            );
            """
        )

        # 5 customers across 3 regions
        customers = [
            (1, "Ava Thompson",   "North", "2023-01-15"),
            (2, "Liam Chen",      "South", "2023-02-03"),
            (3, "Noah Patel",     "West",  "2023-02-20"),
            (4, "Emma Rodriguez", "North", "2023-03-11"),
            (5, "Olivia Kim",     "South", "2023-04-07"),
        ]
        cur.executemany(
            "INSERT INTO customers (id, name, region, signup_date) VALUES (?, ?, ?, ?)",
            customers,
        )

        # 5 products across 3 categories
        products = [
            (1, "Wireless Mouse",   "Electronics", 25.00),
            (2, "Mechanical Keyboard", "Electronics", 80.00),
            (3, "Desk Lamp",        "Home",        35.00),
            (4, "Notebook",         "Stationery",  4.50),
            (5, "Standing Desk",    "Home",        220.00),
        ]
        cur.executemany(
            "INSERT INTO products (id, name, category, unit_price) VALUES (?, ?, ?, ?)",
            products,
        )

        # 8 orders; order 4 is cancelled (excluded from revenue)
        orders = [
            (1, 1, "2023-05-01", "completed"),
            (2, 2, "2023-05-03", "completed"),
            (3, 3, "2023-05-10", "completed"),
            (4, 1, "2023-05-12", "cancelled"),
            (5, 4, "2023-06-01", "completed"),
            (6, 5, "2023-06-05", "completed"),
            (7, 2, "2023-06-18", "completed"),
            (8, 3, "2023-07-02", "completed"),
        ]
        cur.executemany(
            "INSERT INTO orders (id, customer_id, order_date, status) VALUES (?, ?, ?, ?)",
            orders,
        )

        # 15 order_items; unit_price snapshotted per line (revenue = quantity * unit_price)
        order_items = [
            (1,  1, 1, 2, 25.00),
            (2,  1, 4, 5, 4.50),
            (3,  2, 2, 1, 80.00),
            (4,  2, 3, 2, 35.00),
            (5,  3, 5, 1, 220.00),
            (6,  3, 1, 1, 25.00),
            (7,  4, 2, 1, 80.00),   # belongs to cancelled order 4
            (8,  4, 3, 1, 35.00),   # belongs to cancelled order 4
            (9,  5, 4, 10, 4.50),
            (10, 5, 1, 3, 25.00),
            (11, 6, 3, 1, 35.00),
            (12, 6, 5, 1, 220.00),
            (13, 7, 2, 2, 80.00),
            (14, 8, 1, 4, 25.00),
            (15, 8, 4, 6, 4.50),
        ]
        cur.executemany(
            "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) "
            "VALUES (?, ?, ?, ?, ?)",
            order_items,
        )

        conn.commit()
    finally:
        conn.close()


def ensure_sample_db(path=DB_PATH):
    """Seed the demo DB only if it does not already exist (no-clobber).

    This is the safe entry point: it never overwrites an existing database.
    """
    if not os.path.exists(path):
        build_sample_db(path)


def get_schema_text():
    """Return a plain-text schema description for the LLM prompts."""
    return (
        "Tables:\n"
        "  customers(id, name, region, signup_date)\n"
        "  products(id, name, category, unit_price)\n"
        "  orders(id, customer_id, order_date, status)\n"
        "  order_items(id, order_id, product_id, quantity, unit_price)\n"
        "\n"
        "Relationships:\n"
        "  orders.customer_id -> customers.id\n"
        "  order_items.order_id -> orders.id\n"
        "  order_items.product_id -> products.id\n"
        "\n"
        "Notes:\n"
        "  - Revenue is computed at the order_items grain: revenue = quantity * unit_price.\n"
        "  - Exclude cancelled orders (orders.status = 'cancelled') from revenue and sales.\n"
    )


def run_select(sql, path=DB_PATH):
    """Run a single read-only SELECT/WITH query and return (columns, rows).

    Safety is enforced by the read-only connection (`mode=ro`); the textual
    checks below are structural only (single SELECT/WITH statement) and just
    produce friendly errors — they are NOT a keyword denylist.
    """
    stripped = sql.strip().rstrip(";").strip()

    first_token = stripped.split(None, 1)[0].lower() if stripped else ""
    if first_token not in ("select", "with"):
        raise ValueError("Only read-only SELECT/WITH queries are allowed.")

    # After removing a single trailing ';', any remaining ';' means stacked statements.
    if ";" in stripped:
        raise ValueError("Multiple statements are not allowed; run a single query.")

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(stripped)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS)
        return columns, rows
    finally:
        conn.close()
