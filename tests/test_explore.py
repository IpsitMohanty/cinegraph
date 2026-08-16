"""cde.explore tests against a small, hand-built (film, credits, person)
db -- no gz TSV fixtures needed, explore() only ever reads these three
tables.

Fixture cast, all connected to the seed tt1000 ("Film Noir Classic", 1975,
Drama/Crime) through exactly one shared person each, unless noted:

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
                                                   the same-director penalty
  tt1005 "Orphan Connection" (1975, Drama)     -- nm2006 editor, degree 2;
                                                   nm2006 has no `person`
                                                   row (orphan nconst)

Phase 2 tuning fixtures (stage 3A tuning, Fix 1 / Fix 2):

  tt1006 "A Cross-Director Film" (1988)  -- nm2007 cinematographer (shared,
                                             degree 2), directed by nm2008
                                             (NOT the seed's director) --
                                             cross-director bonus case.
  tt1007 "Born Too Late Flick" (1930)    -- nm2009, credited as *writer* on
                                             the seed, birth_year=2050 --
                                             birth-year gate, proving no
                                             writer exemption on rule 1.
  tt1008 "Died Before Flick" (1930)      -- nm2010 director, death_year=1900
                                             -- death-year gate drops a
                                             non-writer.
  tt1009 "Adaptation Decades Later" (1930) -- nm2011 *writer*, death_year=
                                             1900 -- the Akutagawa/
                                             Bandyopadhyay case: KEPT.
  tt1010 "Modern Rescore" (2020)         -- nm2012 composer, no birth_year,
                                             45-year gap -- rescore clause
                                             drops it.
  tt1011 "Modern But Plausible" (2020)   -- nm2013 composer, birth_year=
                                             1930 (in-window) -- rescore
                                             clause does NOT fire; KEPT.
  tt1012 "Cast Only Classic" (1965)      -- a second "seed": nm2014/nm2015,
                                             actor/actress only, no crew
                                             role at all -- thin_data case.

Pre-ship tuning fixtures (credit-importance billing down-weight, cast
only). tt1013/tt1014/tt1015 all share the seed's decade/genre (1990,
Mystery vs. the seed's 1975 Drama/Crime -- no overlap either way), so their
scores isolate the person edge alone with zero bonus noise:

  tt1013 "Low Billed Connection" (1990)  -- nm2016 actor, ordering=9 on the
                                             seed (a deep cameo), degree 2.
  tt1014 "High Billed Connection" (1990) -- nm2017 actor, ordering=1 on the
                                             seed (the lead), degree 2.
  tt1015 "Crew High Ordering" (1990)     -- nm2018 cinematographer,
                                             ordering=9 on the seed, degree
                                             2 -- billing is cast-only, so
                                             this must score exactly like
                                             ordering=1 would.
"""

import duckdb
import pytest

