"""Minimal Streamlit UI for CineGraph Explore / Connect / Follow: a title
search box, a craft-first film view, ranked explained Explore results with
clickable connecting people and context threads (-> Follow), and a
two-film Connect input returning the explained chain. Plain lists/chains
-- no graph/network visualization, no marketing copy.

Web-only deps (streamlit) live in requirements-app.txt -- the engine
(cde/explore.py, cde/connect.py, cde/follow.py) never imports this module.

Run: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import duckdb
import streamlit as st

from cde.config import DB_PATH
from cde.connect import DEFAULT_HOP_CAP, connect
from cde.explore import build_person_degree, explore
from cde.follow import film_view, follow_context, follow_person
from cde.resolve import resolve_one_title

_DATA_MISSING_MESSAGE = (
    f"film.duckdb not found at `{DB_PATH}`. This backbone is built locally from "
    "the IMDb non-commercial datasets (never redistributed -- see README "
    "'Data & licensing') via `python -m cde.cli build`, or point the "
    "`CDE_DB_PATH` environment variable at an existing one."
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


def _render_person_buttons(people, key_prefix):
    """people: list of {"nconst", "name"} (or "person_name"/"nconst" from
    Explore connections). Renders each as a clickable Follow pivot."""
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
        st.markdown(f"**{group['category']}**")
        _render_person_buttons(group["people"], key_prefix=f"fv_{tconst}_{group['category']}")

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
        st.markdown(f"**{r['title']} ({r['year']})** — score {r['score']}")
        st.write(r["explanation"])
        _render_person_buttons(r["connections"], key_prefix=f"res_{r['tconst']}")
        st.divider()


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
        for item in result["chain"]:
            if "person_name" in item:
                # Bilateral: the role held leaving this film may differ
                # from the role held arriving at the next one -- shown
                # honestly, never collapsed to a single (possibly wrong)
                # label.
                st.write(
                    f"↳ {item['role_from']} → **{item['person_name']}** → {item['role_to']}"
                )
            else:
                year = item["year"] if item["year"] is not None else "?"
                st.markdown(f"**{item['title']}** ({year})")


def main():
    st.set_page_config(page_title="CineGraph")
    st.title("CineGraph")
    st.caption(
        "A relational discovery engine over IMDb-derived film/credit data -- "
        "not a rating-based recommender. Structural substrate only "
        "(who / when / what-genre), never votes, never a style label."
    )

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
