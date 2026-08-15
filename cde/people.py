"""IMDb people load: `credits` + `person` tables, and the collaborator-edge
density report that scopes stage 3A.

Additive to the stage-1 cde/imdb.py -- loads title.principals.tsv.gz /
name.basics.tsv.gz (the two IMDb people files stage 1 never loaded) into
two new tables in film.duckdb:

  credits -- (tconst, nconst, category, ordering), restricted to `film`
             tconsts and the creative categories in CREDIT_CATEGORIES.
  person  -- (nconst, primary_name), restricted to nconsts that appear in
             credits (no need to load all ~14M people in name.basics).

Note: stage 1's optional `load(with_people=True)` path also builds tables
named `principal`/`person` (unfiltered categories, a wider `person` schema
with birth/death year etc.). This module's `person` table has a different,
narrower schema and is built independently -- don't run both loaders
against the same connection, the second `CREATE OR REPLACE TABLE person`
will silently win.

IMDb data is compute-only: `credits`/`person` live in film.duckdb
(gitignored), never in data/enriched/ -- nothing IMDb-derived is
committable. Report-only in this pass: this module loads and reports edge
density, it does not build stage 3A navigation.
"""

from __future__ import annotations

import duckdb

from cde.config import CREDIT_CATEGORIES, DATA_RAW, READ_OPTS


def _read_csv_expr(path):
    posix = str(path).replace("\\", "/")
    return f"read_csv('{posix}', {READ_OPTS})"


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------


def load_people(con: duckdb.DuckDBPyConnection, categories=CREDIT_CATEGORIES) -> None:
    """Build `credits` and `person` in `con` from title.principals.tsv.gz /
    name.basics.tsv.gz in data/raw/. Requires `film` to already be loaded
    (see cde.imdb.load()).
    """
    principals = DATA_RAW / "title.principals.tsv.gz"
    name_basics = DATA_RAW / "name.basics.tsv.gz"
    for p in (principals, name_basics):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} not found -- run cde.imdb.download() for PEOPLE_FILES first"
            )

    category_list_sql = ", ".join(f"'{c}'" for c in categories)

    # credits: creative-collaboration edges, restricted to films already in
    # `film` (the JOIN drops principals rows for non-film tconsts) and the
    # creative category set (drops self/archive_footage/archive_sound and
    # anything else not in CREDIT_CATEGORIES).
    con.execute(f"""
        CREATE OR REPLACE TABLE credits AS
        SELECT
            p.tconst,
            p.nconst,
            p.category,
            TRY_CAST(p.ordering AS INTEGER) AS ordering
        FROM {_read_csv_expr(principals)} p
        JOIN film f ON f.tconst = p.tconst
        WHERE p.category IN ({category_list_sql})
    """)

    # person: only the nconsts that actually appear in credits -- no need
    # to load all ~14M people in name.basics.
    con.execute(f"""
        CREATE OR REPLACE TABLE person AS
        SELECT DISTINCT
            n.nconst,
            n.primaryName AS primary_name
        FROM {_read_csv_expr(name_basics)} n
        WHERE n.nconst IN (SELECT DISTINCT nconst FROM credits)
    """)


# --------------------------------------------------------------------------
# Density report
# --------------------------------------------------------------------------


def compute_density(con: duckdb.DuckDBPyConnection) -> dict:
    """Pure computation (reads `con`, no printing) so it's directly
    unit-testable against a tiny fixture db. Requires `film`, `credits`,
    `person` to already be loaded."""
    total_films = con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    total_edges = con.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
    total_persons = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]

    coverage = {}
    for cat, n in con.execute("""
        SELECT category, COUNT(DISTINCT tconst)
        FROM credits GROUP BY category ORDER BY 2 DESC
    """).fetchall():
        coverage[cat] = {
            "films": n,
            "pct": round(n / total_films * 100, 1) if total_films else 0.0,
        }

    n_with_credits, collab_mean, collab_median, collab_min, collab_max = con.execute("""
        WITH per_film AS (
            SELECT tconst, COUNT(DISTINCT nconst) AS n
            FROM credits GROUP BY tconst
        )
        SELECT COUNT(*), AVG(n), MEDIAN(n), MIN(n), MAX(n) FROM per_film
    """).fetchone()

    n_persons_deg, degree_mean, degree_median, degree_max, persons_ge5 = con.execute("""
        WITH deg AS (
            SELECT nconst, COUNT(DISTINCT tconst) AS n
            FROM credits GROUP BY nconst
        )
        SELECT COUNT(*), AVG(n), MEDIAN(n), MAX(n),
               SUM(CASE WHEN n >= 5 THEN 1 ELSE 0 END)
        FROM deg
    """).fetchone()

    return {
        "total_films": total_films,
        "total_edges": total_edges,
        "total_persons": total_persons,
        "coverage": coverage,
        "films_with_credits": n_with_credits,
        "collab_mean": collab_mean,
        "collab_median": collab_median,
        "collab_min": collab_min,
        "collab_max": collab_max,
        "persons_with_credits": n_persons_deg,
        "degree_mean": degree_mean,
        "degree_median": degree_median,
        "degree_max": degree_max,
        "persons_ge5": persons_ge5,
    }


def report_people(con: duckdb.DuckDBPyConnection) -> dict:
    """Print the collaborator-edge density report and return the
    underlying stats dict."""
    stats = compute_density(con)
    total_films = stats["total_films"]

    print("\n=== IMDb people / collaborator-edge density report ===")
    print(f"credits edges: {stats['total_edges']}")
    print(f"distinct persons: {stats['total_persons']}")

    print("\nper-category film coverage "
          "(headline: cinematographer / composer / editor):")
    for cat, c in stats["coverage"].items():
        print(f"  {cat}: {c['films']} films ({c['pct']}%)")
    missing = set(CREDIT_CATEGORIES) - set(stats["coverage"])
    for cat in sorted(missing):
        print(f"  {cat}: 0 films (0.0%)")

    print("\ncollaborators per film (distinct persons credited):")
    if total_films:
        pct = stats["films_with_credits"] / total_films * 100
        print(f"  films with >=1 credit: {stats['films_with_credits']} ({pct:.1f}% of film)")
    print(f"  mean: {stats['collab_mean']:.1f}, median: {stats['collab_median']:.0f}, "
          f"min: {stats['collab_min']}, max: {stats['collab_max']}")

    print("\nperson -> film degree "
          "(inverse-frequency input for 3A edge weighting):")
    print(f"  persons: {stats['persons_with_credits']}, "
          f"mean films: {stats['degree_mean']:.1f}, "
          f"median films: {stats['degree_median']:.0f}, "
          f"max films: {stats['degree_max']}")
    if stats["persons_with_credits"]:
        ge5_pct = stats["persons_ge5"] / stats["persons_with_credits"] * 100
        print(f"  persons connecting >=5 films: {stats['persons_ge5']} ({ge5_pct:.1f}%)")

    print("========================================================\n")
    return stats
