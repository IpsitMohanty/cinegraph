"""Stage 3A: CineGraph Explore -- the walking skeleton.

Film in -> ranked, explained connected films out. Reads `film`, `credits`,
`person` from film.duckdb; never writes to it (read-only). Pandas-free,
importable with `duckdb` alone -- no web dependency belongs in this module
(see app/ for the FastAPI/Streamlit layer and requirements-app.txt).

Algorithm, for a seed film F:
  1. people_F = credits rows for F (nconst, category). If a person has more
     than one category on F (rare, e.g. writer-director), the higher-
     weighted category wins for scoring/display -- one edge per person.
  2. Candidates = other films sharing >=1 person with F (join credits on
     nconst). A person whose only credit is F contributes no candidate --
     that falls out of the join for free.
  3. edge_weight(p) = category_weight[role_on_F(p)] * idf(degree(p)), where
     degree(p) is p's distinct-film count across all of `credits` and
     idf(d) = 1 / log2(1 + d) -- rarer collaborator, stronger edge.
  4. score(C) = sum(edge_weight over shared people) + small fixed bonuses
     for shared decade / shared genre -- deliberately much smaller than one
     specific-person edge, so a shared rare cinematographer beats a shared
     genre. This encodes "genre/period weak, collaborator strong."
  5. Triviality handling, not via votes:
       - franchise/near-duplicate: a crude v1 heuristic drops candidates
         whose title shares a long common prefix with F's (sequels). Real
         series data (Wikidata P179) is the proper fix, parked with the
         Wikidata merge.
       - novelty=True down-weights (not zeroes) the specific edge
         contribution of a person who is F's director AND is also credited
         as director on the candidate -- so Explore doesn't just return the
         rest of one filmography. Off by default; same-director candidates
         stay visible but rarely dominate anyway, since idf already
         discounts anyone with many films (directors included).
  6. Top-N by score, each with its explanation.

Votes are NOT in this file. `imdb_rating`/`imdb_votes` never appear in
scoring, ranking, or novelty -- that line is deliberate and non-negotiable
(see README "Stance"); grep for confirmation if in doubt.

CATEGORY_WEIGHT, DECADE_BONUS, GENRE_BONUS_*, FRANCHISE_*, and
NOVELTY_DIRECTOR_DAMPING are hand-set PRIORS for this walking skeleton --
to be tuned by eval_explore.py's judgment pass, not treated as truths.

Capability boundaries (also in README): era/genre are explicit, read
straight from `film`. Movement is never a label the engine prints -- it
only ever shows the shared people/period that would constitute one to a
human reader. Visual style / mise-en-scene is out by design: this engine
shows the structural substrate (who / when / what-genre), never an
asserted aesthetic judgment, and never an LLM tag.
"""

from __future__ import annotations

import math
from collections import defaultdict

import duckdb

# --------------------------------------------------------------------------
# Priors (tune via eval_explore.py, not by reflex)
# --------------------------------------------------------------------------

CATEGORY_WEIGHT = {
    "cinematographer": 1.0,
    "composer": 0.9,
    "editor": 0.85,
    "writer": 0.7,
    "director": 0.6,
    "production_designer": 0.5,
    "producer": 0.3,
    "actor": 0.2,
    "actress": 0.2,
}

# Small fixed bonuses -- deliberately much smaller than a real
# specific-person edge (see test_bonus_smaller_than_specific_person_edge).
DECADE_BONUS = 0.1
GENRE_BONUS_PER_GENRE = 0.05
GENRE_BONUS_CAP_GENRES = 2  # cap so a multi-genre overlap can't outrun an edge

# Franchise/near-duplicate heuristic: crude v1, title-prefix based. See
# module docstring -- proper series data comes with the Wikidata merge.
FRANCHISE_MIN_PREFIX = 5
FRANCHISE_MIN_FRACTION = 0.6

# novelty=True damping factor applied to a same-director edge's weight
# (not the whole candidate's score -- other shared people still count).
NOVELTY_DIRECTOR_DAMPING = 0.3

DEFAULT_N = 20


# --------------------------------------------------------------------------
# Small math / string helpers
# --------------------------------------------------------------------------

def idf(degree: int) -> float:
    """1 / log2(1 + degree). Rarer collaborator (lower degree) -> larger
    weight. degree is always >= 1 for anyone who appears in `credits`."""
    return 1.0 / math.log2(1 + max(degree, 1))


