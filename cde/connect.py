"""Stage 3A: Connect -- the strongest meaningful path between two films.

Strongest-path, not shortest-path: shortest path is a database answer,
strongest is a cinephile one. Traverses ONLY strong-connector person edges
(cde.explore.STRONG_CONNECTOR_CATEGORIES -- director, writer,
cinematographer, editor, composer, producer, production_designer; cast is
excluded, same as the edge-tier rule everywhere else in stage 3A). Context
edges (decade/genre) can never BE a hop -- "both 1970s" is not a path, no
matter how tempting a fallback that would be when no strong path exists.

Objective: maximize cumulative edge strength (idf x category weight, as in
Explore -- CATEGORY_WEIGHT/idf imported directly, not re-derived) along the
chain, subject to a hop cap, not minimize hop count. A longer chain of
specific-collaborator hops beats a 2-hop generic one, and a 4-hop chain of
rare collaborators is preferred over a 1-hop tie through someone ubiquitous
(idf keeps that comparison honest, same as it does in Explore). If no
strong-connector path exists within the cap, that is reported honestly --
never a silent fallback to a context-only "connection."

Note on Connect's weighting scope: this deliberately uses only the base
Explore formula (category_weight x idf). It does NOT carry over Explore's
tuned refinements (billing/credit_importance, the temporal-plausibility
gate, same-director penalty) -- those were calibrated against a single-
seed neighbourhood ranking problem, not a multi-hop chain, and porting them
here without their own eval would be exactly the kind of blind reweighting
the project has deliberately avoided throughout. Flagged as a known gap:
a modern-rescore composer could in principle contribute a spurious hop
here the same way Explore's temporal gate now excludes from its own
neighbourhoods -- worth a targeted look before Connect ships to real users
at scale, not solved here.

Known limitation, found during deploy verification and only partially
fixed: this is a greedy bounded search, not an exhaustive optimal solver,
so among several paths of similar strength, WHICH one is returned can vary
between runs of the identical query against the identical, unchanged
database. Two sources were found: (1) Python randomizes string hashing per
process, so plain `set` iteration order for the search frontier varied run
to run -- fixed here, frontiers are iterated in sorted order. (2) DuckDB's
parallel table scan over the 8M-row `credits` table doesn't guarantee row
order across query executions, which can still change which near-tied
candidate the greedy search settles on -- NOT fixed here, because doing so
properly (ORDER BY on every query in the hot path, or forcing single-
threaded scans) would touch the query layer broadly enough to be a rewrite,
which is explicitly out of scope for this brief. Every individual result is
still honest about itself (a real, valid, strong-connector-only path
satisfying the hop cap and no-reuse rules, with a strength that matches
what's actually shown) -- the caveat is that asking the identical question
twice is not guaranteed to draw the identical answer. Recorded, not solved.

Approach (bounded bidirectional expansion -- crude-first, same discipline
as Explore, not a provably optimal max-weight-path solver): the films<->
people graph is far too large to materialize. From each film, a bounded,
depth-layered frontier expansion runs up to half the hop cap, keeping the
single best (highest cumulative strength) path found to every film it
reaches. Two prunes keep this tractable: a person whose overall
strong-connector degree exceeds MAX_PERSON_DEGREE_FOR_EXPANSION is not
expanded through at all (their idf weight would be negligible anyway, and
unbounded fan-out through a 300+-film producer is not tractable one query
at a time); and each film's outgoing hops are capped to the
MAX_NEIGHBORS_PER_FILM strongest. The two frontiers are then intersected:
the winning path is whichever meeting-point film (which may be one of the
two input films itself, for a direct connection) maximizes combined
strength across both sides, with no film revisited on either side.

Performance note (measured, not theoretical): computing each person's
strong-connector degree on demand -- one COUNT query per person
encountered -- is the dominant cost and made a real hop_cap=4 query
against the full 744k-film backbone take ~40s. build_strong_person_degree()
precomputes every strong-connector person's degree once (a few seconds)
so the search does dict lookups instead; pass its result as `person_degree`
(the API layer does this once at startup, same pattern as
cde.explore.build_person_degree()). Do NOT pass Explore's
build_person_degree() here -- see that function's docstring for why the
two are not interchangeable.
"""

from __future__ import annotations

import logging
import time

import duckdb

from cde.explore import CATEGORY_WEIGHT, STRONG_CONNECTOR_CATEGORIES, _fetch_films, _get_film, idf

DEFAULT_HOP_CAP = 4
MAX_PERSON_DEGREE_FOR_EXPANSION = 300
MAX_NEIGHBORS_PER_FILM = 40

