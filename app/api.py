"""Thin FastAPI layer for CineGraph Explore.

Web-only deps (fastapi, uvicorn) live in requirements-app.txt, not
requirements.txt -- the engine (cde/explore.py) is importable with duckdb
alone and never imports this module.

Run: uvicorn app.api:app --reload
"""

from __future__ import annotations

import duckdb
from fastapi import FastAPI, HTTPException, Query

from cde.config import DB_PATH
from cde.explore import build_person_degree, explore

app = FastAPI(title="CineGraph Explore")

_con = None
_person_degree = None


def _get_con():
    """Lazily open a read-only connection and precompute person_degree
    once per process, reused across requests."""
    global _con, _person_degree
    if _con is None:
        _con = duckdb.connect(str(DB_PATH), read_only=True)
        _person_degree = build_person_degree(_con)
    return _con


@app.get("/explore/{tconst}")
def get_explore(
    tconst: str,
    n: int = Query(20, ge=1, le=100),
    novelty: bool = False,
):
    """Film in -> ranked, explained connected films out."""
    con = _get_con()
    try:
        return explore(con, tconst, n=n, novelty=novelty, person_degree=_person_degree)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
