"""Thin FastAPI layer for CineGraph Explore / Connect / Follow.

Web-only deps (fastapi, uvicorn) live in requirements-app.txt, not
requirements.txt -- the engine (cde/explore.py, cde/connect.py,
cde/follow.py) is importable with duckdb alone and never imports this
module.

Run: uvicorn app.api:app --reload
"""

from __future__ import annotations

import duckdb
from fastapi import FastAPI, HTTPException, Query

from cde.config import DB_PATH
from cde.connect import (
    DEFAULT_HOP_CAP,
    DEFAULT_TIMEOUT_SECONDS,
    build_strong_person_degree,
    connect,
)
from cde.explore import (
    CREDIT_IMPORTANCE_K,
    CROSS_DIRECTOR_BONUS,
    SAME_DIRECTOR_PENALTY,
    build_person_degree,
    explore,
)
from cde.follow import DEFAULT_N, follow

app = FastAPI(title="CineGraph Explore")

_con = None
_person_degree = None
_strong_person_degree = None

_DATA_MISSING_MESSAGE = (
    f"film.duckdb not found at {DB_PATH}. This backbone is built locally from "
    "the IMDb non-commercial datasets (never redistributed -- see README "
    "'Data & licensing') via `python -m cde.cli build`, or point CDE_DB_PATH "
    "at an existing one."
)


def _get_con():
    """Lazily open a read-only connection and precompute both degree
    caches once per process, reused across requests. Explore/Connect/
    Follow all issue nothing but SELECT against film/credits/person --
    read_only=True holds for the whole API layer (unlike the Streamlit UI,
    which also needs resolve_one_title()).

    Two distinct caches, not one: cde.explore.build_person_degree() counts
    across ALL categories (what Explore's idf needs); Connect needs
    strong-connector-only degree (cde.connect.build_strong_person_degree())
    -- passing the wrong one silently understates rarity for anyone who
    also has cast credits. See cde/connect.py's docstring."""
    global _con, _person_degree, _strong_person_degree
    if _con is None:
        if not DB_PATH.exists():
            # A clear, actionable 503 -- not a raw DuckDB IOError/traceback.
            raise HTTPException(status_code=503, detail=_DATA_MISSING_MESSAGE)
        _con = duckdb.connect(str(DB_PATH), read_only=True)
        _person_degree = build_person_degree(_con)
        _strong_person_degree = build_strong_person_degree(_con)
    return _con


@app.get("/explore/{tconst}")
def get_explore(
    tconst: str,
    n: int = Query(20, ge=1, le=100),
    novelty: bool = True,
    same_director_penalty: float = SAME_DIRECTOR_PENALTY,
    cross_director_bonus_enabled: bool = False,
    cross_director_bonus: float = CROSS_DIRECTOR_BONUS,
    temporal_gate: bool = True,
    credit_importance: bool = True,
    credit_importance_k: float = CREDIT_IMPORTANCE_K,
):
    """Film in -> ranked, explained connected films out. Defaults mirror
    cde.explore.explore()'s -- see its docstring for what each flag does."""
    con = _get_con()
    try:
        return explore(
            con, tconst, n=n, novelty=novelty,
            same_director_penalty=same_director_penalty,
            cross_director_bonus_enabled=cross_director_bonus_enabled,
            cross_director_bonus=cross_director_bonus,
            temporal_gate=temporal_gate,
            credit_importance=credit_importance,
            credit_importance_k=credit_importance_k,
            person_degree=_person_degree,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/connect")
def get_connect(
    a: str = Query(..., description="tconst of the first film"),
    b: str = Query(..., description="tconst of the second film"),
    hop_cap: int = Query(DEFAULT_HOP_CAP, ge=1, le=8),
    timeout_seconds: float = Query(DEFAULT_TIMEOUT_SECONDS, ge=1, le=60),
):
    """Strongest strong-connector-only path between two films. See
    cde.connect.connect() -- returns found=False (not a 404, and never a
    relaxed/fallback path) when no strong path exists within hop_cap or
    the search exceeds timeout_seconds (result["timed_out"] says which); a
    404 means a or b isn't in the backbone at all."""
    con = _get_con()
    try:
        return connect(
            con, a, b, hop_cap=hop_cap, person_degree=_strong_person_degree,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/follow")
def get_follow(
    entity_type: str = Query(..., description="person | decade | genre | (stubbed types)"),
    id: str = Query(..., description="nconst for person; year for decade; name for genre"),
    seed: str | None = Query(None, description="seed tconst -- required for decade/genre"),
    n: int = Query(DEFAULT_N, ge=1, le=200),
):
    """Pivot on a graph entity. See cde.follow.follow() -- a registered-
    but-not-yet-built (Wikidata-dependent) entity type returns a
    not_implemented stub, not a 404; an unknown type is a 404."""
    con = _get_con()
    try:
        return follow(con, entity_type, id, seed=seed, n=n, person_degree=_person_degree)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