from cde.explore import (
    CATEGORY_WEIGHT,
    CROSS_DIRECTOR_BONUS,
    DECADE_BONUS,
    GENRE_BONUS_CAP_GENRES,
    GENRE_BONUS_PER_GENRE,
    SAME_DIRECTOR_PENALTY,
    _billing_factor,
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
        ("tt1006", "A Cross-Director Film", 1988, "Mystery"),
        ("tt1007", "Born Too Late Flick", 1930, "Drama"),
        ("tt1008", "Died Before Flick", 1930, "Drama"),
        ("tt1009", "Adaptation Decades Later", 1930, "Drama"),
        ("tt1010", "Modern Rescore", 2020, "Drama"),
        ("tt1011", "Modern But Plausible", 2020, "Drama"),
        ("tt1012", "Cast Only Classic", 1965, "Drama"),
        ("tt1013", "Low Billed Connection", 1990, "Mystery"),
        ("tt1014", "High Billed Connection", 1990, "Mystery"),
        ("tt1015", "Crew High Ordering", 1990, "Mystery"),
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

        # Cross-director bonus: nm2007 shared, tt1006's own director (nm2008)
        # differs from the seed's (nm2005).
        ("tt1000", "nm2007", "cinematographer", 6),
        ("tt1006", "nm2007", "cinematographer", 1),
        ("tt1006", "nm2008", "director", 2),

        # Birth-year gate (universal, writer included).
        ("tt1000", "nm2009", "writer", 7),
        ("tt1007", "nm2009", "writer", 1),

        # Death-year gate: non-writer dropped.
        ("tt1000", "nm2010", "director", 8),
        ("tt1008", "nm2010", "director", 1),

        # Death-year gate: writer exempt (adaptation lineage) -- KEPT.
        ("tt1000", "nm2011", "writer", 9),
        ("tt1009", "nm2011", "writer", 1),

        # Rescore clause: composer, no birth_year, large gap -- dropped.
        ("tt1000", "nm2012", "composer", 10),
        ("tt1010", "nm2012", "composer", 1),

        # Rescore clause does NOT fire: composer WITH an in-window
        # birth_year, despite the same large gap -- KEPT.
        ("tt1000", "nm2013", "composer", 11),
        ("tt1011", "nm2013", "composer", 1),

        # Thin-data seed: cast-only, no crew role at all.
        ("tt1012", "nm2014", "actor", 1),
        ("tt1012", "nm2015", "actress", 2),

        # Credit-importance billing: low-billed vs. top-billed cast, and a
        # high-ordering crew edge that billing must NOT touch.
        ("tt1000", "nm2016", "actor", 9),
        ("tt1013", "nm2016", "actor", 1),

        ("tt1000", "nm2017", "actor", 1),
        ("tt1014", "nm2017", "actor", 1),

        ("tt1000", "nm2018", "cinematographer", 9),
        ("tt1015", "nm2018", "cinematographer", 1),
    ]
    # Inflate nm2002's degree to 200 on tconsts that don't exist in `film`.
    credits_rows += [(f"tt9{i:03d}", "nm2002", "actor", 1) for i in range(198)]
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", credits_rows)

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, ?, ?)", [
        ("nm2001", "Rae Cinematographer", None, None),
        ("nm2002", "Uber Actor", None, None),
        ("nm2004", "Wanda Writer", None, None),
        ("nm2005", "Dee Director", None, None),
        # nm2006 deliberately absent -- orphan nconst.
        ("nm2007", "Cam Cross", None, None),
        ("nm2008", "Otto Otherdirector", None, None),
        ("nm2009", "Future Person", 2050, None),
        ("nm2010", "Early Departed", None, 1900),
        ("nm2011", "Classic Novelist", None, 1900),
        ("nm2012", "Mystery Rescorer", None, None),
        ("nm2013", "Plausible Composer", 1930, None),
        ("nm2016", "Deep Cameo", None, None),
        ("nm2017", "Top Billed Lead", None, None),
        ("nm2018", "Crew Regardless Of Ordering", None, None),
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
    out = explore(
        con_explore, SEED, novelty=False, temporal_gate=False, credit_importance=False,
    )
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
    out = explore(
        con_explore, SEED, novelty=False, temporal_gate=False, credit_importance=False,
    )
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
# same-director penalty (novelty, default ON)
# --------------------------------------------------------------------------


def test_same_director_penalty_demotes_not_drops(con_explore):
    off = _by_tconst(explore(con_explore, SEED, novelty=False)["results"])
    on = _by_tconst(explore(con_explore, SEED, novelty=True)["results"])

    weight_off = off["tt1004"]["connections"][0]["weight"]
    weight_on = on["tt1004"]["connections"][0]["weight"]

    assert weight_on < weight_off
    assert weight_on == pytest.approx(weight_off * SAME_DIRECTOR_PENALTY, abs=1e-4)
    # Same-director candidate stays visible, just down-weighted -- not
    # dropped.
    assert "tt1004" in on


def test_same_director_penalty_is_config_exposed(con_explore):
    custom = _by_tconst(
        explore(con_explore, SEED, novelty=True, same_director_penalty=0.1)["results"]
    )
    default = _by_tconst(explore(con_explore, SEED, novelty=True)["results"])
    custom_weight = custom["tt1004"]["connections"][0]["weight"]
    default_weight = default["tt1004"]["connections"][0]["weight"]
    assert custom_weight < default_weight


def test_novelty_defaults_to_on(con_explore):
    default = _by_tconst(explore(con_explore, SEED)["results"])
    explicit_on = _by_tconst(explore(con_explore, SEED, novelty=True)["results"])
    assert default["tt1004"]["score"] == explicit_on["tt1004"]["score"]


# --------------------------------------------------------------------------
# cross-director bonus (OFF by default)
# --------------------------------------------------------------------------


def test_cross_director_bonus_off_by_default(con_explore):
    out = _by_tconst(explore(con_explore, SEED, temporal_gate=False)["results"])
    edge = CATEGORY_WEIGHT["cinematographer"] * idf(2)
    assert out["tt1006"]["score"] == pytest.approx(edge, abs=1e-4)


def test_cross_director_bonus_fires_only_across_different_directors(con_explore):
    on = _by_tconst(explore(
        con_explore, SEED, temporal_gate=False, cross_director_bonus_enabled=True,
    )["results"])

    cinematographer_edge = CATEGORY_WEIGHT["cinematographer"] * idf(2)
    # tt1006 has its own director (nm2008), different from the seed's
    # (nm2005) -- cross-director bonus applies.
    assert on["tt1006"]["score"] == pytest.approx(
        cinematographer_edge + CROSS_DIRECTOR_BONUS, abs=1e-4
    )

    # tt1004's director IS the seed's director (nm2005) -- no bonus, even
    # with the flag on.
    director_edge = CATEGORY_WEIGHT["director"] * idf(2) * SAME_DIRECTOR_PENALTY
    assert on["tt1004"]["score"] == pytest.approx(director_edge, abs=1e-4)


