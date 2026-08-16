"""cde.connect tests against a small, hand-built (film, credits, person) db.

Three independent scenarios:

  tt7000 <-> tt7001: same decade AND genre, but share no person at all --
    the "context can't bridge" case.
  tt7000 <-> tt7002: share nm8001 (director, degree 2) directly -- a plain
    1-hop strong-connector case.

  tt7010 "Start Film" <-> tt7013 "End Film": TWO candidate paths --
    - a 1-hop path via nm8010 (producer, degree inflated to 250 with
      filler credits rows on tconsts that don't exist in `film`, same
      trick as cde.explore's tests) -- weak, ubiquitous.
    - a 3-hop path via nm8011/nm8012/nm8013 (cinematographer/editor/
      composer, degree 2 each, through tt7011/tt7012) -- individually
      rare, cumulatively much stronger. Connect must prefer this one.

  tt7020..tt7025: a pure 5-hop linear chain (cinematographer/editor/
    composer/writer/director, degree 2 each, no shortcuts) -- for the hop
    cap: unreachable at the default cap (4), reachable once the cap is
    raised to 6.
"""

import duckdb
import pytest

from cde.connect import build_strong_person_degree, connect


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?)", [
        ("tt7000", "Film A", 1970, "Drama"),
        ("tt7001", "Film B", 1970, "Drama"),
        ("tt7002", "Film C", 1971, "Mystery"),

        ("tt7010", "Start Film", 1970, "Drama"),
        ("tt7011", "Mid Film 1", 1971, "Mystery"),
        ("tt7012", "Mid Film 2", 1972, "Mystery"),
        ("tt7013", "End Film", 1973, "Mystery"),

        ("tt7020", "Chain A", 1970, "Drama"),
        ("tt7021", "Chain B", 1971, "Drama"),
        ("tt7022", "Chain C", 1972, "Drama"),
        ("tt7023", "Chain D", 1973, "Drama"),
        ("tt7024", "Chain E", 1974, "Drama"),
        ("tt7025", "Chain F", 1975, "Drama"),

        ("tt7030", "Same Person P1", 1970, "Drama"),
        ("tt7031", "Same Person P2", 1971, "Drama"),
        ("tt7032", "Same Person P3", 1972, "Drama"),
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    credits_rows = [
        # tt7000 <-> tt7002: direct strong connector.
        ("tt7000", "nm8001", "director", 1),
        ("tt7002", "nm8001", "director", 1),

        # tt7010 <-> tt7013: the long, rare-collaborator chain.
        ("tt7010", "nm8011", "cinematographer", 1),
        ("tt7011", "nm8011", "cinematographer", 1),
        ("tt7011", "nm8012", "editor", 1),
        ("tt7012", "nm8012", "editor", 1),
        ("tt7012", "nm8013", "composer", 1),
        ("tt7013", "nm8013", "composer", 1),

        # tt7010 <-> tt7013: the short, ubiquitous-producer shortcut.
        ("tt7010", "nm8010", "producer", 1),
        ("tt7013", "nm8010", "producer", 1),

        # tt7020..tt7025: pure 5-hop linear chain, no shortcuts.
        ("tt7020", "nm8020", "cinematographer", 1),
        ("tt7021", "nm8020", "cinematographer", 1),
        ("tt7021", "nm8021", "editor", 1),
        ("tt7022", "nm8021", "editor", 1),
        ("tt7022", "nm8022", "composer", 1),
        ("tt7023", "nm8022", "composer", 1),
        ("tt7023", "nm8023", "writer", 1),
        ("tt7024", "nm8023", "writer", 1),
        ("tt7024", "nm8024", "director", 1),
        ("tt7025", "nm8024", "director", 1),

        # One person (nm8030, cinematographer, degree 3) credited on all
        # three of tt7030/31/32 -- caught during real-data testing: without
        # a no-person-reuse guard, the search would prefer a padded 2-hop
        # "tt7030 -> nm8030 -> tt7031 -> nm8030 -> tt7032" path (double-
        # counting the same relationship) over the correct direct 1-hop
        # edge, since summing the same person's weight twice scores higher.
        ("tt7030", "nm8030", "cinematographer", 1),
        ("tt7031", "nm8030", "cinematographer", 1),
        ("tt7032", "nm8030", "cinematographer", 1),
    ]
    # Inflate nm8010's degree to 250 on tconsts that don't exist in `film`.
    credits_rows += [(f"tt9{i:04d}", "nm8010", "producer", 1) for i in range(248)]
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", credits_rows)

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, NULL, NULL)", [
        ("nm8001", "Dee Director"),
        ("nm8010", "Uber Producer"),
        ("nm8011", "Cam Cinematographer"),
        ("nm8012", "Ed Editor"),
        ("nm8013", "Cory Composer"),
        ("nm8020", "Chain Cinematographer"),
        ("nm8021", "Chain Editor"),
        ("nm8022", "Chain Composer"),
        ("nm8023", "Chain Writer"),
        ("nm8024", "Chain Director"),
        ("nm8030", "Shared Cinematographer"),
    ])
    return con


