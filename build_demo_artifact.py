"""Build the public-demo artifact (demo/artifact.json) by running the real
engine over the curated roster (cde.demo.ROSTER) and Connect pairs
(cde.demo.CONNECT_PAIRS) against the real film.duckdb, then freezing the
results to JSON.

Run locally only, against the real backbone -- never in CI, never against
a reduced/live corpus pretending to be the full one. See cde/demo.py's
module docstring for the licensing guardrail this exists to satisfy.

Usage: python build_demo_artifact.py [--out demo/artifact.json]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from cde.config import DB_PATH
from cde.demo import ARTIFACT_PATH, CONNECT_PAIRS, ROSTER, build_artifact, save_artifact


def main():
    parser = argparse.ArgumentParser(description="Build the CineGraph public-demo artifact")
    parser.add_argument("--out", default=str(ARTIFACT_PATH))
    args = parser.parse_args()
    out_path = Path(args.out)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print(f"Building artifact: {len(ROSTER)} roster films, "
              f"{len(CONNECT_PAIRS)} Connect pairs...")
        t0 = time.monotonic()
        artifact = build_artifact(con, roster=ROSTER, pairs=CONNECT_PAIRS)
        elapsed = time.monotonic() - t0
        print(f"Built in {elapsed:.1f}s")

        n_thin = sum(1 for s in artifact["seeds"].values() if s["thin_data"])
        n_found = sum(1 for c in artifact["connect_pairs"].values() if c["found"])
        print(f"  seeds: {len(artifact['seeds'])} ({n_thin} thin_data)")
        print(f"  connect_pairs: {len(artifact['connect_pairs'])} ({n_found} found, "
              f"{len(artifact['connect_pairs']) - n_found} no-path)")

        save_artifact(artifact, out_path)
    finally:
        con.close()

    size_kb = out_path.stat().st_size / 1024
    print(f"Wrote {out_path} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
