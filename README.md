# CineGraph

## What CineGraph is

CineGraph is a relational discovery engine over IMDb-derived film and
creative-credit data. It builds a local backbone of film identity
(`film`) and creative-collaboration edges (`credits`: director, writer,
cinematographer, editor, composer, producer, production_designer, cast —
`cde/people.py`), weights those edges by category and by how rare a
specific collaborator is (`category_weight x idf(degree)` — `cde/explore.py`),
and exposes three actions over the resulting graph: **Explore**, **Follow**,
**Connect** (`cde/explore.py`, `cde/follow.py`, `cde/connect.py`). It is
metadata-only — no plot text, no reviews, no embeddings, no LLM-derived
tags anywhere in the ranking path.

## Why it is not a conventional recommender

*(placeholder — the narrative goes here; see `PREDICTIONS.md`,
`PREDICTIONS_tuning.md`, and the `Stance` note below for the underlying
decisions this section will explain)*

One committed stance, stated plainly now rather than left to the
narrative: `imdb_rating` and `imdb_votes` are loaded as factual columns
and are **never** used as a filter or a ranker anywhere in this package —
not in Explore's scoring, novelty handling, or credit-importance
down-weight; not in Connect's path strength; not in Follow. Mass-user
preference does not enter the ontology. The only place `imdb_votes` is
touched at all is the title resolver's tie-break (`cde/resolve.py`),
deciding which real film a title+year pair refers to when more than one
candidate matches — identity disambiguation, not a value judgment about
quality.

## Core actions: Explore / Follow / Connect

- **Explore** — find worthwhile relational exits from one film. A
  result's score sums specific-person edges (rarer collaborator, stronger
  edge) plus small fixed bonuses for shared decade/genre, deliberately
  weaker than one real collaborator edge. Every result carries its
  explanation — who connects it, in what role on each side (bilateral:
  a person's role is never implied to be the same on both films when the
  data says otherwise), plus shared decade/genre. Cast edges are
  additionally down-weighted by billing order (`credit_importance` —
  `CREDIT_IMPORTANCE_K`), so an uncredited-tier cameo can't dominate
  discovery the way a top-billed lead legitimately can.

  *[screenshot: Explore result list for a seed film]*
  *[screenshot: craft-first film view]*

- **Follow** — pick a person, or a decade/genre thread, from a film's
  craft-first credit view, and pursue it. `Follow(person)` returns that
  person's full filmography with the role held on each film.
  `Follow(decade | genre)` returns films of that decade/genre scoped to
  the seed film's collaborator neighbourhood — a pivot on the graph, not a
  bare catalog filter. The entity registry is extensible: Wikidata-
  dependent types (`company`, `distributor`, `work`, `series`, `movement`,
  `festival`, `location`) are registered stubs (a clear response, never a
  crash) — not built in v1, see Known limitations.

  *[screenshot: Follow(person) filmography]*

- **Connect** — find a meaningful route between two films. Strongest-path,
  not shortest-path: traverses only strong-connector person edges (never
  cast, never decade/genre — a shared decade is never a hop), maximizing
  cumulative edge strength within a hop cap (default 4) rather than
  minimizing hop count. When no strong-connector path exists within the
  cap, or the search runs past its time budget, that is reported honestly
  — never a silent fallback to a weaker or context-only "connection."

  *[screenshot: a real multi-hop Connect chain]*
  *[screenshot: honest no-path / bounded-search state]*

## Architecture

```
IMDb non-commercial datasets (title.basics, title.principals, name.basics, ...)
        |
        v
canonical films + credits  (cde/imdb.py, cde/people.py -> film.duckdb)
        |
        v
weighted relational graph  (cde/explore.py: CATEGORY_WEIGHT x idf(degree),
                             shared edge-tier rule: strong-connector /
                             context-only / cast-conditional)
        |
        v
Explore / Follow / Connect  (cde/explore.py, cde/follow.py, cde/connect.py
                              -- pandas-free, importable without web deps)
        |
        v
FastAPI (app/api.py)  +  Streamlit (app/streamlit_app.py)
```

## Evaluation

*(scaffold — the full narrative and final numbers are written next; the
verified record already exists in-repo and is only referenced here, not
restated as prose)*

