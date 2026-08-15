"""cde.tuning tests: parsing, the two pure crosstab aggregations, and the
DB-dependent same-director / temporal-implausibility checks against a
small hand-built (film, credits, person) fixture.
"""

import duckdb
import pytest

from cde.tuning import (
    annotate_same_director,
    annotate_year_gap,
    build_crosstab_A,
    build_crosstab_B,
    is_same_director,
    is_temporally_implausible,
    parse_labeled_eval,
    year_gap_bucket,
)

# --------------------------------------------------------------------------
# Fixture db
# --------------------------------------------------------------------------


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR,
            imdb_votes INTEGER
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?, ?)", [
        ("tt2000", "Old Silent Epic", 1925, "Drama", 100),
        ("tt2001", "Old Silent Epic II", 1928, "Drama", 50),   # same-director result
        ("tt2004", "A Different Vision", 1926, "Drama", 50),   # cross-director result
        ("tt2002", "Modern Rescore Edition", 2013, "Drama", 50),  # temporally implausible
        ("tt2003", "A Contemporary Companion", 1930, "Drama", 50),  # plausible
        ("tt2005", "Posthumous Puzzle", 1930, "Drama", 50),    # death_year implausible
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", [
        ("tt2000", "nm3001", "director", 1),
        ("tt2001", "nm3001", "director", 1),   # same director as seed

        ("tt2000", "nm3005", "cinematographer", 2),
        ("tt2004", "nm3005", "cinematographer", 1),
        ("tt2004", "nm3002", "director", 2),   # a different director on tt2004

        ("tt2000", "nm3010", "composer", 3),
        ("tt2002", "nm3010", "composer", 1),   # born after the older film

        ("tt2000", "nm3011", "composer", 4),
        ("tt2003", "nm3011", "composer", 1),   # plausible

        ("tt2000", "nm3012", "editor", 5),
        ("tt2005", "nm3012", "editor", 1),     # died before the older film
    ])

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, ?, ?)", [
        ("nm3001", "Dee Director", None, None),
        ("nm3005", "Cam Cinematographer", None, None),
        ("nm3002", "Other Director", None, None),
        ("nm3010", "Modern Composer", 1980, None),
        ("nm3011", "Plausible Composer", 1900, 1960),
        ("nm3012", "Long Gone Editor", 1850, 1920),
    ])

    # annotate_same_director() resolves the seed via resolve_one_title(),
    # which needs title_lookup (see cde.imdb.load()).
    con.execute("""
        CREATE TABLE title_lookup (
            tconst VARCHAR, title VARCHAR, year INTEGER, source VARCHAR
        )
    """)
    con.executemany("INSERT INTO title_lookup VALUES (?, ?, ?, 'primary')", [
        (t, title, year) for t, title, year, _ in [
            ("tt2000", "Old Silent Epic", 1925, None),
            ("tt2001", "Old Silent Epic II", 1928, None),
            ("tt2004", "A Different Vision", 1926, None),
            ("tt2002", "Modern Rescore Edition", 2013, None),
            ("tt2003", "A Contemporary Companion", 1930, None),
            ("tt2005", "Posthumous Puzzle", 1930, None),
        ]
    ])
    return con


@pytest.fixture
def con_tuning():
    con = _build_db()
    yield con
    con.close()


# --------------------------------------------------------------------------
# parse_labeled_eval
# --------------------------------------------------------------------------

SAMPLE_MD = """# sample

| seed | rank | result | tconst | score | explanation | judgment | failure_mode |
|---|---|---|---|---|---|---|---|
| Old Epic (1925) | 1 | Old Epic II (1928) | tt2001 | 0.5 | via: X | trivial | weighting-limited |
| Old Epic (1925) | 2 | A Vision (1926) | tt2004 | 0.4 | via: Y | interesting | |
"""


def test_parse_labeled_eval(tmp_path):
    p = tmp_path / "sample.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    rows = parse_labeled_eval(p)

    assert len(rows) == 2
    assert rows[0]["seed_title"] == "Old Epic"
    assert rows[0]["seed_year"] == 1925
    assert rows[0]["result_title"] == "Old Epic II"
    assert rows[0]["result_year"] == 1928
    assert rows[0]["tconst"] == "tt2001"
    assert rows[0]["judgment"] == "trivial"
    assert rows[0]["failure_mode"] == "weighting-limited"

    assert rows[1]["judgment"] == "interesting"
    assert rows[1]["failure_mode"] is None


