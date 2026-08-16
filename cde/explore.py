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
       - novelty=True (default -- see stage 3A tuning) down-weights, not
         zeroes, the specific edge contribution of a person who is F's
         director AND is also credited as director on the candidate, by
         `same_director_penalty` -- so Explore doesn't just return the rest
         of one filmography. Demote, don't drop: the tuning eval found
         same-director results were STILL judged interesting 37% of the
         time, so a same-director candidate stays visible, just ranked on
         its other merits. An optional, OFF-by-default cross_director_bonus
         rewards a shared craftsperson connecting two DIFFERENT directors
         (the "lateral jump" the eval judged best) -- provisional, since
         the eval that surfaced it was confounded with the thin-data/
         rescore failure mode below, not yet cleanly isolated.
       - temporal_gate=True (default) drops a specific person-edge that
         can't be real: (a) universally, any category, when the person's
         birth_year postdates the older of the two films -- nobody credited
         on a film before they were born, no exceptions; (b) for non-writer
         categories, when the person's death_year precedes the older film
         -- their working window can't span both; (c) a composer with NO
         birth_year at all connecting films more than
         `temporal_gate_year_gap` years apart -- the rescore/re-release
         signature (a modern band or arranger credited on a remastered
         reissue of an old film) that (a)/(b) can't catch since there's no
         birth data to compare. Rule (b) deliberately EXEMPTS writer: a
         source-material author credited posthumously on an adaptation
         decades later (Akutagawa -> The Outrage; Bandyopadhyay across the
         Apu trilogy) is real adaptation lineage, not a data error -- the
         tuning eval traced exactly this false positive and it's excluded
         on purpose. If every one of a candidate's person-edges is dropped
         by this gate, the candidate itself is dropped (no bonus-only
         candidates).
  6. credit_importance=True (default -- pre-ship tuning) scales a CAST
     (actor/actress) edge's weight by the person's billing on the seed:
     `1 / (1 + credit_importance_k * (ordering - 1))`, so the top-billed
     lead (ordering=1) is untouched and a deep cameo is progressively
     weaker. This is cast-only, deliberately -- cinematographer/editor/
     composer are already the strong edges and aren't the problem; cast is
     the triple-suppressed category (low category_weight + high idf-degree
     discount + now billing), aimed at a low-billed cameo (Benazeraf's bit
     part in Breathless) dominating discovery on a thin-crew film. IMDb's
     free datasets carry no clean, usable credited/uncredited flag (traced
     during pre-ship tuning: "uncredited" appears in the characters field
     on 21 of ~42M actor/actress rows -- noise, not a signal), so this is
     billing-order only, not a credited/uncredited check -- no such flag is
     fabricated.
  7. Top-N by score, each with its explanation. `thin_data` is set on the
     result when the seed's own `credits` rows never include a
     non-cast/actress category at all (Breathless: 10 principals rows, all
     cast) -- that's a source-coverage gap no scoring rule can fix,
     billing-aware or not; it's signalled, not patched over.

Votes are NOT in this file. `imdb_rating`/`imdb_votes` never appear in
scoring, ranking, or novelty -- that line is deliberate and non-negotiable
(see README "Stance"); grep for confirmation if in doubt.

CATEGORY_WEIGHT, DECADE_BONUS, GENRE_BONUS_*, FRANCHISE_*,
SAME_DIRECTOR_PENALTY, CROSS_DIRECTOR_BONUS, TEMPORAL_GATE_YEAR_GAP, and
CREDIT_IMPORTANCE_K are hand-set PRIORS for this walking skeleton --
SAME_DIRECTOR_PENALTY, the temporal gate, and CREDIT_IMPORTANCE_K were each
tuned once, against pre-registered human evals (PREDICTIONS_tuning.md);
the rest remain to be tuned the same way, not treated as truths. This is
the last scoring change before Connect/Follow -- crew-clique list
redundancy (Tokyo Story, Pather Panchali, In the Mood ranks 1-6) is a
list-level diversity problem (MMR/xQuAD), not a scalar-weight one, and is
explicitly deferred to a post-ship reranker rather than chased here with
more priors.

