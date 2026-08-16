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


def test_demo_mode_loads_artifact_without_opening_duckdb(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    assert not at.exception

    at.text_input(key="demo_search_query").input("Winter Ledger")
    at.button(key="demo_search_button").click().run(timeout=30)
    assert not at.exception

    body = " ".join(m.value for m in at.markdown) + " ".join(w.value for w in at.subheader)
    assert "Winter Ledger" in body
    assert "1970" in body


def test_demo_mode_off_roster_query_is_honest(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)

    at.text_input(key="demo_search_query").input("Not In The Roster At All")
    at.button(key="demo_search_button").click().run(timeout=30)

    assert not at.exception
    assert len(at.error) == 1
    assert "isn't in this" in at.error[0].value
    assert "demo roster" in at.error[0].value


def test_demo_mode_follow_person_from_search_result(demo_env):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)

    at.text_input(key="demo_search_query").input("Winter Ledger")
    at.button(key="demo_search_button").click().run(timeout=30)
    assert not at.exception

    # tt9000's director (nm9001) is precomputed -- its Follow button must
    # be present and clickable without touching film.duckdb.
    follow_buttons = [b for b in at.button if b.key and b.key.startswith("demo_follow_")]
    assert follow_buttons, "expected a precomputed Follow(person) button for tt9000's director"
    follow_buttons[0].click().run(timeout=30)
    assert not at.exception

    body = " ".join(m.value for m in at.markdown) + " ".join(w.value for w in at.subheader)
    assert "Follow: Demo Director" in body