# --------------------------------------------------------------------------
# year_gap_bucket
# --------------------------------------------------------------------------


def test_year_gap_bucket_boundaries():
    assert year_gap_bucket(0) == "0-15"
    assert year_gap_bucket(15) == "0-15"
    assert year_gap_bucket(16) == "16-40"
    assert year_gap_bucket(40) == "16-40"
    assert year_gap_bucket(41) == "41+"
    assert year_gap_bucket(100) == "41+"
    assert year_gap_bucket(None) == "unknown"


# --------------------------------------------------------------------------
# pure crosstab aggregation
# --------------------------------------------------------------------------


def test_build_crosstab_A_pure():
    rows = [
        {"judgment": "interesting", "same_director": False},
        {"judgment": "interesting", "same_director": False},
        {"judgment": "trivial", "same_director": True},
        {"judgment": "trivial", "same_director": True},
        {"judgment": "trivial", "same_director": False},
    ]
    table = build_crosstab_A(rows)
    assert table[("interesting", "cross_director")] == 2
    assert table[("trivial", "same_director")] == 2
    assert table[("trivial", "cross_director")] == 1
    assert ("interesting", "same_director") not in table


def test_build_crosstab_B_pure():
    rows = [
        {"judgment": "wrong", "year_gap": 88},
        {"judgment": "wrong", "year_gap": 92},
        {"judgment": "interesting", "year_gap": 3},
    ]
    table = build_crosstab_B(rows)
    assert table[("wrong", "41+")] == 2
    assert table[("interesting", "0-15")] == 1


# --------------------------------------------------------------------------
# is_same_director / annotate_same_director
# --------------------------------------------------------------------------


def test_is_same_director(con_tuning):
    assert is_same_director(con_tuning, "tt2000", "tt2001") is True
    assert is_same_director(con_tuning, "tt2000", "tt2004") is False


def test_annotate_same_director(con_tuning):
    rows = [
        {"seed_title": "Old Silent Epic", "seed_year": 1925, "tconst": "tt2001"},
        {"seed_title": "Old Silent Epic", "seed_year": 1925, "tconst": "tt2004"},
    ]
    annotated = annotate_same_director(con_tuning, rows)
    assert annotated[0]["same_director"] is True
    assert annotated[1]["same_director"] is False
    assert annotated[0]["seed_tconst"] == "tt2000"


# --------------------------------------------------------------------------
# is_temporally_implausible / annotate_year_gap
# --------------------------------------------------------------------------


def test_temporal_implausibility_birth_year(con_tuning):
    # nm3010 born 1980, connects 1925 seed to a 2013 "result" -- implausible.
    assert is_temporally_implausible(con_tuning, "tt2000", "tt2002", 1925, 2013) is True


def test_temporal_implausibility_plausible_pair(con_tuning):
    # nm3011 born 1900, connects 1925 seed to a 1930 result -- plausible.
    assert is_temporally_implausible(con_tuning, "tt2000", "tt2003", 1925, 1930) is False


def test_temporal_implausibility_death_year(con_tuning):
    # nm3012 died 1920, connects 1925 seed to a 1930 result -- died before
    # the older film was even made.
    assert is_temporally_implausible(con_tuning, "tt2000", "tt2005", 1925, 1930) is True


def test_temporal_implausibility_no_shared_person_data_not_flagged(con_tuning):
    # tt2000 <-> tt2001 share nm3001 (director), who has no birth/death
    # data -- absence of data must not be treated as implausibility.
    assert is_temporally_implausible(con_tuning, "tt2000", "tt2001", 1925, 1928) is False


def test_annotate_year_gap(con_tuning):
    rows = [
        {"seed_tconst": "tt2000", "tconst": "tt2002", "seed_year": 1925, "result_year": 2013},
        {"seed_tconst": "tt2000", "tconst": "tt2003", "seed_year": 1925, "result_year": 1930},
    ]
    annotated = annotate_year_gap(con_tuning, rows)
    assert annotated[0]["year_gap"] == 88
    assert annotated[0]["temporally_implausible"] is True
    assert annotated[1]["year_gap"] == 5
    assert annotated[1]["temporally_implausible"] is False
