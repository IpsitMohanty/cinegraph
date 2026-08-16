"""app/streamlit_app.py demo-mode tests: CDE_DEMO_MODE=1 must browse the
precomputed artifact end-to-end and never open film.duckdb -- proven here
by making duckdb.connect() raise if it's ever called, not just by reading
the code. An off-roster query must return the honest "not in this demo"
message, never an error or an empty hang.

Uses Streamlit's official headless AppTest harness (streamlit.testing.v1)
to drive the real script, same as the live-mode verification in the
deploy-surface brief -- this catches real Streamlit-level bugs (widget/
session_state key collisions, etc.) a code read wouldn't.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

import cde.demo as demo_mod
from cde.demo import build_artifact, save_artifact

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")

# Same small fixture shape as tests/test_demo.py -- titles deliberately
# unrelated (a shared prefix trips the franchise-pair heuristic).
ROSTER = ["tt9000", "tt9002"]
PAIRS = [("tt9000", "tt9002")]


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
def demo_env(tmp_path, monkeypatch):
    """A tiny fixture artifact on disk, CDE_DEMO_MODE on, and
    duckdb.connect() booby-trapped to fail the test loudly if demo mode
    ever tries to open a real database."""
    con = _build_db()
    artifact = build_artifact(con, roster=ROSTER, pairs=PAIRS)
    con.close()
    artifact_path = tmp_path / "artifact.json"
    save_artifact(artifact, artifact_path)

    # app/streamlit_app.py does `from cde.demo import ARTIFACT_PATH` at
    # script-exec time (AppTest re-execs the file each .run()), so
    # patching the attribute here is picked up fresh each run.
    monkeypatch.setattr(demo_mod, "ARTIFACT_PATH", artifact_path)
    monkeypatch.setenv("CDE_DEMO_MODE", "1")

    def _forbidden_connect(*args, **kwargs):
        raise AssertionError(
            "demo mode called duckdb.connect() -- it must never open film.duckdb"
        )
    monkeypatch.setattr(duckdb, "connect", _forbidden_connect)

    return artifact_path


def _submit_demo_search(at, query):
    """Fills the search box and submits the st.form -- st.form_submit_button
    shows up under at.button (Streamlit represents it as a Button with a
    "FormSubmitter:<form_id>-<label>" key), found here by label rather than
    hardcoding that key format. A form's submit button is what Enter in the
    field triggers too -- Streamlit treats both as the same submission
    event, so this is the sanctioned proxy for "Enter submits" (AppTest has
    no raw-keyboard-event simulation)."""
    at.text_input(key="demo_search_query").input(query)
    submit = [b for b in at.button if b.label == "Search"][0]
    submit.click().run(timeout=30)


def test_demo_mode_loads_artifact_without_opening_duckdb(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    assert not at.exception

    _submit_demo_search(at, "Winter Ledger")
    assert not at.exception

    body = " ".join(m.value for m in at.markdown) + " ".join(w.value for w in at.subheader)
    assert "Winter Ledger" in body
    assert "1970" in body


def test_demo_mode_off_roster_query_is_honest(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)

    _submit_demo_search(at, "Not In The Roster At All")

    assert not at.exception
    assert len(at.error) == 1
    assert "isn't in this" in at.error[0].value
    assert "demo roster" in at.error[0].value


def test_demo_mode_follow_person_from_search_result(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)

    _submit_demo_search(at, "Winter Ledger")
    assert not at.exception

    # tt9000's director (nm9001) is precomputed -- its Follow button must
    # be present and clickable without touching film.duckdb.
    follow_buttons = [b for b in at.button if b.key and b.key.startswith("demo_follow_")]
    assert follow_buttons, "expected a precomputed Follow(person) button for tt9000's director"
    follow_buttons[0].click().run(timeout=30)
    assert not at.exception

    body = " ".join(m.value for m in at.markdown) + " ".join(w.value for w in at.subheader)
    assert "Follow: Demo Director" in body


def test_demo_mode_browse_list_click_selects_film(demo_env):
    """Fix 2: clicking a title in the browse-all list must render that
    film's result, through the exact same demo_explore_tconst
    session_state key search uses -- no second selection path."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    assert not at.exception

    browse_buttons = [b for b in at.button if b.key and b.key.startswith("demo_browse_")]
    assert len(browse_buttons) == len(ROSTER), "expected one browse button per roster film"

    target = [b for b in browse_buttons if b.label.startswith("Coastal Harbor")][0]
    target.click().run(timeout=30)
    assert not at.exception
    assert at.session_state["demo_explore_tconst"] == "tt9002"

    body = " ".join(m.value for m in at.markdown) + " ".join(w.value for w in at.subheader)
    assert "Coastal Harbor" in body
    assert "1980" in body