- **Methodology**: pre-registered predictions (`PREDICTIONS.md`,
  `PREDICTIONS_tuning.md`), written and committed *before* the eval
  harness (`eval_explore.py`) was run — checkable in git log ordering, not
  just asserted.
- **Judgment rubric**: each result labeled `interesting` / `trivial` /
  `wrong`; weak results further diagnosed as `data-limited` (a coverage
  gap, e.g. a thin-crew film) or `weighting-limited` (a scoring-tunable
  problem) — kept as separate axes on purpose, so a fix is aimed at the
  right layer.
- **Passes run**: an initial pass, a confirm-then-fix tuning pass
  (same-director penalty, temporal-plausibility gate), and a pre-ship pass
  (credit-importance billing down-weight). `[fill in: exact
  interesting/trivial/wrong counts per pass]` — see
  `eval/explore_eval_labeled.md`, `eval/explore_eval_tuned_labeled.md`,
  and the corresponding unlabeled tables for the full record.
- **Scalar tuning was deliberately stopped**, not exhausted: the dominant
  remaining failure mode (crew-clique list redundancy — near-duplicate
  results from one dense collaborator ecosystem crowding a seed's top-N)
  is a list-level diversity problem, not a per-edge weighting one, and is
  routed to a deferred MMR/xQuAD reranker rather than chased with more
  priors.

## Known limitations

Stated honestly, not smoothed over:

- **Graph completeness depends on available principal credits.** IMDb's
  `title.principals` caps entries per title; some real, well-known films
  (found during eval: *Breathless*) have only cast rows in the source
  data, so Explore has no strong-connector edge to promote for them
  regardless of scoring. Signaled in the UI (`thin_data`), not silently
  patched over.
- **Role semantics can be broader than ideal.** The only granularity
  available is IMDb's own `credits.category` (writer, cinematographer,
  ...) — real distinctions like screenplay vs. novel vs. story adaptation
  aren't in the current data. Roles are now rendered *bilaterally*
  (a person's role on each side of a connection is shown separately, never
  collapsed into one when they differ), but the label itself is still no
  finer than what `credits.category` carries.
- **Dense auteur/crew ecosystems create redundancy** in Explore's top-N
  (the crew-clique failure mode above) — a list-level problem, deferred to
  a diversity reranker.
- **Connect's latency grows with hop depth.** A bounded, non-exhaustive
  best-first search, not a provably optimal solver; hop cap 4 currently
  takes single-digit-to-low-teens seconds against the full backbone.
  Guarded with a conservative default cap, an execution timeout, and an
  honest "search exceeded budget" message — never a relaxed or fallback
  path to force a result within time.
- **Connect's answer to an identical query can vary between runs.**
  Among near-tied candidate paths, Python's per-process hash
  randomization was one source (fixed — frontiers now iterate in sorted
  order) and DuckDB's non-deterministic parallel-scan row order over the
  8M-row `credits` table is a second, not fixed (would require touching
  the query layer broadly enough to be a rewrite, out of scope here).
  Every individual result is still honest about itself; asking the same
  question twice is not guaranteed to draw the same answer — confirmed
  directly while building the public-demo artifact: repeated live runs of
  Connect(*The Conformist*, *The Godfather*) at the default hop cap found
  a real path roughly 7 times out of 8 sampled (strength 0.70–0.90), and
  "no path found" the rest. A live-engine roadmap item is to make
  candidate-neighbor selection order-stable so this stops being possible;
  not fixed here.
- **The public demo runs on a fixed, precomputed roster**, not the live
  engine. `CDE_DEMO_MODE=1` switches `app/streamlit_app.py` to read
  `demo/artifact.json` — a committed JSON file of Explore/Follow/Connect
  *outputs* for ~75 curated films, computed once by the real engine and
  frozen — instead of opening `film.duckdb` at all. This sidesteps the
  Connect non-determinism above by construction: each demo Connect pair is
  one specific, inspectable, checked-in answer, not a live query. A search
  outside the roster gets an honest "not in this demo" message pointing at
  the full local install, never an error or an empty hang. See "Data and
  licensing" for what the artifact does and does not contain.
- **Wikidata entity types are deferred.** `company`, `distributor`,
  `work`/based-on, `series`, `movement`, `festival`, `location` are
  registered stubs in Follow's entity registry, not built — Phase A of
  the (parked) Wikidata merge measured that layer too thin to build on
  before a first deploy (33.8% IMDb-id match rate, movement present on
  0.1%).