# --------------------------------------------------------------------------
# temporal-plausibility gate (default ON)
# --------------------------------------------------------------------------


def test_temporal_gate_birth_year_universal_including_writer(con_explore):
    on = _by_tconst(explore(con_explore, SEED, novelty=False)["results"])
    off = _by_tconst(explore(con_explore, SEED, novelty=False, temporal_gate=False)["results"])

    # nm2009 is credited as *writer*, born 2050 -- the older film (1930) is
    # decades before they were born. Dropped even though it's a writer
    # credit -- rule 1 has no exemption.
    assert "tt1007" not in on
    assert "tt1007" in off


def test_temporal_gate_death_year_excludes_writer(con_explore):
    on = _by_tconst(explore(con_explore, SEED, novelty=False)["results"])

    # nm2010, director, died 1900 -- before the older film (1930) was even
    # made. Dropped.
    assert "tt1008" not in on

    # nm2011, writer, also died 1900, same year math -- but writers are
    # exempt from the death-year check: posthumous source-material credit
    # (Akutagawa -> The Outrage; the Apu trilogy) is real adaptation
    # lineage, not an artifact. KEPT.
    assert "tt1009" in on
    assert on["tt1009"]["connections"][0]["role"] == "writer"


def test_temporal_gate_rescore_clause(con_explore):
    on = _by_tconst(explore(con_explore, SEED, novelty=False)["results"])

    # nm2012, composer, no birth_year, 45-year gap -- the rescore/band
    # signature. Dropped.
    assert "tt1010" not in on

    # nm2013, composer, birth_year=1930 (plausibly in-window) despite the
    # same 45-year gap -- the clause only fires when birth_year is
    # entirely absent. KEPT.
    assert "tt1011" in on


def test_temporal_gate_off_keeps_all(con_explore):
    off = _by_tconst(explore(con_explore, SEED, novelty=False, temporal_gate=False)["results"])
    assert {"tt1007", "tt1008", "tt1009", "tt1010", "tt1011"} <= set(off)


# --------------------------------------------------------------------------
# credit-importance billing down-weight (cast only, default ON)
# --------------------------------------------------------------------------


def test_billing_down_weight_low_billed_below_top_billed(con_explore):
    out = _by_tconst(
        explore(con_explore, SEED, novelty=False, temporal_gate=False)["results"]
    )
    # nm2016 (ordering=9, a deep cameo) vs. nm2017 (ordering=1, the lead) --
    # identical category, degree, and zero bonus noise (same decade/genre,
    # neither overlapping the seed's), so the whole gap is billing.
    low_billed = out["tt1013"]["score"]
    top_billed = out["tt1014"]["score"]
    assert low_billed < top_billed
    # Top billing (ordering=1) is untouched by the billing factor.
    assert top_billed == pytest.approx(CATEGORY_WEIGHT["actor"] * idf(2), abs=1e-4)


def test_billing_down_weight_crew_unaffected_by_ordering(con_explore):
    out = _by_tconst(
        explore(con_explore, SEED, novelty=False, temporal_gate=False)["results"]
    )
    # nm2018 is a cinematographer at ordering=9 on the seed -- billing is
    # cast-only, so this scores exactly as if ordering were 1.
    assert out["tt1015"]["score"] == pytest.approx(
        CATEGORY_WEIGHT["cinematographer"] * idf(2), abs=1e-4
    )


def test_billing_down_weight_off_flag_no_effect(con_explore):
    out = _by_tconst(explore(
        con_explore, SEED, novelty=False, temporal_gate=False, credit_importance=False,
    )["results"])
    assert out["tt1013"]["score"] == pytest.approx(out["tt1014"]["score"], abs=1e-4)
    assert out["tt1013"]["score"] == pytest.approx(CATEGORY_WEIGHT["actor"] * idf(2), abs=1e-4)


def test_billing_factor_missing_ordering_is_not_penalized():
    # No usable credited/uncredited signal exists in IMDb's free datasets
    # (traced during pre-ship tuning: "uncredited" appears in the
    # characters field on 21 of ~42M actor/actress rows -- noise, not a
    # signal) -- billing is ordering-only. Missing ordering must never be
    # treated as evidence of a cameo.
    assert _billing_factor(None) == 1.0
    assert _billing_factor(1) == 1.0
    assert _billing_factor(9) < 1.0


# --------------------------------------------------------------------------
# thin-data signal
# --------------------------------------------------------------------------


def test_thin_data_signal_fires_on_cast_only(con_explore):
    out = explore(con_explore, "tt1012")
    assert out["thin_data"] is True


def test_thin_data_signal_not_fired_when_crew_present(con_explore):
    out = explore(con_explore, SEED)
    assert out["thin_data"] is False


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
