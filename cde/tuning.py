"""Stage 3A tuning, Phase 1: crosstabs over the labeled eval.

Report-only tooling -- reads the labeled eval table plus `film`/`credits`/
`person` (read-only) and computes the two crosstabs PREDICTIONS_tuning.md
pre-registers. Nothing here touches cde/explore.py's scoring; this module
exists to confirm (or falsify) the predictions before any fix is written.

Pandas-free, consistent with the rest of cde/.
"""

from __future__ import annotations

import re
from collections import Counter

import duckdb

from cde.resolve import resolve_one_title

YEAR_GAP_BUCKETS = [(0, 15), (16, 40), (41, None)]


# --------------------------------------------------------------------------
# Parsing the labeled eval markdown table
# --------------------------------------------------------------------------

_ROW_RE = re.compile(r"^\|(.+)\|\s*$")


def parse_labeled_eval(path) -> list[dict]:
    """Parse eval/explore_eval_labeled.md's table into row dicts. Pure
    string parsing, no DB access -- skips the header/separator rows and any
    non-table lines."""
    rows = []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        m = _ROW_RE.match(line.rstrip("\n"))
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 8:
            continue
        seed, rank, result, tconst, score, explanation, judgment, failure_mode = cells[:8]
        if seed == "seed" or set(seed) == {"-"}:
            continue  # header / separator row
        seed_title, seed_year = _split_title_year(seed)
        result_title, result_year = _split_title_year(result)
        rows.append({
            "seed_title": seed_title,
            "seed_year": seed_year,
            "rank": rank,
            "result_title": result_title,
            "result_year": result_year,
            "tconst": tconst,
            "score": score,
            "explanation": explanation,
            "judgment": judgment,
            "failure_mode": failure_mode or None,
        })
    return rows


_TITLE_YEAR_RE = re.compile(r"^(.*)\((\d{4})\)$")


def _split_title_year(label: str):
    m = _TITLE_YEAR_RE.match(label.strip())
    if not m:
        return label.strip(), None
    return m.group(1).strip(), int(m.group(2))


# --------------------------------------------------------------------------
# crosstab_A: judgment x same-director / cross-director
# --------------------------------------------------------------------------

def is_same_director(con: duckdb.DuckDBPyConnection, seed_tconst: str, result_tconst: str) -> bool:
    """True if some person is credited as 'director' on BOTH films -- the
    same check cde.explore's novelty flag uses, not a string-match on the
    explanation text."""
    row = con.execute("""
        SELECT COUNT(*) FROM credits a
        JOIN credits b ON a.nconst = b.nconst
        WHERE a.tconst = ? AND b.tconst = ?
          AND a.category = 'director' AND b.category = 'director'
    """, [seed_tconst, result_tconst]).fetchone()
    return row[0] > 0


def build_crosstab_A(labeled_rows: list[dict]) -> dict:
    """Pure aggregation over rows that already carry a 'same_director'
    bool -- unit-testable without a DB. judgment x same-director/
    cross-director counts."""
    table = Counter()
    for row in labeled_rows:
        key = (row["judgment"], "same_director" if row["same_director"] else "cross_director")
        table[key] += 1
    return dict(table)


def annotate_same_director(con, labeled_rows: list[dict], resolve_cache=None) -> list[dict]:
    """Resolve each row's seed tconst (title, year) and tag same_director,
    returning new dicts (rows carry their own tconst for the result
    already)."""
    resolve_cache = resolve_cache if resolve_cache is not None else {}
    out = []
    for row in labeled_rows:
        seed_key = (row["seed_title"], row["seed_year"])
        if seed_key not in resolve_cache:
            resolve_cache[seed_key] = resolve_one_title(
                con, row["seed_title"], row["seed_year"]
            )
        seed_tconst = resolve_cache[seed_key]

        annotated = dict(row)
        annotated["seed_tconst"] = seed_tconst
        if seed_tconst and row["tconst"] and row["tconst"] != "-":
            annotated["same_director"] = is_same_director(con, seed_tconst, row["tconst"])
        else:
            annotated["same_director"] = False
        out.append(annotated)
    return out


