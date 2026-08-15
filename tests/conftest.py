"""Tiny synthetic gz TSV fixtures + a seeded in-memory DuckDB backbone.

Anchors reproduce known cases from the real reconstruction run: a
Shawshank-like row (high votes), an "8 1/2" row (title/original-title
divergence), a "The Seventh Seal" row with an aka, two same-title
different-year rows (Dune 1984/2021, for the year +/-1 window), two
identical-title-and-year rows (Nightfall x2, for the votes tie-break), one
isAdult='1' row, and one short (dropped by the default --types filter).
"""

from __future__ import annotations

import gzip

import duckdb
import pytest

import cde.imdb as imdb_mod
import cde.people as people_mod

BASICS_HEADER = (
    "tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult"
    "\tstartYear\tendYear\truntimeMinutes\tgenres"
)
BASICS_ROWS = [
    "tt0001111\tmovie\tThe Shawshank Redemption\tThe Shawshank Redemption\t0"
    "\t1994\t\\N\t142\tDrama",
    "tt0002222\tmovie\t8 1/2\tOtto e mezzo\t0\t1963\t\\N\t138\tDrama,Fantasy",
    "tt0003333\tmovie\tThe Seventh Seal\tDet sjunde inseglet\t0\t1957\t\\N\t96\tDrama",
    "tt0004444\tmovie\tDune\tDune\t0\t1984\t\\N\t137\tAdventure,Sci-Fi",
    "tt0005555\tmovie\tDune\tDune\t0\t2021\t\\N\t155\tAdventure,Drama,Sci-Fi",
    "tt0006666\tmovie\tForbidden Skin\tForbidden Skin\t1\t1999\t\\N\t90\tDrama",
    "tt0007777\tshort\tShort Subject\tShort Subject\t0\t2000\t\\N\t12\tComedy",
    "tt0008888\tmovie\tNightfall\tNightfall\t0\t2005\t\\N\t100\tDrama",
    "tt0009999\tmovie\tNightfall\tNightfall\t0\t2005\t\\N\t105\tDrama",
]

AKAS_HEADER = (
    "titleId\tordering\ttitle\tregion\tlanguage\ttypes\tattributes\tisOriginalTitle"
)
AKAS_ROWS = [
    "tt0003333\t1\tSeventh Seal\tUS\t\\N\t\\N\t\\N\t0",
]

RATINGS_HEADER = "tconst\taverageRating\tnumVotes"
RATINGS_ROWS = [
    "tt0001111\t9.3\t2900000",
    "tt0002222\t8.0\t150000",
    "tt0003333\t8.1\t90000",
    "tt0004444\t8.0\t250000",
    "tt0005555\t8.0\t700000",
    "tt0008888\t6.0\t5000",
    "tt0009999\t6.5\t50000",
]

CREW_HEADER = "tconst\tdirectors\twriters"
CREW_ROWS = [
    "tt0001111\tnm0001\tnm0002",
    "tt0002222\tnm0003\tnm0003",
    "tt0003333\tnm0004\tnm0004",
    "tt0004444\tnm0005\tnm0006",
    "tt0005555\tnm0007\tnm0008",
    "tt0008888\tnm0009\t\\N",
    "tt0009999\tnm0010\t\\N",
]

