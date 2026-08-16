"""Minimal Streamlit UI for CineGraph Explore / Connect / Follow: a title
search box, a craft-first film view, ranked explained Explore results with
clickable connecting people and context threads (-> Follow), and a
two-film Connect input returning the explained chain. Plain lists/chains
-- no graph/network visualization, no marketing copy.

Two modes, one codebase: CDE_DEMO_MODE (env var) set -> browse the
precomputed public-demo artifact (cde/demo.py, ~75-film curated roster),
never opening film.duckdb at all. Unset -> the live engine against the
real local backbone (unchanged from before). The public demo never
redistributes IMDb data in any form -- see README "Data and licensing".

Web-only deps (streamlit) live in requirements-app.txt -- the engine
(cde/explore.py, cde/connect.py, cde/follow.py) never imports this module.

Run: streamlit run app/streamlit_app.py
Demo mode: CDE_DEMO_MODE=1 streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import html
import os

import duckdb
import streamlit as st

from cde.config import DB_PATH
from cde.connect import DEFAULT_HOP_CAP, connect
from cde.demo import ARTIFACT_PATH, demo_seed, load_artifact, roster_titles
from cde.explore import _format_connection_role, build_person_degree, explore
from cde.follow import film_view, follow_context, follow_person
from cde.resolve import resolve_one_title

DEMO_MODE = bool(os.environ.get("CDE_DEMO_MODE"))

_DATA_MISSING_MESSAGE = (
    f"film.duckdb not found at `{DB_PATH}`. This backbone is built locally from "
    "the IMDb non-commercial datasets (never redistributed -- see README "
    "'Data & licensing') via `python -m cde.cli build`, or point the "
    "`CDE_DB_PATH` environment variable at an existing one."
)
_ARTIFACT_MISSING_MESSAGE = (
    f"Demo artifact not found at `{ARTIFACT_PATH}`. Build it locally with "
    "`python build_demo_artifact.py` (needs a live film.duckdb -- the artifact "
    "itself is derived, committable output; see README 'Data and licensing')."
)


@st.cache_resource
def _get_con():
    # Not read_only: resolve_one_title() (via resolve_titles()) creates a
    # DuckDB macro + temp tables to do its matching, which DuckDB refuses
    # on a read_only connection. cde.explore/connect/follow themselves
    # issue nothing but SELECT against film/credits/person -- so this
    # doesn't compromise the read-only contract, only accommodates the
    # resolver's catalog-level bookkeeping. app/api.py, which never calls
    # resolve, stays read_only=True.
    return duckdb.connect(str(DB_PATH))


@st.cache_resource
def _get_person_degree(_con):
    return build_person_degree(_con)


def _resolve_from_inputs(con, title, year, tconst_override):
    """title+year is the primary disambiguation path; an explicit IMDb id
    (tconst) always wins when given, for the rare case title+year still
    leaves real ambiguity."""
    override = tconst_override.strip()
    if override:
        return override if override.startswith("tt") else f"tt{override}"
    year_val = int(year) if year.strip().isdigit() else None
    return resolve_one_title(con, title.strip(), year_val)


def _film_search_inputs(key_prefix):
    col1, col2, col3 = st.columns([3, 1, 2])
    with col1:
        title = st.text_input("Film title", key=f"{key_prefix}_title")
    with col2:
        year = st.text_input("Year (optional)", key=f"{key_prefix}_year")
    with col3:
        tconst = st.text_input(
            "or IMDb id (optional)", key=f"{key_prefix}_tconst",
            help="e.g. tt0065571 -- skips title search entirely if filled in",
        )
    return title, year, tconst


def _go_follow_person(nconst):
    st.session_state["view"] = "follow_person"
    st.session_state["follow_nconst"] = nconst
    st.rerun()


def _go_follow_context(entity_type, value, seed_tconst):
    st.session_state["view"] = "follow_context"
    st.session_state["follow_entity_type"] = entity_type
    st.session_state["follow_value"] = value
    st.session_state["follow_seed"] = seed_tconst
    st.rerun()


def _back_to_explore():
    st.session_state["view"] = "explore"
    st.rerun()


def _dedupe_people(people):
    """Preserve order, drop repeat nconsts. film_view() groups credits
    straight off `credits` rows -- the same person can be credited on
    more than one ordering row for one film/category, which otherwise
    renders as a visual duplicate ("Joseph Cotten, Joseph Cotten") or, for
    button-rendered groups, two widgets sharing one key. Display-layer
    only: cde/follow.py's grouping and the frozen demo artifact are
    untouched, this only changes what's shown."""
    seen = set()
    deduped = []
    for p in people:
        nconst = p.get("nconst")
        if nconst in seen:
            continue
        seen.add(nconst)
        deduped.append(p)
    return deduped