# --------------------------------------------------------------------------
# crosstab_B: judgment x release-year gap, temporal-implausibility flag
# --------------------------------------------------------------------------

def year_gap_bucket(gap: int | None) -> str:
    if gap is None:
        return "unknown"
    for lo, hi in YEAR_GAP_BUCKETS:
        if hi is None:
            if gap >= lo:
                return f"{lo}+"
        elif lo <= gap <= hi:
            return f"{lo}-{hi}"
    return "unknown"


def build_crosstab_B(labeled_rows: list[dict]) -> dict:
    """Pure aggregation over rows that already carry 'year_gap' -- judgment
    x year-gap bucket counts."""
    table = Counter()
    for row in labeled_rows:
        bucket = year_gap_bucket(row.get("year_gap"))
        table[(row["judgment"], bucket)] += 1
    return dict(table)


def is_temporally_implausible(
    con: duckdb.DuckDBPyConnection,
    seed_tconst: str,
    result_tconst: str,
    seed_year: int | None,
    result_year: int | None,
) -> bool:
    """True if some connecting person's birth_year postdates the older film,
    or death_year predates it -- the rescore/re-release signature. False
    (not flagged) when there's no shared person with birth/death data at
    all, since absence of data isn't evidence of implausibility."""
    if seed_year is None or result_year is None:
        return False
    older_year = min(seed_year, result_year)

    rows = con.execute("""
        SELECT DISTINCT p.birth_year, p.death_year
        FROM credits a
        JOIN credits b ON a.nconst = b.nconst
        JOIN person p ON p.nconst = a.nconst
        WHERE a.tconst = ? AND b.tconst = ?
    """, [seed_tconst, result_tconst]).fetchall()

    for birth_year, death_year in rows:
        if birth_year is not None and birth_year > older_year:
            return True
        if death_year is not None and death_year < older_year:
            return True
    return False


def annotate_year_gap(con, labeled_rows: list[dict]) -> list[dict]:
    """Adds 'year_gap' and 'temporally_implausible' to rows that already
    carry 'seed_tconst' (see annotate_same_director)."""
    out = []
    for row in labeled_rows:
        annotated = dict(row)
        seed_tconst = row.get("seed_tconst")
        result_tconst = row["tconst"] if row["tconst"] != "-" else None
        seed_year, result_year = row["seed_year"], row["result_year"]

        if seed_year is not None and result_year is not None:
            annotated["year_gap"] = abs(result_year - seed_year)
        else:
            annotated["year_gap"] = None

        if seed_tconst and result_tconst:
            annotated["temporally_implausible"] = is_temporally_implausible(
                con, seed_tconst, result_tconst, seed_year, result_year
            )
        else:
            annotated["temporally_implausible"] = False
        out.append(annotated)
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_crosstabs(con: duckdb.DuckDBPyConnection, labeled_eval_path) -> dict:
    """Parse the labeled eval, annotate every row with same_director /
    year_gap / temporally_implausible, build both crosstabs, and compute
    the temporal-flag catch rate on the 12 `wrong` rows. Read-only."""
    rows = parse_labeled_eval(labeled_eval_path)
    rows = annotate_same_director(con, rows)
    rows = annotate_year_gap(con, rows)

    crosstab_A = build_crosstab_A(rows)
    crosstab_B = build_crosstab_B(rows)

    wrong_rows = [r for r in rows if r["judgment"] == "wrong"]
    wrong_caught = sum(1 for r in wrong_rows if r["temporally_implausible"])

    return {
        "rows": rows,
        "crosstab_A": crosstab_A,
        "crosstab_B": crosstab_B,
        "n_wrong": len(wrong_rows),
        "n_wrong_caught_by_temporal_flag": wrong_caught,
    }
