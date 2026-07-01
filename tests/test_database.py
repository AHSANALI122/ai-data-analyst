"""Read-only execution guard and no-clobber seeding (invariants #3 and #6)."""

import os

import pytest

from database import DB_PATH, ensure_sample_db, run_select


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE customers SET name = 'x'",
        "DROP TABLE customers",
        "INSERT INTO customers (id, name, region, signup_date) VALUES (99,'x','N','2023')",
        "SELECT 1; DROP TABLE customers",  # stacked statements
    ],
)
def test_run_select_blocks_writes_and_stacked(sql):
    with pytest.raises(ValueError):
        run_select(sql)


def test_run_select_allows_valid_select():
    columns, rows = run_select("SELECT region, COUNT(*) FROM customers GROUP BY region")
    assert columns[0] == "region"
    assert rows


def test_keyword_in_literal_is_allowed():
    """No denylist false positives: a keyword inside a string literal is fine."""
    columns, rows = run_select("SELECT 'please create x' AS s")
    assert rows == [("please create x",)]


def test_ensure_sample_db_does_not_clobber():
    ensure_sample_db()  # already seeded by the session fixture
    before = os.path.getmtime(DB_PATH)
    ensure_sample_db()
    assert os.path.getmtime(DB_PATH) == before
