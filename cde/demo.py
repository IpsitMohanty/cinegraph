"""Public-demo artifact: a curated roster's worth of Explore/Follow/Connect
outputs, precomputed once by the real engine and frozen to JSON.

Licensing guardrail (the whole point of this module -- see README "Data
and licensing"): the artifact holds ONLY derived outputs -- film titles,
years, tconsts as opaque ids, computed paths, roles-as-shown, scores. It
is never a reshaping of the `credits`/`person` tables themselves; a dump
of IMDb's relational data as JSON would still be IMDb's database under
another format. Every value in this module's output is something
cde.explore.explore() / cde.follow.film_view() / cde.follow.follow_person()
/ cde.follow.follow_context() / cde.connect.connect() already computed and
returned -- build_artifact() freezes their exact return shapes verbatim
(they're already JSON-serializable plain dicts/lists/strings/numbers), it
does not add a new query path into the credit graph.

The roster (~75 films) is curated, not sampled: it starts from the 12
canonical eval seeds (era/country-spread, see eval_explore.py), plus the
higher-scoring `interesting`-labeled results from the tuned eval pass
(eval/explore_eval_tuned_labeled.md) -- so a demo visitor's first click
lands on a seed the engine is already known to handle well, not a
coin-flip. The Conformist and The Godfather are in the roster (the
strongest labeled Explore seed, and the headline Connect chain); Breathless
is in the roster deliberately AS a thin-data case -- the demo shows the
engine being honest about a coverage gap, not just its wins.

Note on the Conformist <-> Godfather Connect pair (discovered building
this artifact, not assumed going in): it hits the exact frontier
non-determinism documented in the deploy-surface brief (DuckDB's parallel
scan order affects which neighbors survive the per-film degree cap) --
un-cached repeat runs at hop_cap=4 found a real path ~7 times out of 8
sampled, with strength varying 0.70-0.90, and "no path found" the rest.
Both outcomes are real engine output, not a bug in this pair specifically.
Per this brief's "freeze one deterministic result, cherry-picking sanctioned
for the demo" instruction, CONNECT_PAIRS' entry for this pair was frozen
from the strongest of several sampled real runs (0.8968, via Storaro ->
Reds -> Sondheim -> The Last of Sheila -> Goldenberg -> Up the Sandbox ->
Willis) rather than whatever a single build_demo_artifact.py invocation
happened to land on -- see README "Known limitations" for the general
Connect non-determinism disclosure this is an instance of.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from cde.connect import build_strong_person_degree, connect
from cde.explore import DEFAULT_N, build_person_degree, explore
from cde.follow import CRAFT_ORDER, film_view, follow_context, follow_person

ARTIFACT_PATH = Path(__file__).resolve().parent.parent / "demo" / "artifact.json"

# --------------------------------------------------------------------------
# Roster: 12 core eval seeds + higher-scoring `interesting`-labeled results
# from eval/explore_eval_tuned_labeled.md (top 6 per seed where a seed had
# more than 6, all of them otherwise) -- 72 films total. tconsts for the
# non-core entries are taken directly from that eval run, not re-resolved,
# so there is no risk of a title/year re-resolution drifting to a
# different film than what was actually evaluated.
# --------------------------------------------------------------------------

CORE_SEEDS = [
    ("tt0065571", "The Conformist", 1970),
    ("tt0050976", "The Seventh Seal", 1957),
    ("tt0056801", "8 1/2", 1963),
    ("tt0111161", "The Shawshank Redemption", 1994),
    ("tt0042876", "Rashomon", 1950),
    ("tt0033467", "Citizen Kane", 1941),
    ("tt0053472", "Breathless", 1960),
    ("tt0046438", "Tokyo Story", 1953),
    ("tt0048473", "Pather Panchali", 1955),
    ("tt0068646", "The Godfather", 1972),
    ("tt0118694", "In the Mood for Love", 2000),
    ("tt0015648", "The Battleship Potemkin", 1925),
]

# tconst only -- title/year come from `film` at build time, same as any
# other engine call.
INTERESTING_ADDITIONS = [
    # from The Conformist (top 6 of 8 interesting)
    "tt0079495", "tt0069678", "tt0071442", "tt0066413", "tt0070109", "tt0075652",
    # from The Seventh Seal (all 5 interesting)
    "tt0043019", "tt0044811", "tt0055103", "tt0051854", "tt0049172",
    # from 8 1/2 (all 4 interesting)
    "tt0050406", "tt0065054", "tt0054130", "tt0044000",
    # from The Shawshank Redemption (top 6 of 8)
    "tt0235737", "tt0109836", "tt0418763", "tt8579674", "tt0959337", "tt0112818",
    # from Rashomon (top 6 of 8)
    "tt0048198", "tt0043614", "tt0046478", "tt0182685", "tt2190475", "tt0041699",
    # from Citizen Kane (top 6 of 7)
    "tt0033532", "tt0036969", "tt0034922", "tt0043456", "tt0036044", "tt0032551",
    # from Breathless (its only interesting result)
    "tt0062457",
    # from Tokyo Story (all 5 interesting)
    "tt0049784", "tt0051093", "tt0051720", "tt0053579", "tt0044982",
    # from Pather Panchali (all 4 interesting)
    "tt0052046", "tt0056134", "tt0060742", "tt0059709",
    # from The Godfather (top 6 of 9)
    "tt0072912", "tt0077742", "tt0079522", "tt0066017", "tt0077360", "tt0071369",
    # from In the Mood for Love (all 5 interesting)
    "tt0343663", "tt0984130", "tt0235079", "tt0377923", "tt0354243",
    # from Battleship Potemkin (top 6 of 8)
    "tt0020451", "tt21635008", "tt0024668", "tt0051790", "tt0413316", "tt21746718",
]

ROSTER = [t for t, _title, _year in CORE_SEEDS] + INTERESTING_ADDITIONS

# Curated Connect pairs -- NOT all-pairs (that's N^2 over 72 films). The
# Conformist/Godfather pair is required (the headline chain -- see module
# docstring for why its frozen entry was hand-picked from several sampled
# runs rather than taken from whatever build_demo_artifact.py landed on).
# The rest span era/country on purpose, including genuine no-path results
# at build time -- an honest "no path found" is as much a real,
# demo-worthy output as a chain is.
CONNECT_PAIRS = [
    ("tt0065571", "tt0068646"),  # The Conformist <-> The Godfather (headline)
    ("tt0050976", "tt0056801"),  # The Seventh Seal <-> 8 1/2
    ("tt0042876", "tt0046438"),  # Rashomon <-> Tokyo Story
    ("tt0048473", "tt0118694"),  # Pather Panchali <-> In the Mood for Love
    ("tt0033467", "tt0053472"),  # Citizen Kane <-> Breathless
    ("tt0015648", "tt0033467"),  # Battleship Potemkin <-> Citizen Kane
]

FOLLOW_TOP_N_PEOPLE = 3  # top craft people per seed to precompute Follow(person) for
FOLLOW_CONTEXT_GENRES = 1  # how many of the seed's own genres to precompute Follow(genre) for


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def _top_people_for_follow(film_view_result, n=FOLLOW_TOP_N_PEOPLE):
    """The first n people encountered walking film_view()'s craft-first
    groups in order -- i.e. the people a demo visitor would actually see
    first and might click."""
    people = []
    for group in film_view_result["groups"]:
        if group["category"] not in CRAFT_ORDER:
            continue  # skip cast for the precomputed Follow(person) set
        for p in group["people"]:
            people.append(p["nconst"])
            if len(people) >= n:
                return people
    return people


def build_artifact(
    con: duckdb.DuckDBPyConnection,
    roster=None,
    pairs=None,
    n=DEFAULT_N,
) -> dict:
    """Run the real engine over `roster` and `pairs`, freeze the exact
    return shapes to a plain dict. Pure w.r.t. the database -- no writes,
    same read-only discipline as the engine itself."""
    roster = roster if roster is not None else ROSTER
    pairs = pairs if pairs is not None else CONNECT_PAIRS
    person_degree = build_person_degree(con)
    # NOT interchangeable with person_degree (all-category) -- Connect
    # needs strong-connector-only degree, see cde.connect's docstring.
    strong_person_degree = build_strong_person_degree(con)

    seeds = {}
    for tconst in roster:
        view = film_view(con, tconst)
        explore_result = explore(con, tconst, n=n, person_degree=person_degree)

        follow_people = {}
        for nconst in _top_people_for_follow(view):
            follow_people[nconst] = follow_person(con, nconst)

        follow_contexts = []
        if explore_result["seed"]["year"] is not None:
            decade = (explore_result["seed"]["year"] // 10) * 10
            follow_contexts.append(
                follow_context(con, "decade", decade, tconst, person_degree=person_degree)
            )
        for genre in view["genres"][:FOLLOW_CONTEXT_GENRES]:
            follow_contexts.append(
                follow_context(con, "genre", genre, tconst, person_degree=person_degree)
            )

        seeds[tconst] = {
            "tconst": tconst,
            "title": view["title"],
            "year": view["year"],
            "genres": view["genres"],
            "thin_data": explore_result["thin_data"],
            "film_view": view,
            "explore": explore_result,
            "follow_people": follow_people,
            "follow_contexts": follow_contexts,
        }

    connect_pairs = {}
    for tconst_a, tconst_b in pairs:
        key = f"{tconst_a}|{tconst_b}"
        connect_pairs[key] = connect(
            con, tconst_a, tconst_b, person_degree=strong_person_degree
        )

    return {
        "meta": {
            "roster_size": len(roster),
            "pair_count": len(pairs),
            "note": (
                "Derived outputs only -- computed Explore/Follow/Connect results "
                "from the real engine, frozen at build time. Not a reshaping of "
                "IMDb's credits/person tables. See README 'Data and licensing'."
            ),
        },
        "seeds": seeds,
        "connect_pairs": connect_pairs,
    }


# --------------------------------------------------------------------------
# Save / load
# --------------------------------------------------------------------------

def save_artifact(artifact: dict, path: Path = ARTIFACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=1, ensure_ascii=False, sort_keys=True)


def load_artifact(path: Path = ARTIFACT_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Demo-mode lookups (used by app/streamlit_app.py; DuckDB never touched)
# --------------------------------------------------------------------------

def demo_seed(artifact, tconst):
    return artifact["seeds"].get(tconst)


def demo_connect(artifact, tconst_a, tconst_b):
    """Looks up both key orderings -- the curated pairs list is small and
    unordered from a UI perspective (a demo visitor picks "A" and "B" from
    the same roster, not knowing which order was precomputed)."""
    pairs = artifact["connect_pairs"]
    for key in (f"{tconst_a}|{tconst_b}", f"{tconst_b}|{tconst_a}"):
        if key in pairs:
            return pairs[key]
    return None


def roster_titles(artifact):
    """[(tconst, "Title (year)"), ...] sorted by title, for a demo search
    dropdown -- never free-text against the whole IMDb corpus, which isn't
    loaded in demo mode at all."""
    items = [
        (t, f"{s['title']} ({s['year']})" if s["year"] is not None else s["title"])
        for t, s in artifact["seeds"].items()
    ]
    return sorted(items, key=lambda item: item[1])