def _chip(text):
    """A small pill-shaped label -- Streamlit has no native "chip"
    component, so this is a minimal inline-styled <span> via st.markdown
    (unsafe_allow_html=True is the standard, dependency-free way to do
    this; no JS, no external resources, no new library). Background uses
    alpha instead of a fixed hex so it reads reasonably in both light and
    dark themes; text color is left to inherit rather than set."""
    return (
        '<span style="background:rgba(128,128,128,0.18); padding:2px 9px; '
        'border-radius:12px; font-size:0.85em; margin:2px 6px 2px 0; '
        f'display:inline-block;">{html.escape(str(text))}</span>'
    )


def _render_chips(chips):
    if chips:
        st.markdown(" ".join(chips), unsafe_allow_html=True)


def _shared_context_chips(shared_decade, shared_genres):
    chips = []
    if shared_decade is not None:
        chips.append(_chip(f"{shared_decade}s"))
    for genre in shared_genres or []:
        chips.append(_chip(genre))
    return chips


# Craft department -> a reader-facing label, since `credits.category` is a
# raw IMDb value (director, cinematographer, ...), not display copy.
# Anything not in this map (shouldn't happen against the real backbone,
# but defensive) falls back to a title-cased version of the raw category.
_CRAFT_DISPLAY_LABELS = {
    "director": "Direction",
    "writer": "Writing",
    "cinematographer": "Cinematography",
    "editor": "Editing",
    "composer": "Music",
    "producer": "Producing",
    "production_designer": "Production Design",
    "cast": "Performance",
}


def _craft_label(category):
    return _CRAFT_DISPLAY_LABELS.get(category, category.replace("_", " ").title())


def _render_craft_section_header(category):
    st.markdown(f"##### {_craft_label(category)}")


def _render_connect_chain(chain):
    """A single horizontal arrow-chain -- Film A -> (person, role) ->
    Film B -> ... -- instead of one stacked line per hop. Still plain
    text/markdown, just laid out as a path; bold marks a film node,
    italic marks a person node so the two are visually distinct without
    a graph drawing."""
    parts = []
    for item in chain:
        if "person_name" in item:
            role_from, role_to = item["role_from"], item["role_to"]
            role = role_from if role_from == role_to else f"{role_from}→{role_to}"
            parts.append(f"*{item['person_name']}* ({role})")
        else:
            year = item["year"] if item["year"] is not None else "?"
            parts.append(f"**{item['title']}** ({year})")
    st.markdown(" → ".join(parts))


def _render_result_card(r, follow_key_prefix=None):
    """One Explore/Connected-film result as a bordered card: title/year
    header, a light score bar, shared-decade/genre as chips, and each
    connecting person's role. follow_key_prefix (live mode) makes each
    connecting person a clickable Follow button labeled with their role;
    omitted (demo mode -- no live engine call to route an arbitrary click
    into), the same info renders as plain informational chips instead."""
    with st.container(border=True):
        st.markdown(f"**{r['title']}** ({r['year']})")
        st.progress(min(1.0, max(0.0, r["score"])), text=f"score {r['score']:.4f}")
        _render_chips(_shared_context_chips(r.get("shared_decade"), r.get("shared_genres")))
        connections = _dedupe_people(r["connections"])
        if not connections:
            return
        if follow_key_prefix is None:
            _render_chips([
                _chip(f"{c['person_name']} ({_format_connection_role(c)})") for c in connections
            ])
            return
        cols = st.columns(min(len(connections), 4) or 1)
        for i, c in enumerate(connections):
            nconst = c.get("nconst")
            label = f"{c['person_name']} ({_format_connection_role(c)})"
            with cols[i % len(cols)]:
                if nconst and st.button(label, key=f"{follow_key_prefix}_{r['tconst']}_{nconst}"):
                    _go_follow_person(nconst)