- **No attention/reception signal exists anywhere in the graph yet** —
  this is the same missing-signal gap across all three actions, not
  three separate problems: Explore's strongest edge can be to an obscure
  film, Follow's filmography has no sense of which entry mattered, and
  Connect's strongest-path traversal is indifferent to whether an
  intermediate film is itself noteworthy. The deferred reception/critical-
  attention layer is designed to fill this gap; none of the three actions
  papers over it with votes in the meantime.

## Local setup

```bash
# Core engine (pandas-free)
pip install -r requirements.txt
python -m cde.cli build                  # downloads + builds data/processed/film.duckdb
python -m cde.cli build --with-people     # + name.basics / title.principals

# API + UI
pip install -r requirements-app.txt
uvicorn app.api:app --reload              # GET /explore/{tconst}, /connect, /follow
streamlit run app/streamlit_app.py        # search -> Explore -> Follow / Connect

# Public-demo artifact (needs a locally built film.duckdb; never run in CI)
python build_demo_artifact.py             # writes demo/artifact.json
CDE_DEMO_MODE=1 streamlit run app/streamlit_app.py   # browses the artifact, no film.duckdb opened

# Deploy platforms expecting one requirements file: requirements-deploy.txt
# (= requirements.txt + requirements-app.txt combined; see that file's header)

# Development
pip install -r requirements.txt -r requirements-dev.txt
flake8 cde tests
pytest -q -m "not integration"           # unit tests (what CI runs)
pytest -q -m integration                 # + integration, needs a built film.duckdb
```

`film.duckdb` is required and is never bundled with the repo (see Data and
licensing) — both `app/api.py` and `app/streamlit_app.py` fail with a
clear, actionable message (not a raw database error) if it's absent.
`CDE_DB_PATH` (and `CDE_DATA_RAW`/`CDE_DATA_PROCESSED`) override the
default local paths for a deploy environment.

## Data and licensing

- **Code**: MIT licensed (`LICENSE`).
- **IMDb data**: the [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/),
  under IMDb's personal/non-commercial terms. **Never redistributed** by
  this repo or its deployed demo, in any form — `data/raw/` and
  `data/processed/` are gitignored; `python -m cde.cli build` regenerates
  them locally from IMDb directly.
- **The public demo ships a results artifact, not a data dump.**
  `demo/artifact.json` (`cde/demo.py`, built by `build_demo_artifact.py`)
  is committed to this repo and served by `app/streamlit_app.py` when
  `CDE_DEMO_MODE=1` is set — but it holds only *derived outputs*: film
  titles, years, tconsts as opaque ids, computed Explore/Connect/Follow
  results, roles as displayed, scores. It is never a reshaping of the
  `credits`/`person` tables — that would still be IMDb's relational data
  under a different file format, and the whole point of this file's
  existence is to not do that. `tests/test_demo.py` enforces this
  mechanically: it walks the generated artifact for table-shaped keys
  (`credits`, `ordering`, `ratings`, `imdb_rating`, `imdb_votes`) and
  fails the suite if any leak in. The roster is curated (~75 films: the
  12 era/country-spread eval seeds, weighted toward results the labeled
  eval scored `interesting`) so a demo visitor's first click lands on the
  engine at its best — including *The Conformist* and *The Godfather*
  (the strongest labeled Explore results, and Connect's headline chain
  between them) and *Breathless* (deliberately included as a thin-data
  case: the demo shows the engine being honest about a coverage gap, not
  only its wins). The full live engine — the real thing this demo is a
  frozen sample of — runs locally against the complete IMDb-derived
  backbone; see "Local setup".
- **Wikidata enrichment is not required for v1.** The Wikidata merge
  (country, movement, based-on, series, collaborator edges, all CC0) is
  explored on a parked branch and would be additive, licensed separately
  under CC0 — not needed for anything documented above.
- The `isAdult` filter (dropped from the backbone) is a stance, not a
  neutral default: it can catch legitimately transgressive canon alongside
  what it's meant to exclude. Kept as-is to preserve a pinned, verified
  corpus count; revisit when corpus definition is addressed directly.
