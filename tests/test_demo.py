"""cde.demo tests: the artifact generator emits results-only data -- never
a reshaping of the credits/person tables -- against a small hand-built
(film, credits, person) db.

Fixture (titles deliberately unrelated -- a shared prefix would trip the
franchise-pair heuristic, as this test file discovered the hard way):
  tt9000 "Winter Ledger" (1970, Drama)   -- nm9001 director (shared with
                                             tt9001), nm9003 actor (cast),
                                             nm9004 writer (shared with
                                             tt9002 -- the Connect link).
  tt9001 "Autumn Circuit" (1971, Drama)  -- shares nm9001 with tt9000.
  tt9002 "Coastal Harbor" (1980, Mystery) -- nm9002 cinematographer (shared
                                              with tt9003), nm9004 writer
                                              (shared with tt9000).
  tt9003 "Northern Static" (1981, Mystery) -- shares nm9002 with tt9002.
"""

import json

import duckdb
import pytest

from cde.demo import build_artifact, demo_connect, demo_seed, load_artifact, save_artifact

ROSTER = ["tt9000", "tt9002"]
PAIRS = [("tt9000", "tt9002")]

# Keys that would signal a raw table dump rather than a computed result --
# none of these should ever appear anywhere in the artifact.
FORBIDDEN_KEYS = {"credits", "ordering", "ratings", "imdb_rating", "imdb_votes"}


def _build_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR
        )
    """)
    con.executemany("INSERT INTO film VALUES (?, ?, ?, ?)", [
        ("tt9000", "Winter Ledger", 1970, "Drama"),
        ("tt9001", "Autumn Circuit", 1971, "Drama"),
        ("tt9002", "Coastal Harbor", 1980, "Mystery"),
        ("tt9003", "Northern Static", 1981, "Mystery"),
    ])

    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", [
        ("tt9000", "nm9001", "director", 1),
        ("tt9001", "nm9001", "director", 1),
        ("tt9000", "nm9003", "actor", 2),
        ("tt9000", "nm9004", "writer", 3),
        ("tt9002", "nm9004", "writer", 1),
        ("tt9002", "nm9002", "cinematographer", 1),
        ("tt9003", "nm9002", "cinematographer", 1),
    ])

    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, NULL, NULL)", [
        ("nm9001", "Demo Director"),
        ("nm9002", "Demo Cinematographer"),
        ("nm9003", "Demo Actor"),
        ("nm9004", "Demo Writer"),
    ])
    return con


@pytest.fixture
def con_demo():
    con = _build_db()
    yield con
    con.close()


@pytest.fixture
def artifact(con_demo):
    return build_artifact(con_demo, roster=ROSTER, pairs=PAIRS)


# --------------------------------------------------------------------------
# results-only guarantee
# --------------------------------------------------------------------------


def _walk_keys(obj):
    """All dict keys anywhere in a nested structure."""
    keys = set()
    if isinstance(obj, dict):
        keys |= set(obj.keys())
        for v in obj.values():
            keys |= _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _walk_keys(item)
    return keys


def test_artifact_has_no_raw_table_dump_keys(artifact):
    all_keys = _walk_keys(artifact)
    leaked = all_keys & FORBIDDEN_KEYS
    assert not leaked, f"artifact leaks raw-table-shaped keys: {leaked}"


def test_artifact_top_level_shape(artifact):
    assert set(artifact.keys()) == {"meta", "seeds", "connect_pairs"}
    assert set(artifact["seeds"].keys()) == set(ROSTER)
    assert set(artifact["connect_pairs"].keys()) == {"tt9000|tt9002"}


def test_seed_entry_shape_is_results_only(artifact):
    seed = artifact["seeds"]["tt9000"]
    assert set(seed.keys()) == {
        "tconst", "title", "year", "genres", "thin_data",
        "film_view", "explore", "follow_people", "follow_contexts",
    }
    # film_view groups are name/nconst pairs, not raw credits rows.
    for group in seed["film_view"]["groups"]:
        for person in group["people"]:
            assert set(person.keys()) == {"nconst", "name"}


def test_artifact_is_plain_json_serializable(artifact):
    # Round-trips with the stdlib encoder alone -- proves everything in it
    # is plain dict/list/str/int/float/bool/None, nothing exotic.
    round_tripped = json.loads(json.dumps(artifact))
    assert round_tripped == artifact


# --------------------------------------------------------------------------
# content correctness
# --------------------------------------------------------------------------


def test_explore_results_present_for_seed(artifact):
    seed = artifact["seeds"]["tt9000"]
    result_tconsts = {r["tconst"] for r in seed["explore"]["results"]}
    assert "tt9001" in result_tconsts  # via nm9001 director
    assert "tt9002" in result_tconsts  # via nm9004 writer


def test_follow_people_precomputed(artifact):
    seed = artifact["seeds"]["tt9000"]
    assert seed["follow_people"], "expected at least one precomputed Follow(person)"
    for nconst, follow_result in seed["follow_people"].items():
        assert follow_result["entity_type"] == "person"
        assert follow_result["entity_id"] == nconst


def test_follow_contexts_precomputed(artifact):
    seed = artifact["seeds"]["tt9000"]
    entity_types = {fc["entity_type"] for fc in seed["follow_contexts"]}
    assert "decade" in entity_types


def test_connect_pair_found(artifact):
    result = demo_connect(artifact, "tt9000", "tt9002")
    assert result is not None
    assert result["found"] is True
    people = [c["person_name"] for c in result["chain"] if "person_name" in c]
    assert people == ["Demo Writer"]


def test_connect_pair_lookup_either_order(artifact):
    # demo_connect() must find the pair regardless of which order the UI
    # happens to ask in.
    assert demo_connect(artifact, "tt9002", "tt9000") is not None


def test_demo_seed_lookup(artifact):
    assert demo_seed(artifact, "tt9000")["title"] == "Winter Ledger"
    assert demo_seed(artifact, "tt_not_in_roster") is None


# --------------------------------------------------------------------------
# save / load round trip
# --------------------------------------------------------------------------


def test_save_and_load_round_trip(artifact, tmp_path):
    path = tmp_path / "artifact.json"
    save_artifact(artifact, path)
    loaded = load_artifact(path)
    assert loaded == artifact
