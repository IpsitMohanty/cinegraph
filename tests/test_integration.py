"""Regression pin: confirms the refactored package reproduces the numbers
from the reconstruction run that it replaces. Runs only when the real
data/processed/film.duckdb is present (never in CI -- it's gitignored and
~429 MB).

Asserts invariants that survive IMDb's daily dataset refresh, not brittle
exact counts.
"""

import duckdb
import pytest

from cde.config import DB_PATH
from conftest import lookup_reconciles

pytestmark = pytest.mark.integration

skip_reason = f"{DB_PATH} not present -- run `python -m cde.cli build` first"


@pytest.fixture(scope="module")
def real_con():
    if not DB_PATH.exists():
        pytest.skip(skip_reason)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    yield con
    con.close()


def test_title_lookup_reconciles(real_con):
    assert lookup_reconciles(real_con)


def test_rated_fraction_within_band(real_con):
    total = real_con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    rated = real_con.execute(
        "SELECT COUNT(*) FROM film WHERE imdb_rating IS NOT NULL"
    ).fetchone()[0]
    fraction = rated / total
    assert 0.44 <= fraction <= 0.48


def test_shawshank_in_top_3_by_votes(real_con):
    top3 = real_con.execute("""
        SELECT primaryTitle FROM film
        ORDER BY imdb_votes DESC NULLS LAST
        LIMIT 3
    """).fetchall()
    titles = {row[0] for row in top3}
    assert "The Shawshank Redemption" in titles


def test_film_count_within_tolerance_band(real_con):
    # 700k-800k: a tolerance band around the verified 744,866, since IMDb's
    # dataset is refreshed daily and the exact count will drift over time.
    total = real_con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    assert 700_000 <= total <= 800_000
