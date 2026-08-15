"""Shared configuration for the cde package."""

from __future__ import annotations

from pathlib import Path

IMDB_BASE = "https://datasets.imdbws.com/"

# Files needed for the film backbone itself (titles, akas, ratings, crew).
CORE_FILES = [
    "title.basics.tsv.gz",
    "title.akas.tsv.gz",
    "title.ratings.tsv.gz",
    "title.crew.tsv.gz",
]

# Optional files for people-level enrichment (directors/writers/cast names).
PEOPLE_FILES = [
    "name.basics.tsv.gz",
    "title.principals.tsv.gz",
]

# Shorts are a noisy tail. Keep the default corpus to features and add
# shorts back deliberately later, if at all.
DEFAULT_TYPES = ("movie",)

# The creative-collaboration categories kept in `credits` (cde.people) --
# the collaborator-network edges stage 3A navigates on. Deliberately
# excludes `self`, `archive_footage`, `archive_sound`: real presence, not
# creative collaboration.
CREDIT_CATEGORIES = (
    "director", "writer", "cinematographer", "composer", "editor",
    "producer", "actor", "actress", "production_designer",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = _PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = _PROJECT_ROOT / "data" / "processed"
DB_PATH = DATA_PROCESSED / "film.duckdb"

# Common DuckDB CSV read options for IMDb's TSV format: tab-delimited, no
# quote character (IMDb doesn't quote fields), "\N" is the null sentinel,
# and everything comes in as VARCHAR so we can control casting explicitly.
READ_OPTS = "delim='\\t', quote='', nullstr='\\N', all_varchar=true, header=true"