def _render_person_buttons(people, key_prefix):
    """people: list of {"nconst", "name"} (or "person_name"/"nconst" from
    Explore connections). Renders each as a clickable Follow pivot."""
    people = _dedupe_people(people)
    if not people:
        return
    cols = st.columns(min(len(people), 4) or 1)
    for i, p in enumerate(people):
        nconst = p.get("nconst")
        name = p.get("name") or p.get("person_name") or nconst
        with cols[i % len(cols)]:
            if nconst and st.button(name, key=f"{key_prefix}_{nconst}"):
                _go_follow_person(nconst)


def _render_film_view(con, tconst):
    """Craft-first grouping -- craft departments elevated, cast secondary
    -- plus decade/genre threads, all clickable Follow pivots. Only
    categories present in the loaded data are ever shown (no Wikidata
    departments)."""
    try:
        view = film_view(con, tconst)
    except ValueError as exc:
        st.error(f"Couldn't load this film: {exc}")
        return None

    st.subheader(f"{view['title']} ({view['year']})")
    for group in view["groups"]:
        _render_craft_section_header(group["category"])
        _render_person_buttons(group["people"], key_prefix=f"fv_{tconst}_{group['category']}")
        st.write("")

    st.markdown("**follow a thread**")
    context_cols = st.columns(1 + len(view["genres"]) or 1)
    if view["year"] is not None:
        decade = (view["year"] // 10) * 10
        with context_cols[0]:
            if st.button(f"{decade}s", key=f"ctx_decade_{tconst}"):
                _go_follow_context("decade", decade, tconst)
    for i, genre in enumerate(view["genres"]):
        with context_cols[(i + 1) % len(context_cols)]:
            if st.button(genre, key=f"ctx_genre_{tconst}_{genre}"):
                _go_follow_context("genre", genre, tconst)
    return view


def _render_follow_person(con):
    nconst = st.session_state["follow_nconst"]
    if st.button("< Back to Explore"):
        _back_to_explore()
        return
    try:
        with st.spinner("Looking up filmography..."):
            result = follow_person(con, nconst)
    except Exception:
        st.error("Couldn't load this person's filmography. Please try again.")
        return

    st.subheader(f"Follow: {result['entity_name']}")
    st.caption(f"{len(result['films'])} credited films")
    if not result["films"]:
        st.write("No credited films found.")
        return
    for f in result["films"]:
        year = f["year"] if f["year"] is not None else "?"
        st.write(f"{year} — **{f['title']}** ({f['role']})")


def _render_follow_context(con):
    entity_type = st.session_state["follow_entity_type"]
    value = st.session_state["follow_value"]
    seed_tconst = st.session_state["follow_seed"]
    if st.button("< Back to Explore"):
        _back_to_explore()
        return
    try:
        with st.spinner(f"Following {entity_type}: {value}..."):
            result = follow_context(con, entity_type, value, seed_tconst)
    except ValueError as exc:
        st.error(f"Couldn't follow this {entity_type}: {exc}")
        return
    except Exception:
        st.error("Something went wrong following this thread. Please try again.")
        return

    st.subheader(f"Follow: {entity_type} = {value}")
    st.caption(
        f"scoped to {result['seed']['title']}'s connected films, "
        f"not the whole catalog -- {len(result['films'])} found"
    )
    if not result["films"]:
        st.write("No connected films matched.")
        return
    for f in result["films"]:
        year = f["year"] if f["year"] is not None else "?"
        st.write(f"{year} — **{f['title']}**")


def _render_explore_tab(con, person_degree):
    st.write("Find worthwhile relational exits from one film.")
    title, year, tconst_override = _film_search_inputs("explore")
    novelty = st.checkbox("Demote same-director results (default on)", value=True)
    n = st.slider("How many results", 5, 50, 20)

    if st.button("Explore") and (title.strip() or tconst_override.strip()):
        with st.spinner("Searching..."):
            tconst = _resolve_from_inputs(con, title, year, tconst_override)
        if not tconst:
            st.error(
                "No match found. Try adjusting the title, adding a year, "
                "or entering an IMDb id directly."
            )
            return
        # Deliberately NOT "explore_tconst" -- that key belongs to the
        # "or IMDb id" text_input widget above; Streamlit forbids
        # overwriting a widget-backed session_state key directly (caught
        # by AppTest, not by inspection -- see README known issues).
        st.session_state["explore_result_tconst"] = tconst

    tconst = st.session_state.get("explore_result_tconst")
    if not tconst:
        return

    view = _render_film_view(con, tconst)
    if view is None:
        return

    try:
        with st.spinner("Exploring graph..."):
            result = explore(con, tconst, n=n, novelty=novelty, person_degree=person_degree)
    except ValueError as exc:
        st.error(f"Couldn't explore this film: {exc}")
        return
    except Exception:
        st.error("Something went wrong exploring this film. Please try again.")
        return

    if result["thin_data"]:
        st.info(
            "Limited crew data for this film -- IMDb's credited principals for "
            "this title are cast-only, so results below lean on cast connections "
            "rather than cinematographer/editor/composer/writer links. Not a "
            "ranking judgment, just a coverage gap."
        )

    if not result["results"]:
        st.write("No connected films found (no shared creative collaborators).")
        return

    st.markdown("### Connected films")
    for r in result["results"]:
        _render_result_card(r, follow_key_prefix="res")


def _render_connect_tab(con):
    st.write("Find the strongest chain of collaborators between two films.")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Film A**")
        title_a, year_a, tconst_a_override = _film_search_inputs("connect_a")
    with col2:
        st.markdown("**Film B**")
        title_b, year_b, tconst_b_override = _film_search_inputs("connect_b")
    hop_cap = st.slider(
        "Max hops", 1, 8, DEFAULT_HOP_CAP,
        help="Higher caps search a larger graph and take longer.",
    )

    ready = (title_a.strip() or tconst_a_override.strip()) and \
        (title_b.strip() or tconst_b_override.strip())
    if st.button("Connect") and ready:
        with st.spinner("Searching..."):
            tconst_a = _resolve_from_inputs(con, title_a, year_a, tconst_a_override)
            tconst_b = _resolve_from_inputs(con, title_b, year_b, tconst_b_override)
        if not tconst_a or not tconst_b:
            st.error(
                "Couldn't resolve one or both titles. Try adding a year, "
                "or an IMDb id directly."
            )
            return

        try:
            with st.spinner("Finding strongest path..."):
                result = connect(con, tconst_a, tconst_b, hop_cap=hop_cap)
        except ValueError as exc:
            st.error(str(exc))
            return
        except Exception:
            st.error("Something went wrong finding a path. Please try again.")
            return

        st.subheader(f"{result['a']['title']} → {result['b']['title']}")
        if not result["found"]:
            st.warning(result["message"])
            return
        st.caption(f"{result['hops']} hops, strength {result['strength']}")
        with st.container(border=True):
            _render_connect_chain(result["chain"])


def _get_demo_artifact():
    # Not st.cache_resource: the artifact is a plain dict (cheap to load,
    # a few hundred KB of JSON) and re-reading it per session avoids any
    # risk of a stale cached copy after a redeploy with a rebuilt artifact.
    # ARTIFACT_PATH passed explicitly (not load_artifact()'s own default)
    # so tests can monkeypatch this module's ARTIFACT_PATH at call time.
    return load_artifact(ARTIFACT_PATH)


def _demo_search(artifact, query):
    """Case-insensitive substring match against the roster's own titles --
    no DuckDB, no resolve_one_title(). A unique hit resolves; anything
    ambiguous or absent is treated as "not in this demo" (see
    _render_demo_explore_tab) rather than guessing."""
    query = query.strip().lower()
    if not query:
        return None
    matches = [tconst for tconst, label in roster_titles(artifact) if query in label.lower()]
    return matches[0] if len(matches) == 1 else None


def _go_demo_follow_person(seed_tconst, nconst):
    st.session_state["demo_view"] = "follow_person"
    st.session_state["demo_seed_tconst"] = seed_tconst
    st.session_state["demo_follow_nconst"] = nconst
    st.rerun()


def _go_demo_follow_context(seed_tconst, index):
    st.session_state["demo_view"] = "follow_context"
    st.session_state["demo_ctx_seed"] = seed_tconst
    st.session_state["demo_ctx_index"] = index
    st.rerun()


def _back_to_demo_explore():
    st.session_state["demo_view"] = "explore"
    st.rerun()


def _render_demo_film(artifact, tconst):
    seed = demo_seed(artifact, tconst)
    st.subheader(f"{seed['title']} ({seed['year']})")
    for group in seed["film_view"]["groups"]:
        _render_craft_section_header(group["category"])
        _render_chips([_chip(p["name"]) for p in _dedupe_people(group["people"])])

    # Only entities actually precomputed for this seed are clickable --
    # demo mode makes no live engine call, so nothing else can be offered
    # honestly.
    followable_people = [
        (nconst, res["entity_name"]) for nconst, res in seed["follow_people"].items()
    ]
    if followable_people:
        st.markdown("**follow a thread (precomputed for this demo)**")
        cols = st.columns(min(len(followable_people), 4) or 1)
        for i, (nconst, name) in enumerate(followable_people):
            with cols[i % len(cols)]:
                if st.button(name, key=f"demo_follow_{tconst}_{nconst}"):
                    _go_demo_follow_person(tconst, nconst)
    if seed["follow_contexts"]:
        cols = st.columns(min(len(seed["follow_contexts"]), 4) or 1)
        for i, fc in enumerate(seed["follow_contexts"]):
            label = f"{fc['entity_id']}s" if fc["entity_type"] == "decade" else fc["entity_id"]
            with cols[i % len(cols)]:
                if st.button(label, key=f"demo_ctx_{tconst}_{i}"):
                    _go_demo_follow_context(tconst, i)

    if seed["thin_data"]:
        st.info(
            "Limited crew data for this film -- IMDb's credited principals for "
            "this title are cast-only, so results below lean on cast connections "
            "rather than cinematographer/editor/composer/writer links. Not a "
            "ranking judgment, just a coverage gap."
        )

    results = seed["explore"]["results"]
    if not results:
        st.write("No connected films found (no shared creative collaborators).")
        return
    st.markdown("### Connected films")
    for r in results:
        _render_result_card(r)


def _render_demo_follow_person(artifact):
    seed_tconst = st.session_state["demo_seed_tconst"]
    nconst = st.session_state["demo_follow_nconst"]
    if st.button("< Back to Explore"):
        _back_to_demo_explore()
        return
    result = demo_seed(artifact, seed_tconst)["follow_people"][nconst]
    st.subheader(f"Follow: {result['entity_name']}")
    st.caption(f"{len(result['films'])} credited films")
    for f in result["films"]:
        year = f["year"] if f["year"] is not None else "?"
        st.write(f"{year} — **{f['title']}** ({f['role']})")


def _render_demo_follow_context(artifact):
    seed_tconst = st.session_state["demo_ctx_seed"]
    index = st.session_state["demo_ctx_index"]
    if st.button("< Back to Explore"):
        _back_to_demo_explore()
        return
    result = demo_seed(artifact, seed_tconst)["follow_contexts"][index]
    st.subheader(f"Follow: {result['entity_type']} = {result['entity_id']}")
    st.caption(
        f"scoped to {result['seed']['title']}'s connected films, not the whole catalog"
    )
    if not result["films"]:
        st.write("No connected films matched.")
        return
    for f in result["films"]:
        year = f["year"] if f["year"] is not None else "?"
        st.write(f"{year} — **{f['title']}**")


def _render_demo_browse_list(artifact):
    """Clickable roster list -- writes into the exact same
    st.session_state["demo_explore_tconst"] key _render_demo_explore_tab's
    search sets, so one click renders a film's result the same way a
    successful search does (no second selection path to keep in sync)."""
    items = roster_titles(artifact)
    cols = st.columns(3)
    for i, (tconst, label) in enumerate(items):
        with cols[i % len(cols)]:
            if st.button(label, key=f"demo_browse_{tconst}"):
                st.session_state["demo_explore_tconst"] = tconst


def _render_demo_explore_tab(artifact):
    n_roster = len(artifact["seeds"])
    st.write(
        f"This demo covers {n_roster} curated films with precomputed results. "
        "The full engine runs live against the complete IMDb-derived backbone locally."
    )

    # st.form so pressing Enter in the text_input submits (same as
    # clicking the button) instead of doing nothing until a separate
    # button click.
    with st.form("demo_search_form"):
        query = st.text_input("Search the demo roster (title)", key="demo_search_query")
        submitted = st.form_submit_button("Search")
    if submitted and query.strip():
        tconst = _demo_search(artifact, query)
        if tconst is None:
            st.error(
                f"'{query}' isn't in this {n_roster}-film demo roster -- try the "
                "browse list below, or run the full engine locally against the "
                "complete backbone."
            )
        else:
            st.session_state["demo_explore_tconst"] = tconst

    with st.expander(f"browse all {n_roster} films in this demo"):
        _render_demo_browse_list(artifact)

    tconst = st.session_state.get("demo_explore_tconst")
    if tconst:
        _render_demo_film(artifact, tconst)


def _render_demo_connect_tab(artifact):
    pairs = artifact["connect_pairs"]
    if not pairs:
        st.write("No Connect pairs precomputed for this demo.")
        return
    options = {
        f"{result['a']['title']} ({result['a']['year']}) <-> "
        f"{result['b']['title']} ({result['b']['year']})": key
        for key, result in pairs.items()
    }
    choice = st.selectbox("Curated pair", list(options.keys()))
    result = pairs[options[choice]]

    st.subheader(f"{result['a']['title']} → {result['b']['title']}")
    if not result["found"]:
        st.warning(result["message"])
        return
    st.caption(f"{result['hops']} hops, strength {result['strength']}")
    with st.container(border=True):
        _render_connect_chain(result["chain"])


def _render_demo_mode():
    if not ARTIFACT_PATH.exists():
        st.error(_ARTIFACT_MISSING_MESSAGE)
        st.stop()
    artifact = _get_demo_artifact()

    st.info(
        f"Public demo mode -- browsing {len(artifact['seeds'])} curated, precomputed "
        "films. The full engine runs locally against the complete IMDb-derived "
        "backbone (never redistributed here -- see README)."
    )

    st.session_state.setdefault("demo_view", "explore")
    view = st.session_state["demo_view"]
    if view == "follow_person":
        _render_demo_follow_person(artifact)
        return
    if view == "follow_context":
        _render_demo_follow_context(artifact)
        return

    st.markdown(
        "**Explore** -- find worthwhile exits from one film.  \n"
        "**Follow** -- click a precomputed person or decade/genre thread.  \n"
        "**Connect** -- pick a curated pair and see the strongest route."
    )
    tab_explore, tab_connect = st.tabs(["Explore", "Connect"])
    with tab_explore:
        _render_demo_explore_tab(artifact)
    with tab_connect:
        _render_demo_connect_tab(artifact)


def main():
    st.set_page_config(page_title="CineGraph")
    st.title("CineGraph")
    st.caption(
        "A relational discovery engine over IMDb-derived film/credit data -- "
        "not a rating-based recommender. Structural substrate only "
        "(who / when / what-genre), never votes, never a style label."
    )

    if DEMO_MODE:
        # Branches out, and returns, before _get_con() is ever called --
        # demo mode must never open film.duckdb (see module docstring and
        # tests/test_demo_mode.py).
        _render_demo_mode()
        return

    if not DB_PATH.exists():
        st.error(_DATA_MISSING_MESSAGE)
        st.stop()

    con = _get_con()
    person_degree = _get_person_degree(con)

    st.session_state.setdefault("view", "explore")
    view = st.session_state["view"]

    if view == "follow_person":
        _render_follow_person(con)
        return
    if view == "follow_context":
        _render_follow_context(con)
        return

    st.markdown(
        "**Explore** -- find worthwhile exits from one film.  \n"
        "**Follow** -- click a person or a decade/genre thread from Explore "
        "and pursue it.  \n"
        "**Connect** -- find a meaningful route between two films."
    )

    tab_explore, tab_connect = st.tabs(["Explore", "Connect"])
    with tab_explore:
        _render_explore_tab(con, person_degree)
    with tab_connect:
        _render_connect_tab(con)


if __name__ == "__main__":
    main()
