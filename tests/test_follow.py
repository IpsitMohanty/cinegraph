"""cde.follow tests against a small, hand-built (film, credits, person) db.

Fixture, all sharing director nm6001 with the seed tt5000 unless noted
(the seed's strong-connector neighbourhood for follow_context tests):

  tt5000 "Seed Film" (1975, Drama)         -- the seed. Credited: nm6001
                                               director, nm6002 writer,
                                               nm6003 editor (orphan --
                                               no `person` row),
                                               nm6004 cinematographer,
                                               nm6005 composer, nm6006
                                               actor, nm6007 actress.
  tt5001 "Connected Film" (1976, Drama)    -- shares nm6001 (director).
  tt5002 "Unconnected Film" (1976, Drama)  -- shares NOTHING with the seed
                                               -- proves follow_context is
                                               neighbourhood-scoped, not a
                                               bare corpus filter (same
                                               decade/genre as the seed,
                                               must still be excluded).
  tt5003 "Old Person Film" (1960, Comedy)  -- nm6002 (seed's writer) is
                                               DIRECTOR here -- Follow
                                               (person) shows a different
                                               role per film.
  tt5005 "Orphan's Other Film" (1980, Drama) -- nm6003 (orphan) editor.
  tt5006 "Connected Comedy" (1976, Comedy) -- shares nm6001 (director).
  tt5007 "Connected Later Film" (1985, Drama) -- shares nm6001 (director).
"""

import duckdb
import pytest

from cde.follow import (
    ENTITY_TYPES,
    STUBBED_ENTITY_TYPES,
    film_view,
    follow,
    follow_context,
    follow_person,
)


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?)", [
        ("tt5000", "Seed Film", 1975, "Drama"),
        ("tt5001", "Connected Film", 1976, "Drama"),
        ("tt5002", "Unconnected Film", 1976, "Drama"),
        ("tt5003", "Old Person Film", 1960, "Comedy"),
        ("tt5005", "Orphan's Other Film", 1980, "Drama"),
        ("tt5006", "Connected Comedy", 1976, "Comedy"),
        ("tt5007", "Connected Later Film", 1985, "Drama"),
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", [
        ("tt5000", "nm6001", "director", 1),
        ("tt5001", "nm6001", "director", 1),
        ("tt5006", "nm6001", "director", 1),
        ("tt5007", "nm6001", "director", 1),

        ("tt5000", "nm6002", "writer", 2),
        ("tt5003", "nm6002", "director", 1),

        ("tt5000", "nm6003", "editor", 3),
        ("tt5005", "nm6003", "editor", 1),

        ("tt5000", "nm6004", "cinematographer", 4),
        ("tt5000", "nm6005", "composer", 5),
        ("tt5000", "nm6006", "actor", 6),
        ("tt5000", "nm6007", "actress", 7),
    ])

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, ?, ?)", [
        ("nm6001", "Dee Director", None, None),
        ("nm6002", "Wendy Writer", None, None),
        # nm6003 deliberately absent -- orphan nconst.
        ("nm6004", "Cam Cinematographer", None, None),
        ("nm6005", "Cory Composer", None, None),
        ("nm6006", "Alan Actor", None, None),
        ("nm6007", "Andrea Actress", None, None),
    ])
    return con


@pytest.fixture
def con_follow():
    con = _build_db()
    yield con
    con.close()


# --------------------------------------------------------------------------
# Follow(person)
# --------------------------------------------------------------------------


def test_follow_person_returns_filmography_with_roles(con_follow):
    out = follow_person(con_follow, "nm6002")
    assert out["entity_type"] == "person"
    assert out["entity_name"] == "Wendy Writer"
    films = {f["tconst"]: f["role"] for f in out["films"]}
    assert films == {"tt5003": "director", "tt5000": "writer"}
    # oldest first
    assert [f["tconst"] for f in out["films"]] == ["tt5003", "tt5000"]


def test_follow_person_orphan_nconst_still_yields_rows(con_follow):
    out = follow_person(con_follow, "nm6003")
    assert out["entity_name"] == "nm6003"  # COALESCE fallback, no person row
    tconsts = {f["tconst"] for f in out["films"]}
    assert tconsts == {"tt5000", "tt5005"}


# --------------------------------------------------------------------------
# Follow(decade|genre) -- neighbourhood-scoped
# --------------------------------------------------------------------------


def test_follow_context_scoped_to_neighbourhood_not_whole_corpus(con_follow):
    out = follow_context(con_follow, "decade", 1970, "tt5000")
    tconsts = {f["tconst"] for f in out["films"]}
    # tt5002 shares the seed's decade AND genre but no strong-connector
    # person -- must never appear.
    assert "tt5002" not in tconsts
    assert tconsts == {"tt5001", "tt5006"}


def test_follow_context_decade_filter(con_follow):
    out = follow_context(con_follow, "decade", 1980, "tt5000")
    assert {f["tconst"] for f in out["films"]} == {"tt5005", "tt5007"}


def test_follow_context_genre_filter(con_follow):
    out = follow_context(con_follow, "genre", "Comedy", "tt5000")
    # tt5003 (nm6002 is director there, writer on the seed) is also in
    # the neighbourhood, and is a Comedy too.
    assert {f["tconst"] for f in out["films"]} == {"tt5003", "tt5006"}

    out_drama = follow_context(con_follow, "genre", "Drama", "tt5000")
    assert {f["tconst"] for f in out_drama["films"]} == {"tt5001", "tt5005", "tt5007"}


def test_follow_context_invalid_type_raises(con_follow):
    with pytest.raises(ValueError):
        follow_context(con_follow, "actor", "someone", "tt5000")


# --------------------------------------------------------------------------
# craft-first grouping
# --------------------------------------------------------------------------


def test_film_view_craft_first_cast_secondary(con_follow):
    view = film_view(con_follow, "tt5000")
    categories = [g["category"] for g in view["groups"]]
    assert categories == [
        "director", "writer", "cinematographer", "editor", "composer", "cast",
    ]
    cast_group = view["groups"][-1]
    cast_names = {p["name"] for p in cast_group["people"]}
    assert cast_names == {"Alan Actor", "Andrea Actress"}
    director_group = view["groups"][0]
    assert director_group["people"] == [{"nconst": "nm6001", "name": "Dee Director"}]


def test_film_view_orphan_editor_present(con_follow):
    view = film_view(con_follow, "tt5000")
    editor_group = next(g for g in view["groups"] if g["category"] == "editor")
    assert editor_group["people"] == [{"nconst": "nm6003", "name": "nm6003"}]


# --------------------------------------------------------------------------
# registry dispatch
# --------------------------------------------------------------------------


def test_registry_unregistered_type_is_stub_not_crash(con_follow):
    out = follow(con_follow, "company", "Q123")
    assert out["status"] == "not_implemented"
    assert out["entity_type"] == "company"


def test_registry_all_stub_types_defined():
    for entity_type in STUBBED_ENTITY_TYPES:
        assert entity_type not in ENTITY_TYPES


def test_registry_unknown_type_raises(con_follow):
    with pytest.raises(ValueError):
        follow(con_follow, "not_a_real_type", "x")


def test_registry_dispatches_person(con_follow):
    out = follow(con_follow, "person", "nm6002")
    assert out["entity_type"] == "person"


def test_registry_dispatches_context_requires_seed(con_follow):
    with pytest.raises(ValueError):
        follow(con_follow, "decade", 1970)  # no seed
    out = follow(con_follow, "decade", 1970, seed="tt5000")
    assert out["entity_type"] == "decade"