# Deploy brief: a conservative execution timeout so a slow query degrades
# to an honest "ran out of time" message instead of hanging the UI. This is
# a cooperative deadline checked between films in the frontier expansion
# (see _expand_frontier), not preemptive cancellation -- individual DuckDB
# queries here are fast (milliseconds), so the actual overrun past the
# deadline is small. Never relaxes connector quality or falls back to a
# context-only path to force a result within budget.
DEFAULT_TIMEOUT_SECONDS = 20.0

_STRONG_CATS_SQL = ",".join(f"'{c}'" for c in STRONG_CONNECTOR_CATEGORIES)

# Local-diagnosis timing only -- silent unless a caller configures logging
# (e.g. `logging.getLogger("cde.connect").setLevel(logging.INFO)`). Never
# surfaced to the UI/API response; see module docstring's performance note.
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# DB access (read-only)
# --------------------------------------------------------------------------

def build_strong_person_degree(con: duckdb.DuckDBPyConnection) -> dict:
    """Precompute strong-connector-only degree for every person credited
    in a strong-connector category, once. Read-only, no table created.

    NOT interchangeable with cde.explore.build_person_degree(): that one
    counts a person's films across ALL categories including cast, which
    would systematically overstate degree (and so understate idf/rarity)
    for anyone who also has cast credits -- e.g. a prolific actor who
    directed exactly one film is degree-1 as a director, not their acting
    degree. Callers (e.g. the API layer) should build this once at
    process startup and pass it into connect() to skip a per-person COUNT
    query during the search; connect() also works without it, computing
    degree on demand (much slower -- see module docstring's performance
    note)."""
    rows = con.execute(f"""
        SELECT nconst, COUNT(DISTINCT tconst) AS degree
        FROM credits WHERE category IN ({_STRONG_CATS_SQL})
        GROUP BY nconst
    """).fetchall()
    return dict(rows)


def _strong_connector_people(con, tconst):
    """nconst -> best (highest-weighted) strong-connector category on
    `tconst`. Same one-role-per-person dedup rule as Explore."""
    rows = con.execute(f"""
        SELECT nconst, category FROM credits
        WHERE tconst = ? AND category IN ({_STRONG_CATS_SQL})
    """, [tconst]).fetchall()
    best = {}
    for nconst, category in rows:
        w = CATEGORY_WEIGHT.get(category, 0.0)
        if nconst not in best or w > CATEGORY_WEIGHT.get(best[nconst], 0.0):
            best[nconst] = category
    return best


def _person_strong_degree(con, nconst):
    row = con.execute(f"""
        SELECT COUNT(DISTINCT tconst) FROM credits
        WHERE nconst = ? AND category IN ({_STRONG_CATS_SQL})
    """, [nconst]).fetchone()
    return row[0] if row else 0


def _strong_neighbors(
    con, tconst, person_degree=None,
    expand_cap=MAX_NEIGHBORS_PER_FILM,
    degree_cap=MAX_PERSON_DEGREE_FOR_EXPANSION,
):
    """[(neighbor_tconst, nconst, role_from, role_to, hop_weight), ...],
    strongest first, capped to `expand_cap` -- strong-connector hops out
    of `tconst`. role_from is the person's category on `tconst` (the film
    being left); role_to is their category on `neighbor_tconst` (the film
    being arrived at) -- carried separately, not collapsed to one label,
    so the chain can say what's actually true when they differ (deploy
    brief: never imply the same role held both sides when the data says
    otherwise). hop_weight is still the MAX of the two -- scoring is
    unchanged, only the display gained the second label."""
    people = _strong_connector_people(con, tconst)

    scored = []
    for nconst, role in people.items():
        degree = (
            person_degree.get(nconst) if person_degree is not None
            else _person_strong_degree(con, nconst)
        )
        if not degree or degree > degree_cap:
            continue
        scored.append((nconst, role, degree, CATEGORY_WEIGHT[role] * idf(degree)))
    scored.sort(key=lambda t: t[3], reverse=True)

    neighbors = []
    for nconst, role_from, degree, weight_here in scored:
        if len(neighbors) >= expand_cap:
            break
        rows = con.execute(f"""
            SELECT DISTINCT tconst, category FROM credits
            WHERE nconst = ? AND tconst != ? AND category IN ({_STRONG_CATS_SQL})
        """, [nconst, tconst]).fetchall()
        for other_tconst, role_to in rows:
            weight_there = CATEGORY_WEIGHT.get(role_to, 0.0) * idf(degree)
            neighbors.append(
                (other_tconst, nconst, role_from, role_to, max(weight_here, weight_there))
            )

    neighbors.sort(key=lambda t: t[4], reverse=True)
    return neighbors[:expand_cap]