STRONG_CONNECTOR_CATEGORIES and CONTEXT_ENTITY_TYPES (below) are the edge-
tier classification shared with cde.connect (strongest-path between two
films, traversing strong-connector edges only) and cde.follow (pivot on a
graph entity -- person, or a context entity scoped to a seed's
neighbourhood). Both reuse this module's CATEGORY_WEIGHT/idf and several
of its DB helpers directly rather than re-deriving them.

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

# novelty=True (default) damping factor applied to a same-director edge's
# weight (not the whole candidate's score -- other shared people still
# count). Calibrated against the tuning eval: same-director rows were ~10x
# more likely trivial than cross-director, but still interesting 37% of the
# time -- 0.4 demotes without dropping.
SAME_DIRECTOR_PENALTY = 0.4

# cross_director_bonus_enabled=True (OFF by default) additive bonus when a
# shared craftsperson connects the seed to a candidate with a different,
# specifically-identified director. Provisional: the eval signal for this
# was confounded with the thin-data/rescore failure mode, not yet cleanly
# isolated -- see module docstring.
CROSS_DIRECTOR_BONUS = 0.1

# temporal_gate=True (default) rescore/band-clause threshold: a composer
# with no birth_year connecting films more than this many years apart is
# treated as a modern rescore/re-release credit, not a real collaboration.
TEMPORAL_GATE_YEAR_GAP = 40

# credit_importance=True (default) billing down-weight, cast only. Started
# conservative per the pre-ship brief: ordering=1 (top-billed lead) is
# never penalized; a deep cameo's edge shrinks progressively. Not a
# credited/uncredited flag -- IMDb's free datasets don't carry one cleanly
# (see module docstring).
CAST_CATEGORIES = frozenset({"actor", "actress"})
CREDIT_IMPORTANCE_K = 0.15

# Edge tiers (stage 3A Connect/Follow -- encoded once here, shared by
# cde.connect and cde.follow so the classification can't drift between
# actions):
#   STRONG CONNECTOR -- the craft-person edges: can bridge two films in
#     Connect, and are the strong edges in Explore/Follow. Derived from
#     CATEGORY_WEIGHT so it can never fall out of sync with it -- every
#     category there except cast.
#   CONTEXT-ONLY -- decade, genre. They annotate/score; they can be
#     Followed, but they never form a Connect hop ("both 1970s" is not a
#     path).
#   CONDITIONAL (not built in v1) -- cast, and later company/festival/
#     movement once Wikidata is merged: stays context-annotate +
#     Explore's existing idf/billing discount, never promoted to a
#     connector.
STRONG_CONNECTOR_CATEGORIES = frozenset(CATEGORY_WEIGHT) - CAST_CATEGORIES
CONTEXT_ENTITY_TYPES = frozenset({"decade", "genre"})

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


def _is_edge_implausible(
    role, birth_year, death_year, seed_year, cand_year,
    year_gap_threshold=TEMPORAL_GATE_YEAR_GAP,
) -> bool:
    """The temporal-plausibility gate's three rules (see module docstring).
    Pure function -- no DB access -- so it's directly unit-testable.

    The asymmetry is deliberate and is the crux of the whole gate:
    "credited after death" is normal for a source-material writer
    (adaptation lineage); "born after the film" is never legitimate, for
    any role. Encoding only one of these would either miss real rescore
    artifacts or wrongly demote real adaptation-lineage edges.
    """
    if seed_year is None or cand_year is None:
        return False
    older_year = min(seed_year, cand_year)
    gap = abs(cand_year - seed_year)

    # Rule 1: universal, all categories including writer. Nobody credited
    # on a film before they were born -- no exceptions.
    if birth_year is not None and birth_year > older_year:
        return True

    # Rule 2: excludes writer. Posthumous source-material credit is
    # legitimate adaptation lineage (Rashomon -> The Outrage; the Apu
    # trilogy), not an artifact -- do not demote those.
    if role != "writer" and death_year is not None and death_year < older_year:
        return True

    # Rule 3: rescore/band signature -- a composer with NO birth_year at
    # all (a band/collective entity, commonly) credited across a large
    # gap. Rule 1 structurally can't catch this: there's no birth data to
    # compare against.
    if role == "composer" and birth_year is None and gap > year_gap_threshold:
        return True

    return False


