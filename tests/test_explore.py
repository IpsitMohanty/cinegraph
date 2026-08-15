"""cde.explore tests against a small, hand-built (film, credits, person)
db -- no gz TSV fixtures needed, explore() only ever reads these three
tables.

Fixture cast, all connected to the seed tt1000 ("Film Noir Classic", 1975,
Drama/Crime) through exactly one shared person each:

  tt1001 "Distant Echo" (1990, Thriller)       -- nm2001 cinematographer,
                                                   degree 2 (rare)
  tt1002 "Popular Flick" (1976, Drama)         -- nm2002 actor, degree 200
                                                   (ubiquitous; +198 filler
                                                   credits rows on tconsts
                                                   that don't exist in
                                                   `film`, purely to inflate
                                                   degree -- explore() must
                                                   skip candidates it can't
                                                   find film info for)
  tt1003 "Film Noir Classic II" (1977, D/C)    -- nm2004 writer, degree 2;
                                                   a real edge, but the
                                                   title is a sequel of the
                                                   seed's -- must be dropped
  tt1004 "Same Director Pic" (1980, Adventure) -- nm2005 director, degree 2;
                                                   also F's director, for
                                                   the novelty test
  tt1005 "Orphan Connection" (1975, Drama)     -- nm2006 editor, degree 2;
                                                   nm2006 has no `person`
                                                   row (orphan nconst)
"""

import duckdb
import pytest

from cde.explore import (
    CATEGORY_WEIGHT,
    DECADE_BONUS,
    GENRE_BONUS_CAP_GENRES,
    GENRE_BONUS_PER_GENRE,
    NOVELTY_DIRECTOR_DAMPING,
    build_person_degree,
    explore,
    idf,
)

SEED = "tt1000"


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?)", [
        ("tt1000", "Film Noir Classic", 1975, "Drama,Crime"),
        ("tt1001", "Distant Echo", 1990, "Thriller"),
        ("tt1002", "Popular Flick", 1976, "Drama"),
        ("tt1003", "Film Noir Classic II", 1977, "Drama,Crime"),
        ("tt1004", "Same Director Pic", 1980, "Adventure"),
        ("tt1005", "Orphan Connection", 1975, "Drama"),
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    credits_rows = [
        ("tt1000", "nm2001", "cinematographer", 1),
        ("tt1001", "nm2001", "cinematographer", 1),

        ("tt1000", "nm2002", "actor", 2),
        ("tt1002", "nm2002", "actor", 1),

        ("tt1000", "nm2004", "writer", 3),
        ("tt1003", "nm2004", "writer", 1),

        ("tt1000", "nm2005", "director", 4),
        ("tt1004", "nm2005", "director", 1),

        ("tt1000", "nm2006", "editor", 5),
        ("tt1005", "nm2006", "editor", 1),
    ]
    # Inflate nm2002's degree to 200 on tconsts that don't exist in `film`.
    credits_rows += [(f"tt9{i:03d}", "nm2002", "actor", 1) for i in range(198)]
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", credits_rows)

    con.execute("CREATE TABLE person (nconst VARCHAR, primary_name VARCHAR)")
    con.executemany("INSERT INTO person VALUES (?, ?)", [
        ("nm2001", "Rae Cinematographer"),
        ("nm2002", "Uber Actor"),
        ("nm2004", "Wanda Writer"),
        ("nm2005", "Dee Director"),
        # nm2006 deliberately absent -- orphan nconst.
    ])
    return con


@pytest.fixture
def con_explore():
    con = _build_db()
    yield con
    con.close()


def _by_tconst(results):
    return {r["tconst"]: r for r in results}


# --------------------------------------------------------------------------
# person_degree
# --------------------------------------------------------------------------


def test_person_degree_correct(con_explore):
    degree = build_person_degree(con_explore)
    assert degree["nm2001"] == 2
    assert degree["nm2002"] == 200
    assert degree["nm2004"] == 2
    assert degree["nm2005"] == 2
    assert degree["nm2006"] == 2


# --------------------------------------------------------------------------
# candidate retrieval
# --------------------------------------------------------------------------


def test_candidate_retrieval_finds_shared_person_films_excludes_seed(con_explore):
    out = explore(con_explore, SEED)
    tconsts = {r["tconst"] for r in out["results"]}

    assert SEED not in tconsts
    assert {"tt1001", "tt1002", "tt1004", "tt1005"} <= tconsts
    # Filler tconsts used only to inflate nm2002's degree are not real
    # films and must never surface as results.
    assert not any(t.startswith("tt9") for t in tconsts)


