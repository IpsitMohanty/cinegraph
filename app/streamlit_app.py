"""Minimal Streamlit UI for CineGraph Explore / Connect / Follow: a title
search box, a craft-first film view, ranked explained Explore results with
clickable connecting people (-> Follow that person), and a two-film
Connect input returning the explained chain. Plain lists/chains -- no
graph/network visualization.

Web-only deps (streamlit) live in requirements-app.txt -- the engine
(cde/explore.py, cde/connect.py, cde/follow.py) never imports this module.

Run: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import duckdb
import streamlit as st

from cde.config import DB_PATH
from cde.connect import connect
from cde.explore import build_person_degree, explore
from cde.follow import film_view, follow_person
from cde.resolve import resolve_one_title


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


def _resolve_from_inputs(con, title, year):
    year_val = int(year) if year.strip().isdigit() else None
    return resolve_one_title(con, title.strip(), year_val)


def _go_follow(nconst):
    st.session_state["view"] = "follow"
    st.session_state["follow_nconst"] = nconst
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
                _go_follow(nconst)


def _render_film_view(con, tconst):
    """Craft-first grouping -- craft departments elevated, cast secondary.
    Each person is a Follow(person) pivot point."""
    view = film_view(con, tconst)
    st.subheader(f"{view['title']} ({view['year']})")
    st.caption(", ".join(view["genres"]) or "no genre listed")
    for group in view["groups"]:
        st.markdown(f"**{group['category']}**")
        _render_person_buttons(group["people"], key_prefix=f"fv_{tconst}_{group['category']}")


def _render_follow_person(con):
    nconst = st.session_state["follow_nconst"]
    result = follow_person(con, nconst)
    if st.button("< Back to Explore"):
        st.session_state["view"] = "explore"
        st.rerun()
    st.subheader(f"Follow: {result['entity_name']}")
    st.caption(f"{len(result['films'])} credited films")
    for f in result["films"]:
        year = f["year"] if f["year"] is not None else "?"
        st.write(f"{year} — **{f['title']}** ({f['role']})")


def _render_explore_tab(con, person_degree):
    col1, col2 = st.columns([3, 1])
    with col1:
        title = st.text_input("Film title")
    with col2:
        year = st.text_input("Year (optional)")

    novelty = st.checkbox("Demote same-director results (default on)", value=True)
    n = st.slider("How many results", 5, 50, 20)

    if st.button("Explore") and title.strip():
        tconst = _resolve_from_inputs(con, title, year)
        if not tconst:
            st.error("No match found. Try adjusting the title or adding a year.")
            return
        st.session_state["explore_tconst"] = tconst

    tconst = st.session_state.get("explore_tconst")
    if not tconst:
        return

    _render_film_view(con, tconst)

    result = explore(con, tconst, n=n, novelty=novelty, person_degree=person_degree)

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
        title_a = st.text_input("Film A title", key="connect_title_a")
        year_a = st.text_input("Film A year (optional)", key="connect_year_a")
    with col2:
        title_b = st.text_input("Film B title", key="connect_title_b")
        year_b = st.text_input("Film B year (optional)", key="connect_year_b")
    hop_cap = st.slider("Max hops", 1, 8, 4)

    if st.button("Connect") and title_a.strip() and title_b.strip():
        tconst_a = _resolve_from_inputs(con, title_a, year_a)
        tconst_b = _resolve_from_inputs(con, title_b, year_b)
        if not tconst_a or not tconst_b:
            st.error("Couldn't resolve one or both titles. Try adding a year.")
            return
        try:
            result = connect(con, tconst_a, tconst_b, hop_cap=hop_cap)
        except ValueError as exc:
            st.error(str(exc))
            return

        st.subheader(f"{result['a']['title']} → {result['b']['title']}")
        if not result["found"]:
            st.warning(result["message"])
            return
        st.caption(f"{result['hops']} hops, strength {result['strength']}")
        for item in result["chain"]:
            if "person_name" in item:
                st.write(f"↳ {item['person_name']} ({item['role']})")
            else:
                year = item["year"] if item["year"] is not None else "?"
                st.markdown(f"**{item['title']}** ({year})")


def main():
    st.set_page_config(page_title="CineGraph")
    st.title("CineGraph")
    st.caption(
        "Explore, Connect, Follow -- structural substrate only "
        "(who / when / what-genre) -- never a style label, never votes."
    )

    con = _get_con()
    person_degree = _get_person_degree(con)

    st.session_state.setdefault("view", "explore")

    if st.session_state["view"] == "follow":
        _render_follow_person(con)
        return

    tab_explore, tab_connect = st.tabs(["Explore", "Connect"])
    with tab_explore:
        _render_explore_tab(con, person_degree)
    with tab_connect:
        _render_connect_tab(con)


if __name__ == "__main__":
    main()