def _billing_factor(ordering, k=CREDIT_IMPORTANCE_K) -> float:
    """Cast-only billing down-weight: 1.0 for the top-billed lead
    (ordering=1), decreasing as ordering rises. `ordering` missing/
    uncastable -> no penalty; absence of billing data is not evidence of a
    cameo, so we don't guess. Pure function, unit-testable directly."""
    if ordering is None:
        return 1.0
    return 1.0 / (1.0 + k * max(ordering - 1, 0))


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


def _fetch_person_birth_death(con, nconsts):
    """(birth_year, death_year) per nconst, for the temporal-plausibility
    gate. Missing from `person` entirely, or NULL in name.basics, both
    come back as (None, None) -- absence of data is never itself evidence
    of implausibility (see _is_edge_implausible)."""
    if not nconsts:
        return {}
    values_sql = ", ".join("(?)" for _ in nconsts)
    rows = con.execute(f"""
        SELECT v.nconst, p.birth_year, p.death_year
        FROM (VALUES {values_sql}) AS v(nconst)
        LEFT JOIN person p ON p.nconst = v.nconst
    """, nconsts).fetchall()
    return {nconst: (birth_year, death_year) for nconst, birth_year, death_year in rows}


def _fetch_candidate_directors(con, tconsts):
    """tconst -> set of director nconsts, for the (off-by-default)
    cross-director bonus."""
    if not tconsts:
        return {}
    placeholders = ",".join("?" for _ in tconsts)
    rows = con.execute(f"""
        SELECT tconst, nconst FROM credits
        WHERE tconst IN ({placeholders}) AND category = 'director'
    """, tconsts).fetchall()
    out = defaultdict(set)
    for t, nconst in rows:
        out[t].add(nconst)
    return out


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