def _decade(year):
    return (year // 10) * 10 if year is not None else None


def _genre_set(genres) -> set:
    if not genres:
        return set()
    return {g.strip() for g in genres.split(",") if g.strip()}


def _common_prefix_len(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _is_franchise_pair(title_f: str, title_c: str) -> bool:
    """Crude v1: flag C as a likely sequel/near-duplicate of F when they
    share a long common title prefix relative to the shorter title."""
    if not title_f or not title_c:
        return False
    shorter = min(len(title_f), len(title_c))
    if shorter == 0:
        return False
    prefix = _common_prefix_len(title_f, title_c)
    return prefix >= FRANCHISE_MIN_PREFIX and (prefix / shorter) >= FRANCHISE_MIN_FRACTION


# --------------------------------------------------------------------------
# DB access helpers (all read-only)
# --------------------------------------------------------------------------

def build_person_degree(con: duckdb.DuckDBPyConnection) -> dict:
    """Precompute distinct-film degree for every person in `credits`, once.
    Read-only -- no table is created in `con`'s underlying database.
    Callers (e.g. the API layer) should build this once at process startup
    and pass it into explore() to skip a full-table aggregation per
    request; explore() also works without it, computing degree on demand
    for just the nconsts relevant to that seed."""
    rows = con.execute("""
        SELECT nconst, COUNT(DISTINCT tconst) AS degree
        FROM credits GROUP BY nconst
    """).fetchall()
    return dict(rows)


def _get_film(con, tconst):
    row = con.execute(
        "SELECT tconst, primaryTitle, startYear, genres FROM film WHERE tconst = ?",
        [tconst],
    ).fetchone()
    if row is None:
        return None
    t, title, year, genres = row
    return {"tconst": t, "title": title, "year": year, "genres": _genre_set(genres)}


def _fetch_films(con, tconsts):
    if not tconsts:
        return {}
    placeholders = ",".join("?" for _ in tconsts)
    rows = con.execute(f"""
        SELECT tconst, primaryTitle, startYear, genres
        FROM film WHERE tconst IN ({placeholders})
    """, tconsts).fetchall()
    return {
        t: {"title": title, "year": year, "genres": _genre_set(genres)}
        for t, title, year, genres in rows
    }


def _fetch_degree(con, nconsts):
    if not nconsts:
        return {}
    placeholders = ",".join("?" for _ in nconsts)
    rows = con.execute(f"""
        SELECT nconst, COUNT(DISTINCT tconst) AS degree
        FROM credits WHERE nconst IN ({placeholders})
        GROUP BY nconst
    """, nconsts).fetchall()
    return dict(rows)


def _fetch_person_names(con, nconsts):
    """LEFT JOIN person + COALESCE -- an nconst with no `person` row (the
    502 name.basics orphans from the people load) still resolves to a
    name: itself, not a dropped edge."""
    if not nconsts:
        return {}
    values_sql = ", ".join("(?)" for _ in nconsts)
    rows = con.execute(f"""
        SELECT v.nconst, COALESCE(p.primary_name, v.nconst) AS name
        FROM (VALUES {values_sql}) AS v(nconst)
        LEFT JOIN person p ON p.nconst = v.nconst
    """, nconsts).fetchall()
    return dict(rows)


# --------------------------------------------------------------------------
# Explanation formatting
# --------------------------------------------------------------------------

def _format_explanation(connections, shared_decade, shared_genres):
    parts = []
    if connections:
        top = ", ".join(f"{c['person_name']} ({c['role']})" for c in connections[:3])
        parts.append(f"connected through: {top}")
    if shared_decade is not None:
        parts.append(f"shared decade: {shared_decade}s")
    if shared_genres:
        parts.append(f"shared genres: {', '.join(shared_genres)}")
    return "; ".join(parts) if parts else "genre/decade only"


# --------------------------------------------------------------------------
# Explore
# --------------------------------------------------------------------------

def explore(con: duckdb.DuckDBPyConnection, tconst: str, n: int = DEFAULT_N,
            novelty: bool = False, person_degree: dict | None = None) -> dict:
    """Film in -> ranked, explained connected films out.

    Returns {"seed": {...}, "results": [...]}. Each result is a legible
    record (title, year, score, connections, shared_decade, shared_genres,
    explanation) -- never a graph blob. Raises ValueError if `tconst` isn't
    in `film`.
    """
    seed = _get_film(con, tconst)
    if seed is None:
        raise ValueError(f"tconst {tconst!r} not found in film")

    people_f_rows = con.execute(
        "SELECT nconst, category FROM credits WHERE tconst = ?", [tconst]
    ).fetchall()
    if not people_f_rows:
        return {"seed": _seed_out(seed), "results": []}

    # One role per person on F: if credited in multiple categories, the
    # higher-weighted one wins (one edge per shared person, not one per
    # category row).
    roles_f = {}
    for nconst, category in people_f_rows:
        w = CATEGORY_WEIGHT.get(category, 0.0)
        if nconst not in roles_f or w > CATEGORY_WEIGHT.get(roles_f[nconst], 0.0):
            roles_f[nconst] = category

    director_nconsts_f = {nc for nc, cat in people_f_rows if cat == "director"}

    nconsts = list(roles_f.keys())
    placeholders = ",".join("?" for _ in nconsts)
    candidate_rows = con.execute(f"""
        SELECT DISTINCT tconst, nconst FROM credits
        WHERE nconst IN ({placeholders}) AND tconst != ?
    """, nconsts + [tconst]).fetchall()

    if not candidate_rows:
        return {"seed": _seed_out(seed), "results": []}

    by_candidate = defaultdict(list)
    for cand_tconst, nconst in candidate_rows:
        by_candidate[cand_tconst].append(nconst)

    if person_degree is not None:
        degree = {nc: person_degree.get(nc, 1) for nc in nconsts}
    else:
        degree = _fetch_degree(con, nconsts)

    same_director_pairs = set()
    if novelty and director_nconsts_f:
        dir_list = list(director_nconsts_f)
        dir_ph = ",".join("?" for _ in dir_list)
        rows = con.execute(f"""
            SELECT DISTINCT tconst, nconst FROM credits
            WHERE nconst IN ({dir_ph}) AND category = 'director' AND tconst != ?
        """, dir_list + [tconst]).fetchall()
        same_director_pairs = {(t, nc) for t, nc in rows}

    films_info = _fetch_films(con, list(by_candidate.keys()))
    person_names = _fetch_person_names(con, nconsts)

    results = []
    for cand_tconst, shared_nconsts in by_candidate.items():
        cand = films_info.get(cand_tconst)
        if cand is None:
            continue
        if _is_franchise_pair(seed["title"], cand["title"]):
            continue

        connections = []
        people_score = 0.0
        for nconst in shared_nconsts:
            role = roles_f[nconst]
            weight = CATEGORY_WEIGHT.get(role, 0.0) * idf(degree.get(nconst, 1))
            if novelty and (cand_tconst, nconst) in same_director_pairs:
                weight *= NOVELTY_DIRECTOR_DAMPING
            people_score += weight
            connections.append({
                "person_name": person_names.get(nconst, nconst),
                "role": role,
                "weight": round(weight, 4),
            })
        connections.sort(key=lambda c: c["weight"], reverse=True)

        seed_decade = _decade(seed["year"])
        cand_decade = _decade(cand["year"])
        shared_decade_hit = (
            seed_decade is not None and seed_decade == cand_decade
        )
        shared_genres = sorted(seed["genres"] & cand["genres"])

        bonus = 0.0
        if shared_decade_hit:
            bonus += DECADE_BONUS
        if shared_genres:
            bonus += GENRE_BONUS_PER_GENRE * min(len(shared_genres), GENRE_BONUS_CAP_GENRES)

        total_score = people_score + bonus

        results.append({
            "tconst": cand_tconst,
            "title": cand["title"],
            "year": cand["year"],
            "score": round(total_score, 4),
            "connections": connections,
            "shared_decade": cand_decade if shared_decade_hit else None,
            "shared_genres": shared_genres,
            "explanation": _format_explanation(
                connections, cand_decade if shared_decade_hit else None, shared_genres
            ),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"seed": _seed_out(seed), "results": results[:n]}


def _seed_out(seed):
    return {
        "tconst": seed["tconst"],
        "title": seed["title"],
        "year": seed["year"],
        "genres": sorted(seed["genres"]),
    }