# Principals/name.basics fixtures for cde.people. Covers: creative
# categories kept (director/writer/cinematographer/actor/composer/
# producer), self and archive_footage dropped, a principals row on a
# non-film tconst (tt0007777 is a short -- excluded from `film` by the
# default --types filter) dropped by the JOIN, a person (nm1006) credited
# only via a dropped-category row so they never reach `person`, a person
# (nm1004) with credits on two films (degree 2), and a name.basics row
# (nm9999) for someone never credited at all.
PRINCIPALS_HEADER = "tconst\tordering\tnconst\tcategory\tjob\tcharacters"
PRINCIPALS_ROWS = [
    "tt0001111\t1\tnm1001\tdirector\t\\N\t\\N",
    "tt0001111\t2\tnm1002\twriter\t\\N\t\\N",
    "tt0001111\t3\tnm1003\tcinematographer\t\\N\t\\N",
    "tt0001111\t4\tnm1004\tactor\t\\N\t[\"Andy Dufresne\"]",
    "tt0001111\t5\tnm1004\tself\t\\N\t\\N",
    "tt0002222\t1\tnm1003\tcinematographer\t\\N\t\\N",
    "tt0002222\t2\tnm1005\tdirector\t\\N\t\\N",
    "tt0002222\t3\tnm1006\tarchive_footage\t\\N\t\\N",
    "tt0003333\t1\tnm1008\tcomposer\t\\N\t\\N",
    "tt0003333\t2\tnm1004\tproducer\t\\N\t\\N",
    "tt0007777\t1\tnm1007\tdirector\t\\N\t\\N",
]

NAME_BASICS_HEADER = (
    "nconst\tprimaryName\tbirthYear\tdeathYear\tprimaryProfession\tknownForTitles"
)
NAME_BASICS_ROWS = [
    "nm1001\tAlan Director\t\\N\t\\N\tdirector\t\\N",
    "nm1002\tWendy Writer\t\\N\t\\N\twriter\t\\N",
    "nm1003\tCameron Cinematographer\t\\N\t\\N\tcinematographer\t\\N",
    "nm1004\tPat Player\t\\N\t\\N\tactor\t\\N",
    "nm1005\tDiane Director\t\\N\t\\N\tdirector\t\\N",
    "nm1006\tArchie Footage\t\\N\t\\N\tmiscellaneous\t\\N",
    "nm1007\tSam Short\t\\N\t\\N\tdirector\t\\N",
    "nm1008\tCory Composer\t\\N\t\\N\tcomposer\t\\N",
    "nm9999\tNobody Credited\t\\N\t\\N\tactor\t\\N",
]


def _write_gz(path, header, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(row + "\n")


@pytest.fixture
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    _write_gz(d / "title.basics.tsv.gz", BASICS_HEADER, BASICS_ROWS)
    _write_gz(d / "title.akas.tsv.gz", AKAS_HEADER, AKAS_ROWS)
    _write_gz(d / "title.ratings.tsv.gz", RATINGS_HEADER, RATINGS_ROWS)
    _write_gz(d / "title.crew.tsv.gz", CREW_HEADER, CREW_ROWS)
    _write_gz(d / "title.principals.tsv.gz", PRINCIPALS_HEADER, PRINCIPALS_ROWS)
    _write_gz(d / "name.basics.tsv.gz", NAME_BASICS_HEADER, NAME_BASICS_ROWS)
    return d


@pytest.fixture
def con(raw_dir, monkeypatch):
    """An in-memory DuckDB connection with film + title_lookup built from
    the fixtures above, using the package's default --types (movie only)."""
    monkeypatch.setattr(imdb_mod, "DATA_RAW", raw_dir)
    c = duckdb.connect(":memory:")
    imdb_mod.load(c, with_people=False, types=None)
    yield c
    c.close()


@pytest.fixture
def con_people(con, raw_dir, monkeypatch):
    """`con`, plus credits/person built by cde.people.load_people() from
    the same fixtures."""
    monkeypatch.setattr(people_mod, "DATA_RAW", raw_dir)
    people_mod.load_people(con)
    return con


def lookup_reconciles(con: duckdb.DuckDBPyConnection) -> bool:
    """title_lookup row count should always equal primary + original + aka
    sub-counts -- an invariant that holds regardless of snapshot."""
    primary_n = con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    original_n = con.execute("""
        SELECT COUNT(*) FROM film
        WHERE originalTitle IS NOT NULL AND originalTitle != primaryTitle
    """).fetchone()[0]
    aka_n = con.execute("SELECT COUNT(*) FROM title_lookup WHERE source = 'aka'").fetchone()[0]
    total_n = con.execute("SELECT COUNT(*) FROM title_lookup").fetchone()[0]
    return total_n == primary_n + original_n + aka_n
