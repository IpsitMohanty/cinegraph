"""Download and load the IMDb non-commercial datasets into DuckDB.

No secrets, no network auth -- IMDb's non-commercial datasets are public,
gzip-compressed TSVs served over plain HTTPS.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import requests

from cde.config import DATA_RAW, DEFAULT_TYPES, IMDB_BASE, READ_OPTS

# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def download(fname: str, force: bool = False) -> Path:
    """Download one IMDb dataset file into data/raw/, streaming to disk.

    Skips re-downloading if the file already exists, unless force=True.
    Returns the local path.
    """
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    dest = DATA_RAW / fname

    if dest.exists() and not force:
        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  [skip] {fname} already present ({size_mb:.1f} MB)")
        return dest

    url = IMDB_BASE + fname
    print(f"  [get]  {fname} <- {url}")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
        tmp.replace(dest)

    size_mb = dest.stat().st_size / (1024 * 1024)
    if total:
        print(f"  [done] {fname} ({size_mb:.1f} MB, expected {total / (1024 * 1024):.1f} MB)")
    else:
        print(f"  [done] {fname} ({size_mb:.1f} MB)")
    return dest


def report_sizes(files: list[str]) -> None:
    """Print known local sizes (and remote sizes if not yet downloaded) for
    the given dataset files, without downloading anything."""
    print("Download size report:")
    for fname in files:
        dest = DATA_RAW / fname
        if dest.exists():
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f"  {fname}: {size_mb:.1f} MB (local)")
            continue
        try:
            resp = requests.head(IMDB_BASE + fname, allow_redirects=True, timeout=30)
            length = resp.headers.get("content-length")
            if length:
                print(f"  {fname}: {int(length) / (1024 * 1024):.1f} MB (remote, not downloaded)")
            else:
                print(f"  {fname}: size unknown (remote, not downloaded)")
        except requests.RequestException as exc:
            print(f"  {fname}: could not reach remote ({exc})")


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------


def _read_csv_expr(path: Path) -> str:
    posix = str(path).replace("\\", "/")
    return f"read_csv('{posix}', {READ_OPTS})"


def load(con: duckdb.DuckDBPyConnection, with_people: bool = False,
         types: tuple[str, ...] | list[str] | None = None) -> None:
    """Load the downloaded TSVs into DuckDB tables and build the film
    backbone + title_lookup used by the resolver.

    Expects the relevant files to already be present in data/raw/ (see
    download()).
    """
    types = types or DEFAULT_TYPES
    type_list_sql = ", ".join(f"'{t}'" for t in types)

    basics = DATA_RAW / "title.basics.tsv.gz"
    akas = DATA_RAW / "title.akas.tsv.gz"
    ratings = DATA_RAW / "title.ratings.tsv.gz"
    crew = DATA_RAW / "title.crew.tsv.gz"

    for p in (basics, akas, ratings, crew):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} not found -- run download() for CORE_FILES first"
            )

    # film: filtered, typed core table.
    con.execute(f"""
        CREATE OR REPLACE TABLE film AS
        SELECT
            tconst,
            titleType,
            primaryTitle,
            originalTitle,
            isAdult,
            TRY_CAST(startYear AS INTEGER) AS startYear,
            TRY_CAST(endYear AS INTEGER) AS endYear,
            TRY_CAST(runtimeMinutes AS INTEGER) AS runtimeMinutes,
            genres
        FROM {_read_csv_expr(basics)}
        WHERE titleType IN ({type_list_sql})
          AND isAdult = '0'
    """)

    # ratings, joined onto film by tconst. NOTE: imdb_rating / imdb_votes
    # are loaded as factual columns only -- they are never used as a filter
    # or a ranker in this package. See README "Stance".
    con.execute(f"""
        CREATE OR REPLACE TABLE _ratings AS
        SELECT
            tconst,
            TRY_CAST(averageRating AS DOUBLE) AS imdb_rating,
            TRY_CAST(numVotes AS INTEGER) AS imdb_votes
        FROM {_read_csv_expr(ratings)}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE film AS
        SELECT f.*, r.imdb_rating, r.imdb_votes
        FROM film f
        LEFT JOIN _ratings r USING (tconst)
    """)

    # crew, joined onto film by tconst.
    con.execute(f"""
        CREATE OR REPLACE TABLE _crew AS
        SELECT tconst, directors, writers
        FROM {_read_csv_expr(crew)}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE film AS
        SELECT f.*, c.directors, c.writers
        FROM film f
        LEFT JOIN _crew c USING (tconst)
    """)
    con.execute("DROP TABLE _ratings")
    con.execute("DROP TABLE _crew")

    # title_lookup: every (tconst, title) pair we're willing to match on --
    # primary title, original title (when it differs), and all akas -- so
    # the resolver can hit non-English/alternate titles too.
    con.execute(f"""
        CREATE OR REPLACE TABLE title_lookup AS
        SELECT tconst, primaryTitle AS title, startYear AS year, 'primary' AS source
        FROM film
        UNION
        SELECT tconst, originalTitle AS title, startYear AS year, 'original' AS source
        FROM film
        WHERE originalTitle IS NOT NULL AND originalTitle != primaryTitle
        UNION
        SELECT a.titleId AS tconst, a.title AS title, f.startYear AS year, 'aka' AS source
        FROM {_read_csv_expr(akas)} a
        JOIN film f ON f.tconst = a.titleId
    """)

    if with_people:
        name_basics = DATA_RAW / "name.basics.tsv.gz"
        principals = DATA_RAW / "title.principals.tsv.gz"
        for p in (name_basics, principals):
            if not p.exists():
                raise FileNotFoundError(
                    f"{p} not found -- run download() for PEOPLE_FILES first"
                )
        con.execute(f"""
            CREATE OR REPLACE TABLE person AS
            SELECT
                nconst,
                primaryName,
                TRY_CAST(birthYear AS INTEGER) AS birthYear,
                TRY_CAST(deathYear AS INTEGER) AS deathYear,
                primaryProfession,
                knownForTitles
            FROM {_read_csv_expr(name_basics)}
        """)
        con.execute(f"""
            CREATE OR REPLACE TABLE principal AS
            SELECT
                p.tconst,
                TRY_CAST(p.ordering AS INTEGER) AS ordering,
                p.nconst,
                p.category,
                p.job,
                p.characters
            FROM {_read_csv_expr(principals)} p
            JOIN film f ON f.tconst = p.tconst
        """)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def report(con: duckdb.DuckDBPyConnection) -> None:
    """Print a summary of the loaded backbone."""
    print("\n=== IMDb backbone report ===")

    total = con.execute("SELECT COUNT(*) FROM film").fetchone()[0]
    print(f"film rows: {total}")

    by_type = con.execute("""
        SELECT titleType, COUNT(*) AS n
        FROM film GROUP BY titleType ORDER BY n DESC
    """).fetchall()
    for t, n in by_type:
        print(f"  {t}: {n}")

    rated = con.execute("SELECT COUNT(*) FROM film WHERE imdb_rating IS NOT NULL").fetchone()[0]
    if total:
        print(f"film rows with a rating: {rated} ({rated / total:.1%})")
    else:
        print("film rows with a rating: 0")

    lookup_n = con.execute("SELECT COUNT(*) FROM title_lookup").fetchone()[0]
    lookup_titles = con.execute("SELECT COUNT(DISTINCT tconst) FROM title_lookup").fetchone()[0]
    print(f"title_lookup rows: {lookup_n} (covering {lookup_titles} distinct titles)")

    by_source = con.execute("""
        SELECT source, COUNT(*) AS n FROM title_lookup GROUP BY source ORDER BY n DESC
    """).fetchall()
    for s, n in by_source:
        print(f"  from {s}: {n}")

    has_person = con.execute("""
        SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'person'
    """).fetchone()[0]
    if has_person:
        people = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        principals = con.execute("SELECT COUNT(*) FROM principal").fetchone()[0]
        print(f"person rows: {people}")
        print(f"principal rows: {principals}")

    print("=============================\n")