# --------------------------------------------------------------------------
# Bounded frontier expansion
# --------------------------------------------------------------------------

def _expand_frontier(con, start_tconst, max_depth, person_degree=None, deadline=None):
    """({tconst: (strength, film_path, hop_path)}, timed_out) for every
    film reachable from start_tconst within max_depth strong-connector
    hops (including start_tconst itself, at strength 0), keeping the
    single best (highest cumulative strength) path found to each. Each hop
    in hop_path is (nconst, role_from, role_to, weight).

    `deadline` is an absolute time.monotonic() timestamp (see connect()'s
    timeout_seconds) -- checked once per film in the frontier, not
    preemptive: expansion simply stops early (never relaxing connector
    quality or falling back to a weaker path to force a result), and
    `timed_out` tells the caller whether the incomplete search is why no
    path was found, as opposed to a genuine absence of one.

    Frontiers are iterated in sorted (not raw set) order: Python randomizes
    string hashing per process, so plain set iteration would make ties
    (two equal-strength paths to the same film) resolve differently run to
    run against the identical database -- confusing in a public demo where
    asking the same question twice should give the same answer. Sorting
    fixes that without touching the search's scope or scoring."""
    best = {start_tconst: (0.0, (start_tconst,), ())}
    frontier = {start_tconst}
    timed_out = False
    for _ in range(max_depth):
        next_frontier = set()
        for film in sorted(frontier):
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                break
            cur_strength, cur_path, cur_hops = best[film]
            cur_people = {h[0] for h in cur_hops}
            for neighbor_tconst, nconst, role_from, role_to, weight in _strong_neighbors(
                con, film, person_degree
            ):
                if neighbor_tconst in cur_path:
                    continue  # no revisited films within this path
                if nconst in cur_people:
                    continue  # no reused connecting person either -- a
                    # person credited on several of this path's films
                    # would otherwise "pad" a fake multi-hop chain out of
                    # what is really just one relationship (caught during
                    # real-data testing: Storaro used three times running)
                new_strength = cur_strength + weight
                existing = best.get(neighbor_tconst)
                if existing is None or new_strength > existing[0]:
                    best[neighbor_tconst] = (
                        new_strength,
                        cur_path + (neighbor_tconst,),
                        cur_hops + ((nconst, role_from, role_to, weight),),
                    )
                    next_frontier.add(neighbor_tconst)
        if timed_out:
            break
        frontier = next_frontier
        if not frontier:
            break
    return best, timed_out


# --------------------------------------------------------------------------
# Explanation formatting
# --------------------------------------------------------------------------

def _build_chain(con, films, hops):
    """Alternating [film, hop, film, hop, ..., film] -- the explained
    chain. Never a graph blob. Each hop carries role_from (the person's
    category on the film just left) and role_to (their category on the
    film being arrived at) SEPARATELY -- rendered bilaterally so the chain
    never implies the same role held on both sides when it didn't."""
    films_info = _fetch_films(con, list(films))
    nconsts = [h[0] for h in hops]
    names = {}
    if nconsts:
        values_sql = ", ".join("(?)" for _ in nconsts)
        rows = con.execute(f"""
            SELECT v.nconst, COALESCE(p.primary_name, v.nconst)
            FROM (VALUES {values_sql}) AS v(nconst)
            LEFT JOIN person p ON p.nconst = v.nconst
        """, nconsts).fetchall()
        names = dict(rows)

    chain = []
    for i, tconst in enumerate(films):
        info = films_info.get(tconst, {"title": tconst, "year": None})
        chain.append({"tconst": tconst, "title": info["title"], "year": info["year"]})
        if i < len(hops):
            nconst, role_from, role_to, weight = hops[i]
            chain.append({
                "person_name": names.get(nconst, nconst),
                "role_from": role_from,
                "role_to": role_to,
                "weight": round(weight, 4),
            })
    return chain


def _film_label(item):
    return f"{item['title']} ({item['year']})" if item.get("year") is not None else item["title"]


def _format_chain_explanation(chain):
    """Bilateral, arrow-style: Film A -- role_from --> Person -- role_to
    --> Film B. Only the category label `credits` actually carries
    (writer, cinematographer, ...) -- never a finer-grained label
    (screenplay, adaptation, ...) the data doesn't hold."""
    parts = [_film_label(chain[0])]
    i = 1
    while i < len(chain):
        hop, film = chain[i], chain[i + 1]
        parts.append(
            f"-- {hop['role_from']} --> {hop['person_name']} "
            f"-- {hop['role_to']} --> {_film_label(film)}"
        )
        i += 2
    return " ".join(parts)


