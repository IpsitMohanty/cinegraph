"""Title/year resolver: map an arbitrary title,year CSV onto the film
backbone built by cde.imdb.load().

Pandas-free by design -- all I/O and counting goes through DuckDB
(read_csv / COPY TO / fetchone() / fetchall()), not a DataFrame.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

# Read options for an arbitrary user-supplied title,year CSV. Unlike IMDb's
# TSVs this is a normal comma-delimited CSV, so we use DuckDB's default
# quoting -- everything still comes in as VARCHAR so year comparisons go
# through the same TRY_CAST path regardless of how the input was formatted.
_INPUT_READ_OPTS = "header=true, all_varchar=true"


def create_norm_macro(con: duckdb.DuckDBPyConnection) -> None:
    """Create a DuckDB scalar macro norm_title(t) that normalizes a title
    for fuzzy-ish exact matching:
      - lowercase
      - strip a single leading article ("the "/"a "/"an ")
      - drop punctuation (apostrophes, hyphens, colons, periods, commas, etc.)
      - collapse whitespace
      - trim
    """
    con.execute(r"""
        CREATE OR REPLACE MACRO norm_title(t) AS (
            trim(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(lower(t), '^(the|a|an)\s+', ''),
                        '[^a-z0-9]+', ' ', 'g'
                    ),
                    '\s+', ' ', 'g'
                )
            )
        )
    """)


def resolve_titles(
    con: duckdb.DuckDBPyConnection,
    csv_path: str | Path,
    title_col: str = "title",
    year_col: str = "year",
):
    """Resolve a title,year CSV against the film backbone (film +
    title_lookup, built by cde.imdb.load()).

    Matching: normalize both sides via norm_title(), join on the normalized
    key, keep candidates with |year - startYear| <= 1, then pick the best
    candidate per input row by (exact-year match first, then imdb_votes
    desc -- identity disambiguation only, not a value judgment; see README
    "Stance").

    Writes one row per input row to '<csv_path stem>.resolved.csv', with
    tconst/matched title/year/votes columns added (NULL when unmatched).

    Returns (out_path, matched, total):
      out_path -- path to the written CSV
      matched  -- count of input rows that found at least one candidate
      total    -- total input rows
    """
    create_norm_macro(con)

    csv_path = Path(csv_path)
    input_expr = f"read_csv('{_posix(csv_path)}', {_INPUT_READ_OPTS})"

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _resolve_input AS
        SELECT row_number() OVER () AS rowid, *
        FROM {input_expr}
    """)

    total = con.execute("SELECT COUNT(*) FROM _resolve_input").fetchone()[0]

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _resolve_candidates AS
        SELECT
            i.rowid AS input_rowid,
            i."{title_col}" AS input_title,
            i."{year_col}" AS input_year,
            tl.tconst,
            tl.title AS matched_title,
            tl.year AS matched_year,
            f.imdb_votes,
            (TRY_CAST(i."{year_col}" AS INTEGER) = tl.year) AS exact_year
        FROM _resolve_input i
        JOIN title_lookup tl
          ON norm_title(tl.title) = norm_title(i."{title_col}")
         AND ABS(TRY_CAST(i."{year_col}" AS INTEGER) - tl.year) <= 1
        JOIN film f ON f.tconst = tl.tconst
    """)

    con.execute("""
        CREATE OR REPLACE TEMP TABLE _resolve_best AS
        SELECT * FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY input_rowid
                    ORDER BY exact_year DESC, imdb_votes DESC NULLS LAST
                ) AS rn
            FROM _resolve_candidates
        )
        WHERE rn = 1
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _resolve_result AS
        SELECT
            i.rowid AS input_rowid,
            i."{title_col}" AS input_title,
            i."{year_col}" AS input_year,
            b.tconst,
            b.matched_title,
            b.matched_year,
            b.imdb_votes
        FROM _resolve_input i
        LEFT JOIN _resolve_best b ON b.input_rowid = i.rowid
        ORDER BY i.rowid
    """)

    matched = con.execute(
        "SELECT COUNT(*) FROM _resolve_result WHERE tconst IS NOT NULL"
    ).fetchone()[0]

    out_path = csv_path.with_suffix(".resolved.csv")
    con.execute(f"""
        COPY (SELECT * FROM _resolve_result ORDER BY input_rowid)
        TO '{_posix(out_path)}' (HEADER, DELIMITER ',')
    """)

    con.execute("DROP TABLE IF EXISTS _resolve_input")
    con.execute("DROP TABLE IF EXISTS _resolve_candidates")
    con.execute("DROP TABLE IF EXISTS _resolve_best")
    con.execute("DROP TABLE IF EXISTS _resolve_result")

    return out_path, matched, total


def _posix(path: Path) -> str:
    return str(path).replace("\\", "/")