def test_demo_mode_cast_names_deduped():
    """Fix 3: a person credited on more than one `credits` row for the same
    film/category must render once, not "Name, Name". Doesn't use the
    demo_env fixture -- it needs its own real duckdb.connect() to build a
    tiny fixture artifact, which demo_env's booby-trap (correctly) forbids."""
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE film (
            tconst VARCHAR, primaryTitle VARCHAR, startYear INTEGER, genres VARCHAR
        )
    """)
    con.execute("INSERT INTO film VALUES ('tt9500', 'Duplicate Billing', 1990, 'Drama')")
    con.execute("""
        CREATE TABLE credits (
            tconst VARCHAR, nconst VARCHAR, category VARCHAR, ordering INTEGER
        )
    """)
    con.executemany("INSERT INTO credits VALUES (?, ?, ?, ?)", [
        ("tt9500", "nm9500", "actor", 1),
        ("tt9500", "nm9500", "actor", 2),  # same person, second ordering row
        ("tt9500", "nm9501", "director", 3),
    ])
    con.execute("""
        CREATE TABLE person (
            nconst VARCHAR, primary_name VARCHAR, birth_year INTEGER, death_year INTEGER
        )
    """)
    con.executemany("INSERT INTO person VALUES (?, ?, NULL, NULL)", [
        ("nm9500", "Repeat Player"),
        ("nm9501", "Solo Director"),
    ])
    artifact = build_artifact(con, roster=["tt9500"], pairs=[])
    con.close()

    # film_view() itself (cde/follow.py) is untouched -- the raw, undeduped
    # rows are still there. This asserts the fixture actually has the
    # duplicate (so the test would fail if the fixture stopped exercising
    # the bug), then that app.streamlit_app's own dedupe helper collapses it.
    raw_names = [
        p["name"]
        for g in artifact["seeds"]["tt9500"]["film_view"]["groups"]
        for p in g["people"]
    ]
    assert raw_names.count("Repeat Player") == 2, "fixture no longer exercises the dup case"

    import app.streamlit_app as entrypoint
    deduped_names = [
        p["name"]
        for g in artifact["seeds"]["tt9500"]["film_view"]["groups"]
        for p in entrypoint._dedupe_people(g["people"])
    ]
    assert deduped_names.count("Repeat Player") == 1


def test_entrypoint_import_chain_never_opens_duckdb_in_demo_mode(monkeypatch):
    """Regression test for the deploy-importfix bug: the traceback (\"No
    module named 'cde'\") was at MODULE IMPORT time, before main()'s
    demo-mode branch ever ran -- the earlier AppTest-based tests above
    proved the demo *renderer* never opens film.duckdb, but AppTest execs
    the whole script fresh on every .run(), which can mask an import-chain
    regression if cde happens to already be resolvable in the test
    process (as it is here -- the actual missing-package failure only
    reproduces in a genuinely clean install, verified separately when
    fixing this). This test instead locks in the narrower, durable
    property directly: importing app.streamlit_app's whole chain (cde.config,
    cde.connect, cde.demo, cde.explore, cde.follow, cde.resolve) in demo
    mode, with no film.duckdb reachable, must never call duckdb.connect().
    importlib.reload() forces app.streamlit_app's own top-level statements
    to genuinely re-run rather than short-circuiting via sys.modules.
    """
    monkeypatch.setenv("CDE_DEMO_MODE", "1")
    monkeypatch.setenv("CDE_DB_PATH", "/nonexistent/film.duckdb")

    def _forbidden_connect(*args, **kwargs):
        raise AssertionError(
            "importing the entrypoint called duckdb.connect() -- "
            "import-time must never open film.duckdb"
        )
    monkeypatch.setattr(duckdb, "connect", _forbidden_connect)

    import app.streamlit_app as entrypoint
    importlib.reload(entrypoint)
    assert entrypoint.DEMO_MODE is True