# --------------------------------------------------------------------------
# idf / category_weight math
# --------------------------------------------------------------------------


def test_idf_basic():
    assert idf(1) == 1.0
    assert idf(2) == pytest.approx(1 / 1.5849625, rel=1e-6)


def test_rare_cinematographer_outscores_ubiquitous_actor(con_explore):
    out = explore(con_explore, SEED)
    results = _by_tconst(out["results"])

    cinematographer_edge = results["tt1001"]["connections"][0]["weight"]
    actor_edge = results["tt1002"]["connections"][0]["weight"]

    expected_cinematographer = CATEGORY_WEIGHT["cinematographer"] * idf(2)
    expected_actor = CATEGORY_WEIGHT["actor"] * idf(200)

    assert cinematographer_edge == pytest.approx(expected_cinematographer, abs=1e-4)
    assert actor_edge == pytest.approx(expected_actor, abs=1e-4)
    assert cinematographer_edge > actor_edge


# --------------------------------------------------------------------------
# bonus vs. specific-person edge
# --------------------------------------------------------------------------


def test_bonus_smaller_than_specific_person_edge():
    max_bonus = DECADE_BONUS + GENRE_BONUS_PER_GENRE * GENRE_BONUS_CAP_GENRES
    modestly_rare_writer_edge = CATEGORY_WEIGHT["writer"] * idf(3)
    assert max_bonus < modestly_rare_writer_edge


def test_shared_decade_and_genre_bonus_applied(con_explore):
    out = explore(con_explore, SEED)
    results = _by_tconst(out["results"])

    # tt1002: 1976 (same decade as 1975) + genre "Drama" shared with F.
    r = results["tt1002"]
    assert r["shared_decade"] == 1970
    assert r["shared_genres"] == ["Drama"]

    actor_edge = CATEGORY_WEIGHT["actor"] * idf(200)
    expected_bonus = DECADE_BONUS + GENRE_BONUS_PER_GENRE * 1
    assert r["score"] == pytest.approx(actor_edge + expected_bonus, abs=1e-4)

    # tt1001: different decade, no genre overlap -- no bonus at all.
    r1 = results["tt1001"]
    assert r1["shared_decade"] is None
    assert r1["shared_genres"] == []


# --------------------------------------------------------------------------
# franchise-prefix penalty
# --------------------------------------------------------------------------


def test_franchise_prefix_penalty_fires(con_explore):
    out = explore(con_explore, SEED)
    tconsts = {r["tconst"] for r in out["results"]}
    # tt1003 "Film Noir Classic II" shares a real writer edge with F but
    # must be dropped as a franchise/sequel of "Film Noir Classic".
    assert "tt1003" not in tconsts


# --------------------------------------------------------------------------
# novelty
# --------------------------------------------------------------------------


def test_novelty_down_weights_same_director(con_explore):
    off = _by_tconst(explore(con_explore, SEED, novelty=False)["results"])
    on = _by_tconst(explore(con_explore, SEED, novelty=True)["results"])

    weight_off = off["tt1004"]["connections"][0]["weight"]
    weight_on = on["tt1004"]["connections"][0]["weight"]

    assert weight_on < weight_off
    assert weight_on == pytest.approx(weight_off * NOVELTY_DIRECTOR_DAMPING, abs=1e-4)
    # Same-director candidate stays visible, just down-weighted -- not
    # dropped.
    assert "tt1004" in on


# --------------------------------------------------------------------------
# explanation assembly / orphan nconst
# --------------------------------------------------------------------------


def test_explanation_orphan_nconst_left_join_coalesce(con_explore):
    out = explore(con_explore, SEED)
    results = _by_tconst(out["results"])

    r = results["tt1005"]
    assert len(r["connections"]) == 1
    # nm2006 has no `person` row -- must still produce an edge, with the
    # nconst itself as the name fallback, not a dropped row.
    assert r["connections"][0]["person_name"] == "nm2006"
    assert r["connections"][0]["role"] == "editor"
    assert "connected through" in r["explanation"]


def test_explanation_uses_real_person_name_when_present(con_explore):
    out = explore(con_explore, SEED)
    results = _by_tconst(out["results"])
    r = results["tt1001"]
    assert r["connections"][0]["person_name"] == "Rae Cinematographer"


def test_explore_unknown_tconst_raises(con_explore):
    with pytest.raises(ValueError):
        explore(con_explore, "tt_nonexistent")
