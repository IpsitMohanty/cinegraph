"""Minimal Streamlit UI for CineGraph Explore: a title search box, then
the ranked explained list. Plain list -- no graph/network visualization.

Web-only deps (streamlit) live in requirements-app.txt -- the engine
(cde/explore.py) never imports this module.

Run: streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import duckdb
import streamlit as st

from cde.config import DB_PATH
from cde.explore import build_person_degree, explore
from cde.resolve import resolve_one_title


@st.cache_resource
def _get_con():
    # Not read_only: resolve_one_title() (via resolve_titles()) creates a
    # DuckDB macro + temp tables to do its matching, which DuckDB refuses
    # on a read_only connection. cde.explore itself issues nothing but
    # SELECT against film/credits/person -- see cde/explore.py -- so this
    # doesn't compromise Explore's read-only contract, only accommodates
    # the resolver's catalog-level bookkeeping. app/api.py, which only
    # ever calls explore(), stays read_only=True.
    return duckdb.connect(str(DB_PATH))


@st.cache_resource
def _get_person_degree(_con):
    return build_person_degree(_con)


def main():
    st.set_page_config(page_title="CineGraph Explore")
    st.title("CineGraph Explore")
    st.caption(
        "Film in -> ranked, explained connected films out. Structural substrate "
        "only (who / when / what-genre) -- never a style label, never votes."
    )

    con = _get_con()
    person_degree = _get_person_degree(con)

    col1, col2 = st.columns([3, 1])
    with col1:
        title = st.text_input("Film title")
    with col2:
        year = st.text_input("Year (optional)")

    novelty = st.checkbox(
        "Novelty mode (down-weight same-director results)", value=False
    )
    n = st.slider("How many results", 5, 50, 20)

    if st.button("Explore") and title.strip():
        year_val = int(year) if year.strip().isdigit() else None
        tconst = resolve_one_title(con, title.strip(), year_val)

        if not tconst:
            st.error("No match found. Try adjusting the title or adding a year.")
            return

        result = explore(con, tconst, n=n, novelty=novelty, person_degree=person_degree)
        seed = result["seed"]
        st.subheader(f"{seed['title']} ({seed['year']})")
        st.caption(", ".join(seed["genres"]) or "no genre listed")

        if not result["results"]:
            st.write("No connected films found (no shared creative collaborators).")
            return

        for r in result["results"]:
            st.markdown(f"**{r['title']} ({r['year']})** — score {r['score']}")
            st.write(r["explanation"])
            st.divider()


if __name__ == "__main__":
    main()
