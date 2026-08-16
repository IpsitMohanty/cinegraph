"""Stage 3A: Follow(entity) -- pivot on a graph ENTITY, not a fixed
"cinematographer" feature. An entity has a type; the type determines what
Follow returns. Pandas-free, read-only, importable with duckdb alone.

Entity registry (v1 -- everything buildable from IMDb data already in
film.duckdb, no Wikidata):
  person  -- any credited person (nconst), of any category. Follow(person)
             returns their filmography: every (film, role) row from
             `credits`, oldest first. This is the Gordon Willis / Franco
             Arcalli motion -- "what else did this specific collaborator
             do."
  decade,
  genre   -- context entities (cde.explore.CONTEXT_ENTITY_TYPES).
             Follow(context) returns films in that decade/genre, but
             scoped to the SEED's strong-connector neighbourhood (reusing
             cde.explore.explore()'s candidate set), not the whole 744k-
             film corpus -- unscoped, it would just be a filter, not a
             pivot on the graph.

Registry is deliberately extensible without rearchitecting: STUBBED_ENTITY_
TYPES names the Wikidata-dependent types (company, distributor, work/
based-on, series, movement, festival, location) as registered extension
points. follow() returns a clear not_implemented stub for these -- it does
not build them (Phase A measured that layer too thin to build on pre-
deploy: 33.8% match, movement 0.1%) and it does not crash on them either.

Craft-first presentation: film_view() groups a film's credits with the
craft departments (director, writer, cinematographer, editor, composer,
producer, production_designer) as first-class named groups, cast listed
but secondary -- buildable from `category` alone, no Wikidata needed.

Person-name resolution is LEFT JOIN + COALESCE throughout, same as
cde.explore -- the 502 orphan nconsts from the people load never drop a
row, they resolve to the nconst itself.
"""

from __future__ import annotations

from collections import defaultdict

import duckdb

from cde.explore import CONTEXT_ENTITY_TYPES, DEFAULT_N, _decade, _genre_set, explore

# Craft departments, presentation order. Cast (actor/actress) is always
# appended last, as a single combined "cast" group -- see module
# docstring and cde.explore.CAST_CATEGORIES/STRONG_CONNECTOR_CATEGORIES.
CRAFT_ORDER = (
    "director", "writer", "cinematographer", "editor", "composer",
    "producer", "production_designer",
)

ENTITY_TYPES = frozenset({"person"}) | CONTEXT_ENTITY_TYPES

# Wikidata-dependent extension points -- registered, not built. See
# module docstring.
STUBBED_ENTITY_TYPES = frozenset({
    "company", "distributor", "work", "series", "movement", "festival", "location",
})


def _fetch_person_name(con: duckdb.DuckDBPyConnection, nconst: str) -> str:
    """LEFT JOIN + COALESCE, same pattern as cde.explore._fetch_person_names
    -- an nconst absent from `person` resolves to itself, never a dropped
    row."""
    row = con.execute("""
        SELECT COALESCE(p.primary_name, v.nconst)
        FROM (VALUES (?)) AS v(nconst)
        LEFT JOIN person p ON p.nconst = v.nconst
    """, [nconst]).fetchone()
    return row[0]


# --------------------------------------------------------------------------
# Follow(person)
# --------------------------------------------------------------------------

def follow_person(con: duckdb.DuckDBPyConnection, nconst: str, n: int | None = None) -> dict:
    """That person's filmography: every (film, role) row from `credits`,
    oldest first. Not deduped to one row per film -- a writer-director
    legitimately shows up twice for the same film, which is exactly the
    point of Follow(person) (unlike Explore, which dedupes to one edge per
    person for scoring)."""
    rows = con.execute("""
        SELECT c.tconst, f.primaryTitle, f.startYear, c.category
        FROM credits c
        JOIN film f ON f.tconst = c.tconst
        WHERE c.nconst = ?
        ORDER BY f.startYear NULLS LAST, f.primaryTitle
    """, [nconst]).fetchall()

    films = [
        {"tconst": t, "title": title, "year": year, "role": category}
        for t, title, year, category in rows
    ]
    if n is not None:
        films = films[:n]

    return {
        "entity_type": "person",
        "entity_id": nconst,
        "entity_name": _fetch_person_name(con, nconst),
        "films": films,
    }


# --------------------------------------------------------------------------
# Follow(decade|genre) -- context, scoped to a seed's neighbourhood
# --------------------------------------------------------------------------

