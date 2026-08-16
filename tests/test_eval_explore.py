"""eval_explore.py harness tests: the eval schema carries edge_integrity,
defaulted to "unverified", and it's evaluation metadata only -- it doesn't
feed or alter scoring.

A small (film, credits, person, title_lookup) fixture stands in for
film.duckdb, injected via run()'s con/seeds parameters so this never
touches the real 429 MB database.
"""

import duckdb
import pytest

from cde.explore import explore
from eval_explore import EDGE_INTEGRITY_DEFAULT, run

SEEDS = [("Fixture Film", 2000)]


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR,
            imdb_votes INTEGER
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?, ?)", [
        ("tt3000", "Fixture Film", 2000, "Drama", 100),
        ("tt3001", "Linked Picture", 2001, "Drama", 50),
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", [
        ("tt3000", "nm4001", "cinematographer", 1),
        ("tt3001", "nm4001", "cinematographer", 1),
    ])

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.execute("INSERT INTO person VALUES ('nm4001', 'Fixture DP', NULL, NULL)")

    con.execute("""
        CREATE TABLE title_lookup (
            tconst VARCHAR, title VARCHAR, year INTEGER, source VARCHAR
        )
    """)
    con.executemany("INSERT INTO title_lookup VALUES (?, ?, ?, 'primary')", [
        ("tt3000", "Fixture Film", 2000),
        ("tt3001", "Linked Picture", 2001),
    ])
    return con


@pytest.fixture
def con_eval():
    con = _build_db()
    yield con
    con.close()


def _parse(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("|") or line.startswith("|---") or "seed" in line[:8]:
                continue
            cells = [c.strip() for c in line.strip("\n").strip("|").split("|")]
            rows.append(cells)
    return rows


def test_eval_schema_has_edge_integrity_column(con_eval, tmp_path):
    out_path = run(n=5, out_path=tmp_path / "out.md", con=con_eval, seeds=SEEDS)
    lines = open(out_path, encoding="utf-8").readlines()
    header_line = next(li for li in lines if li.startswith("| seed"))
    assert "edge_integrity" in header_line
    assert "judgment" in header_line


def test_eval_edge_integrity_defaults_unverified(con_eval, tmp_path):
    out_path = run(n=5, out_path=tmp_path / "out.md", con=con_eval, seeds=SEEDS)
    rows = _parse(out_path)
    assert len(rows) == 1
    seed, rank, result, tconst, score, explanation, judgment, edge_integrity = rows[0]
    assert rank == "1"
    assert tconst == "tt3001"
    assert judgment == ""
    assert edge_integrity == EDGE_INTEGRITY_DEFAULT


def test_eval_edge_integrity_does_not_feed_scoring(con_eval, tmp_path):
    """The written score is exactly explore()'s own score -- the
    edge_integrity column is metadata bolted on afterward, never plumbed
    back into the engine."""
    out_path = run(n=5, out_path=tmp_path / "out.md", con=con_eval, seeds=SEEDS)
    rows = _parse(out_path)
    written_score = float(rows[0][4])

    direct = explore(con_eval, "tt3000", n=5)
    assert direct["results"][0]["score"] == pytest.approx(written_score, abs=1e-4)