def _film_out(film):
    return {"tconst": film["tconst"], "title": film["title"], "year": film["year"]}


def _reverse_hop(hop):
    """hops_b is recorded walking FROM b TOWARD the meeting point; the
    final chain walks the meeting point back OUT to b, so each of its hops
    is traversed backwards -- role_from/role_to must swap accordingly, or
    the displayed direction would silently lie about which side is which."""
    nconst, role_from, role_to, weight = hop
    return (nconst, role_to, role_from, weight)


# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------

def connect(
    con: duckdb.DuckDBPyConnection,
    tconst_a: str,
    tconst_b: str,
    hop_cap: int = DEFAULT_HOP_CAP,
    person_degree: dict | None = None,
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Strongest strong-connector-only path between two films, within
    `hop_cap` hops and (best-effort -- see _expand_frontier) within
    `timeout_seconds`. Returns {"a", "b", "found", "strength", "hops",
    "chain", "explanation"} on success, or {"a", "b", "found": False,
    "message", "timed_out"} when no strong-connector path was found --
    never a silent fallback to a context-only path, and never a relaxed
    one to force a result within budget. `timed_out` distinguishes "ran out
    of time" from "no path exists within the cap" -- both are honestly
    reported, but they're different facts and the message says which.
    Pass timeout_seconds=None to disable the budget entirely (e.g. for
    offline/batch use, not the interactive UI).
    """
    if tconst_a == tconst_b:
        raise ValueError("a and b must be different films")

    film_a = _get_film(con, tconst_a)
    if film_a is None:
        raise ValueError(f"tconst {tconst_a!r} not found in film")
    film_b = _get_film(con, tconst_b)
    if film_b is None:
        raise ValueError(f"tconst {tconst_b!r} not found in film")

    t0 = time.monotonic()
    deadline = t0 + timeout_seconds if timeout_seconds is not None else None

    depth_a = hop_cap // 2 + hop_cap % 2  # ceil half
    depth_b = hop_cap // 2                # floor half
    reached_a, timed_out_a = _expand_frontier(con, tconst_a, depth_a, person_degree, deadline)
    reached_b, timed_out_b = _expand_frontier(con, tconst_b, depth_b, person_degree, deadline)
    timed_out = timed_out_a or timed_out_b

    best = None
    # sorted(): same determinism reasoning as _expand_frontier's docstring
    # -- a tie between two meeting points must resolve the same way every
    # time against the same database.
    for film in sorted(set(reached_a) & set(reached_b)):
        strength_a, path_a, hops_a = reached_a[film]
        strength_b, path_b, hops_b = reached_b[film]
        total_hops = len(hops_a) + len(hops_b)
        if total_hops == 0 or total_hops > hop_cap:
            continue
        # A simple path: the two sides may not share an intermediate film
        # (the meeting point itself is fine -- that's where they join), and
        # may not reuse the same connecting person either (same reasoning
        # as within a single side -- see _expand_frontier).
        if set(path_a[:-1]) & set(path_b[:-1]):
            continue
        if {h[0] for h in hops_a} & {h[0] for h in hops_b}:
            continue
        total_strength = strength_a + strength_b
        if best is None or total_strength > best[0]:
            best = (total_strength, path_a, hops_a, path_b, hops_b)

    elapsed = time.monotonic() - t0
    logger.info(
        "connect(%s, %s, hop_cap=%d): %.2fs, timed_out=%s, found=%s",
        tconst_a, tconst_b, hop_cap, elapsed, timed_out, best is not None,
    )

    if best is None:
        if timed_out:
            message = (
                f"Search exceeded the {timeout_seconds:.0f}s interactive budget "
                f"before finding a strong-connector path within {hop_cap} hops. "
                "Try a smaller hop cap, or these two films may simply be far "
                "apart in the graph."
            )
        else:
            message = f"No strong-connector path found within {hop_cap} hops."
        return {
            "a": _film_out(film_a),
            "b": _film_out(film_b),
            "found": False,
            "message": message,
            "timed_out": timed_out,
        }

    total_strength, path_a, hops_a, path_b, hops_b = best
    full_films = list(path_a) + list(reversed(path_b[:-1]))
    full_hops = list(hops_a) + [_reverse_hop(h) for h in reversed(hops_b)]
    chain = _build_chain(con, full_films, full_hops)

    return {
        "a": _film_out(film_a),
        "b": _film_out(film_b),
        "found": True,
        "strength": round(total_strength, 4),
        "hops": len(full_hops),
        "chain": chain,
        "explanation": _format_chain_explanation(chain),
    }