@pytest.fixture
def con_connect():
    con = _build_db()
    yield con
    con.close()


# --------------------------------------------------------------------------
# edge tiers
# --------------------------------------------------------------------------


def test_strong_connector_edge_bridges(con_connect):
    result = connect(con_connect, "tt7000", "tt7002")
    assert result["found"] is True
    assert result["hops"] == 1
    people = [c["person_name"] for c in result["chain"] if "person_name" in c]
    assert people == ["Dee Director"]


def test_context_edge_does_not_bridge(con_connect):
    # tt7000/tt7001 share only decade+genre -- no person at all.
    result = connect(con_connect, "tt7000", "tt7001")
    assert result["found"] is False
    assert "message" in result
    assert "a" in result and "b" in result


# --------------------------------------------------------------------------
# strongest path, not shortest path
# --------------------------------------------------------------------------


def test_longer_high_weight_chain_preferred_over_short_weak_one(con_connect):
    result = connect(con_connect, "tt7010", "tt7013")
    assert result["found"] is True
    # The 3-hop rare-collaborator chain, not the 1-hop ubiquitous producer.
    assert result["hops"] == 3
    people = [c["person_name"] for c in result["chain"] if "person_name" in c]
    assert people == ["Cam Cinematographer", "Ed Editor", "Cory Composer"]
    # And it must actually outscore what the 1-hop shortcut would have been.
    from cde.explore import CATEGORY_WEIGHT, idf
    weak_1hop_strength = CATEGORY_WEIGHT["producer"] * idf(250)
    assert result["strength"] > weak_1hop_strength


def test_explanation_alternates_films_and_people(con_connect):
    result = connect(con_connect, "tt7000", "tt7002")
    assert "Film A" in result["explanation"]
    assert "Dee Director (director)" in result["explanation"]
    assert "Film C" in result["explanation"]
    assert result["explanation"].count("->") == 2


# --------------------------------------------------------------------------
# hop cap
# --------------------------------------------------------------------------


def test_respects_hop_cap(con_connect):
    # Requires exactly 5 hops -- unreachable at the default cap (4).
    result = connect(con_connect, "tt7020", "tt7025", hop_cap=4)
    assert result["found"] is False


def test_finds_path_when_cap_allows_it(con_connect):
    result = connect(con_connect, "tt7020", "tt7025", hop_cap=6)
    assert result["found"] is True
    assert result["hops"] == 5
    assert result["chain"][0]["tconst"] == "tt7020"
    assert result["chain"][-1]["tconst"] == "tt7025"


def test_no_person_reused_within_a_single_path(con_connect):
    # nm8030 is credited on all three of tt7030/31/32. The correct answer
    # is the direct 1-hop edge, not a padded 2-hop path that reuses
    # nm8030 twice to (falsely) double its contribution to path strength.
    result = connect(con_connect, "tt7030", "tt7032")
    assert result["found"] is True
    assert result["hops"] == 1
    people = [c["person_name"] for c in result["chain"] if "person_name" in c]
    assert people == ["Shared Cinematographer"]


def test_same_film_raises(con_connect):
    with pytest.raises(ValueError):
        connect(con_connect, "tt7000", "tt7000")


def test_unknown_tconst_raises(con_connect):
    with pytest.raises(ValueError):
        connect(con_connect, "tt7000", "tt_nonexistent")


# --------------------------------------------------------------------------
# build_strong_person_degree() -- precomputed vs. on-demand consistency
# --------------------------------------------------------------------------


def test_build_strong_person_degree_correct(con_connect):
    degree = build_strong_person_degree(con_connect)
    assert degree["nm8001"] == 2
    assert degree["nm8010"] == 250
    assert degree["nm8011"] == 2


def test_connect_same_result_with_precomputed_degree(con_connect):
    on_demand = connect(con_connect, "tt7010", "tt7013")
    precomputed = connect(
        con_connect, "tt7010", "tt7013",
        person_degree=build_strong_person_degree(con_connect),
    )
    assert on_demand["hops"] == precomputed["hops"]
    assert on_demand["strength"] == pytest.approx(precomputed["strength"], abs=1e-6)