def explore(
    con: duckdb.DuckDBPyConnection,
    tconst: str,
    n: int = DEFAULT_N,
    novelty: bool = True,
    same_director_penalty: float = SAME_DIRECTOR_PENALTY,
    cross_director_bonus_enabled: bool = False,
    cross_director_bonus: float = CROSS_DIRECTOR_BONUS,
    temporal_gate: bool = True,
    temporal_gate_year_gap: int = TEMPORAL_GATE_YEAR_GAP,
    credit_importance: bool = True,
    credit_importance_k: float = CREDIT_IMPORTANCE_K,
    person_degree: dict | None = None,
) -> dict:
    """Film in -> ranked, explained connected films out.

    Returns {"seed": {...}, "results": [...], "thin_data": bool}. Each
    result is a legible record (title, year, score, connections,
    shared_decade, shared_genres, explanation) -- never a graph blob.
    Raises ValueError if `tconst` isn't in `film`.

    novelty, same_director_penalty, cross_director_bonus_enabled,
    cross_director_bonus, temporal_gate, temporal_gate_year_gap,
    credit_importance, credit_importance_k: see module docstring. Every one
    is a config flag specifically so eval_explore.py can measure with/
    without.
    """
    seed = _get_film(con, tconst)
    if seed is None:
        raise ValueError(f"tconst {tconst!r} not found in film")

    people_f_rows = con.execute(
        "SELECT nconst, category, ordering FROM credits WHERE tconst = ?", [tconst]
    ).fetchall()
    thin_data = _is_thin_data(people_f_rows)
    if not people_f_rows:
        return {"seed": _seed_out(seed), "results": [], "thin_data": thin_data}

    # One role per person on F: if credited in multiple categories, the
    # higher-weighted one wins (one edge per shared person, not one per
    # category row). ordering_f carries the billing that went with the
    # winning role, for the cast billing down-weight.
    roles_f = {}
    ordering_f = {}
    for nconst, category, ordering in people_f_rows:
        w = CATEGORY_WEIGHT.get(category, 0.0)
        if nconst not in roles_f or w > CATEGORY_WEIGHT.get(roles_f[nconst], 0.0):
            roles_f[nconst] = category
            ordering_f[nconst] = ordering

    director_nconsts_f = {nc for nc, cat, _ord in people_f_rows if cat == "director"}

    nconsts = list(roles_f.keys())
    placeholders = ",".join("?" for _ in nconsts)
    candidate_rows = con.execute(f"""
        SELECT DISTINCT tconst, nconst FROM credits
        WHERE nconst IN ({placeholders}) AND tconst != ?
    """, nconsts + [tconst]).fetchall()

    if not candidate_rows:
        return {"seed": _seed_out(seed), "results": [], "thin_data": thin_data}

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
    birth_death = _fetch_person_birth_death(con, nconsts) if temporal_gate else {}
    candidate_directors = (
        _fetch_candidate_directors(con, list(by_candidate.keys()))
        if cross_director_bonus_enabled else {}
    )

    results = []
    for cand_tconst, shared_nconsts in by_candidate.items():
        cand = films_info.get(cand_tconst)
        if cand is None:
            continue
        if _is_franchise_pair(seed["title"], cand["title"]):
            continue

        if temporal_gate:
            shared_nconsts = [
                nconst for nconst in shared_nconsts
                if not _is_edge_implausible(
                    roles_f[nconst], *birth_death.get(nconst, (None, None)),
                    seed["year"], cand["year"], temporal_gate_year_gap,
                )
            ]
            if not shared_nconsts:
                continue  # every edge to this candidate was implausible

        connections = []
        people_score = 0.0
        for nconst in shared_nconsts:
            role = roles_f[nconst]
            weight = CATEGORY_WEIGHT.get(role, 0.0) * idf(degree.get(nconst, 1))
            if credit_importance and role in CAST_CATEGORIES:
                weight *= _billing_factor(ordering_f.get(nconst), credit_importance_k)
            if novelty and (cand_tconst, nconst) in same_director_pairs:
                weight *= same_director_penalty
            people_score += weight
            connections.append({
                "nconst": nconst,
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
        if cross_director_bonus_enabled and _is_cross_director(
            director_nconsts_f, candidate_directors.get(cand_tconst, set())
        ):
            bonus += cross_director_bonus

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
    return {"seed": _seed_out(seed), "results": results[:n], "thin_data": thin_data}


def _is_thin_data(people_f_rows) -> bool:
    """True when the seed's own `credits` rows never include a non-cast
    category -- a source-coverage gap (Breathless: 10 principals rows, all
    cast), not a scoring problem. Zero credits at all counts as thin too."""
    non_cast = {cat for _, cat, _ord in people_f_rows if cat not in CAST_CATEGORIES}
    return len(non_cast) == 0


def _is_cross_director(seed_directors: set, candidate_directors: set) -> bool:
    """True only when both films have an identified director and they
    share none -- undefined (False) when either side has no director
    credit at all, since "cross" isn't meaningful without something to be
    across from."""
    if not seed_directors or not candidate_directors:
        return False
    return not (seed_directors & candidate_directors)


def _seed_out(seed):
    return {
        "tconst": seed["tconst"],
        "title": seed["title"],
        "year": seed["year"],
        "genres": sorted(seed["genres"]),
    }
