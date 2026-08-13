"""`python -m cde.cli build|resolve`"""

from __future__ import annotations

import argparse
import sys

import duckdb

from cde.config import CORE_FILES, DATA_PROCESSED, DB_PATH, DEFAULT_TYPES, PEOPLE_FILES
from cde.imdb import download, load, report, report_sizes
from cde.resolve import resolve_titles


def _cmd_build(args: argparse.Namespace) -> None:
    files = list(CORE_FILES)
    if args.with_people:
        files += PEOPLE_FILES

    if args.report_only:
        report_sizes(files)
        return

    print("Downloading IMDb datasets...")
    for fname in files:
        download(fname, force=args.force)

    print("\nLoading into DuckDB...")
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        load(con, with_people=args.with_people, types=args.types)
        report(con)
    finally:
        con.close()


def _cmd_resolve(args: argparse.Namespace) -> None:
    con = duckdb.connect(str(DB_PATH))
    try:
        out_path, matched, total = resolve_titles(con, args.csv, args.title_col, args.year_col)
        if total:
            print(f"Resolved {matched}/{total} rows ({matched / total:.1%})")
        else:
            print("No input rows")
        print(f"Wrote {out_path}")
    finally:
        con.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Cinephile discovery engine -- IMDb backbone (stage 1)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser(
        "build", help="download IMDb datasets and build the DuckDB backbone"
    )
    p_build.add_argument(
        "--with-people", action="store_true", help="also load name.basics/title.principals"
    )
    p_build.add_argument(
        "--types", nargs="+", default=list(DEFAULT_TYPES),
        help=f"titleType values to keep (default: {list(DEFAULT_TYPES)})",
    )
    p_build.add_argument(
        "--report-only", action="store_true", help="print sizes only, do not download"
    )
    p_build.add_argument(
        "--force", action="store_true", help="re-download even if files already exist"
    )
    p_build.set_defaults(func=_cmd_build)

    p_resolve = sub.add_parser("resolve", help="resolve a title,year CSV against the backbone")
    p_resolve.add_argument("csv", help="path to a CSV with title/year columns")
    p_resolve.add_argument("--title-col", default="title")
    p_resolve.add_argument("--year-col", default="year")
    p_resolve.set_defaults(func=_cmd_resolve)

    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