def follow_context(
    con: duckdb.DuckDBPyConnection,
    context_type: str,
    value,
    seed_tconst: str,
    n: int = DEFAULT_N,
    person_degree: dict | None = None,
) -> dict:
    """Films matching `value` (a decade like 1970, or a genre string),
    scoped to the seed's strong-connector neighbourhood -- reuses
    cde.explore.explore()'s candidate set rather than filtering the whole
    corpus, so this is a pivot on the graph, not a bare filter."""
    if context_type not in CONTEXT_ENTITY_TYPES:
        raise ValueError(f"not a context entity type: {context_type!r}")

    result = explore(con, seed_tconst, n=10_000, person_degree=person_degree)
    seed = result["seed"]
    cand_tconsts = [r["tconst"] for r in result["results"]]

    films = []
    if cand_tconsts:
        placeholders = ",".join("?" for _ in cand_tconsts)
        rows = con.execute(f"""
            SELECT tconst, primaryTitle, startYear, genres
            FROM film WHERE tconst IN ({placeholders})
        """, cand_tconsts).fetchall()
        for tconst, title, year, genres in rows:
            if context_type == "decade" and _decade(year) == value:
                films.append({"tconst": tconst, "title": title, "year": year})
            elif context_type == "genre" and value in _genre_set(genres):
                films.append({"tconst": tconst, "title": title, "year": year})

    films.sort(key=lambda f: f["year"] if f["year"] is not None else 0)
    return {
        "entity_type": context_type,
        "entity_id": value,
        "seed": seed,
        "films": films[:n],
    }


# --------------------------------------------------------------------------
# Registry dispatch
# --------------------------------------------------------------------------

def follow(
    con: duckdb.DuckDBPyConnection,
    entity_type: str,
    entity_id,
    seed: str | None = None,
    n: int = DEFAULT_N,
    person_degree: dict | None = None,
) -> dict:
    """Dispatch by entity type. Raises ValueError for a genuinely unknown
    type; returns a clear not_implemented stub (not a crash) for a
    registered-but-not-yet-built Wikidata-dependent type."""
    if entity_type == "person":
        return follow_person(con, entity_id, n=n)

    if entity_type in CONTEXT_ENTITY_TYPES:
        if seed is None:
            raise ValueError(f"entity_type {entity_type!r} requires a seed tconst")
        value = int(entity_id) if entity_type == "decade" else entity_id
        return follow_context(con, entity_type, value, seed, n=n, person_degree=person_degree)

    if entity_type in STUBBED_ENTITY_TYPES:
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "status": "not_implemented",
            "reason": (
                "Wikidata-dependent entity type, registered as a post-merge "
                "extension point -- not built pre-deploy (Phase A measured "
                "this layer too thin: 33.8% match, movement 0.1%). "
                "See README / post-ship roadmap."
            ),
        }

    raise ValueError(f"unknown entity_type {entity_type!r}")


# --------------------------------------------------------------------------
# Craft-first film view
# --------------------------------------------------------------------------

def group_credits_craft_first(rows) -> list:
    """rows: iterable of (nconst, name, category). Returns an ordered list
    of {"category": ..., "people": [...]} groups: craft departments first
    in CRAFT_ORDER, then a single combined "cast" group (actor + actress)
    listed but secondary, then any leftover/unrecognized category last
    (defensive, shouldn't happen against `credits` as loaded)."""
    by_cat = defaultdict(list)
    for nconst, name, category in rows:
        by_cat[category].append({"nconst": nconst, "name": name})

    groups = []
    for cat in CRAFT_ORDER:
        if cat in by_cat:
            groups.append({"category": cat, "people": by_cat.pop(cat)})

    cast_people = by_cat.pop("actor", []) + by_cat.pop("actress", [])
    if cast_people:
        groups.append({"category": "cast", "people": cast_people})

    for cat, people in by_cat.items():
        groups.append({"category": cat, "people": people})

    return groups


def film_view(con: duckdb.DuckDBPyConnection, tconst: str) -> dict:
    """A film's followable entities, craft-first. Each person is a
    Follow(person) pivot point."""
    film_row = con.execute(
        "SELECT tconst, primaryTitle, startYear, genres FROM film WHERE tconst = ?", [tconst]
    ).fetchone()
    if film_row is None:
        raise ValueError(f"tconst {tconst!r} not found in film")
    t, title, year, genres = film_row

    rows = con.execute("""
        SELECT c.nconst, COALESCE(p.primary_name, c.nconst), c.category
        FROM credits c
        LEFT JOIN person p ON p.nconst = c.nconst
        WHERE c.tconst = ?
        ORDER BY c.ordering
    """, [tconst]).fetchall()

    return {
        "tconst": t,
        "title": title,
        "year": year,
        "genres": sorted(_genre_set(genres)),
        "groups": group_credits_craft_first(rows),
    }
