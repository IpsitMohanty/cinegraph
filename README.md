# cinephile-discovery-engine

A metadata-only, graph-based cinematic discovery engine. It builds a local
backbone of film identity and structural relationships (title, year, cast,
crew, and — in later stages — country, movement, and based-on/collaborator
edges) from public metadata sources. It is not a rating recommender: nothing
here optimizes for what audiences liked.

## Stance

`imdb_rating` and `imdb_votes` are loaded as factual columns and are **never**
used as a filter or a ranker anywhere in this package — including in CineGraph
Explore's scoring, ranking, and novelty handling (stage 3A). Mass-user
preference does not enter the ontology. The only place `imdb_votes` is touched
at all is the resolver's tie-break, and only as identity disambiguation
(deciding which real film a title+year pair refers to when more than one
candidate matches) — not as a value judgment about quality. That resolver path
is **used by Explore**: `resolve_one_title()` is its title-search-box entry
point, turning a typed title (+ optional year) into a tconst before handing it
to `explore()`. (Wikidata enrichment, stage 2, joins on `tconst` directly
rather than through the resolver — that path stays parked on its own branch.)

## Data & licensing

- Code is MIT licensed (see `LICENSE`).
- Data comes from the [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/),
  used under IMDb's personal/non-commercial terms. It is **not redistributed**
  by this repo — `data/raw/` (the downloaded TSVs) and `data/processed/`
  (the built DuckDB file) are gitignored; running `python -m cde.cli build`
  regenerates them locally.

### isAdult caveat

The backbone drops rows where IMDb's `isAdult` flag is `'1'`. This is a
blanket filter, not a neutral default: IMDb's adult flag can catch
legitimately transgressive canon (certain Oshima- and Pasolini-adjacent work,
for instance) alongside what it's actually meant to exclude. It's kept as-is
for this stage to preserve a pinned, verified count, but it's a known
cinephile caveat to revisit when corpus definition is addressed directly
(see "Out of scope" below).

## CineGraph Explore (stage 3A)

Explore is the product's first action, and its walking skeleton: film in ->
ranked, explained connected films out. It reads `film`, `credits` (the
creative-collaboration edges loaded by `cde.people`), and `person`; it never
ranks by audience preference. A result's score is a sum of specific-person
edges (`category_weight[role] * idf(collaborator's degree)` — rarer
collaborator, stronger edge) plus small fixed bonuses for shared decade/genre
that are deliberately weaker than one real collaborator edge. Every result
comes with its explanation (who connects it, in what role, plus shared
decade/genre) — a graph blob is never the output; the explanation is the
deliverable.

**Capability boundaries** (so this stops being re-litigated):
- **Era / period** — explicit, read straight from `startYear`. Yes.
- **Genre** — explicit IMDb genre labels. Yes. (A genre-evolution *mode* is a
  later brief, not this one.)
- **Movement** — never a label the engine prints. It emerges only implicitly,
  as a dense region of shared collaborators in a period (e.g. a shared
  cinematographer across several 1970s Italian films) — the engine shows the
  people and period that would constitute a movement to a human reader, it
  never asserts the movement's name.
- **Visual style / mise-en-scène / aesthetic sensibility** — out by design.
  The engine shows the *structural substrate* (who / when / what-genre); it
  never asserts that a film *has* a style. Read, don't derive — and never an
  LLM tag.

The engine (`cde/explore.py`) is pandas-free and importable with `duckdb`
alone. The API (FastAPI) and UI (Streamlit) are a separate layer with their
own `requirements-app.txt` — the engine never imports a web framework.

## CineGraph Connect + Follow (stage 3A)

The other two core actions, sharing Explore's edge-tier rule and priors
(`cde/explore.py`'s `CATEGORY_WEIGHT`/`idf`) rather than re-deriving them:

- **Connect** (`cde/connect.py`) — the strongest meaningful path between two
  films, not the shortest. Traverses only STRONG-CONNECTOR person edges
  (director/writer/cinematographer/editor/composer/producer/
  production_designer); a context edge (decade/genre) can never form a hop —
  "both 1970s" is not a path, and Connect says so honestly rather than
  falling back to one. Maximizes cumulative edge strength within a hop cap
  (default 4), so a longer chain of rare-collaborator hops beats a short one
  through someone ubiquitous. A real example (hop cap 4): *The Conformist*
  → Vittorio Storaro (cinematographer) → *Ladyhawke* → Tom Mankiewicz
  (writer) → *Delirious* → Cliff Eidelman (composer) → *Christopher
  Columbus: The Discovery* → Mario Puzo (writer) → *The Godfather*. Known
  limits, stated plainly: it's a bounded best-first search over a graph too
  large to materialize (not a provably optimal solver), and a `hop_cap` of 4
  currently takes single-digit-to-low-teens seconds against the full
  744k-film backbone — noticeable in an interactive UI, a candidate for
  further optimization rather than solved here.
- **Follow** (`cde/follow.py`) — pivot on a graph **entity**, not a fixed
  feature. `Follow(person)` returns that person's full filmography with the
  role they held on each film (the Gordon Willis / Franco Arcalli motion).
  `Follow(decade|genre)` returns films of that decade/genre scoped to the
  seed's strong-connector neighbourhood — a pivot on the graph, not a bare
  corpus filter. A film's credits are shown **craft-first**: director,
  writer, cinematographer, editor, composer, producer, production_designer
  as named departments; cast listed but secondary. The entity registry is
  extensible on purpose — `company`, `distributor`, `work` (based-on),
  `series`, `movement`, `festival`, `location` are registered stub types
  (a clear `not_implemented` response, never a crash), waiting on the
  parked Wikidata merge, not built here.

```bash
uvicorn app.api:app --reload
# GET /explore/{tconst}?n=20
# GET /connect?a={tconst}&b={tconst}&hop_cap=4
# GET /follow?entity_type=person&id={nconst}
# GET /follow?entity_type=decade&id=1970&seed={tconst}
```

## Quickstart

```bash
pip install -r requirements.txt
python -m cde.cli build                 # movies only, downloads + builds data/processed/film.duckdb
python -m cde.cli build --with-people    # also loads name.basics / title.principals
python -m cde.cli resolve my_titles.csv --title-col title --year-col year
```

Explore/Connect/Follow (need `film`, `credits`, `person` already loaded —
see `cde.people`):

```bash
pip install -r requirements.txt -r requirements-app.txt
uvicorn app.api:app --reload             # /explore, /connect, /follow -- see above
streamlit run app/streamlit_app.py       # search -> Explore results -> click a
                                          # person to Follow; a Connect tab
```

For development:

```bash
pip install -r requirements.txt -r requirements-dev.txt
flake8 cde tests
pytest -q -m "not integration"          # unit tests only (what CI runs)
pytest -q -m integration                # + integration, needs a built film.duckdb
```

## Status

V1, metadata-only. Stage 1 (IMDb backbone) complete and tested. Stage 3A now
has all three core actions — Explore, Connect, Follow — built on the IMDb
`credits`/`person` load, sharing one edge-tier rule and one set of priors.
Explore alone went through three pre-registered eval passes (50% → 60%
judged interesting, `wrong` under 7%; see `PREDICTIONS.md`,
`PREDICTIONS_tuning.md`, `eval_explore.py`). Wikidata enrichment (stage 2)
is parked on its own branch pending the reception-text stage. Plot text and
expert/critic lists are deferred.

Unit tests (what CI runs): 14 passing on `main`. Unit + integration (run
locally against the real ~429 MB `film.duckdb`, integration not run in CI):
18 passing, confirming the package reproduces the reconstruction's verified
numbers (744,866 movies, 46.2% rated, title_lookup reconciliation, resolver
correctness on known anchors). (`imdb-people` through `connect-follow` are
ahead of `main` with their own additional tests — 85 unit tests green on
`connect-follow` — not yet merged; see each branch.)

## Out of scope (next)

Wikidata CC0 enrichment (country, movement, based-on, series, collaborator
edges via SPARQL on IMDb id P345) — the merge that would promote Follow's
stubbed entity types (`company`, `distributor`, `work`, `series`,
`movement`, `festival`, `location`) to real, built implementations. Also:
an MMR/xQuAD list-level diversity reranker (Explore's crew-clique
list-redundancy failure mode — a real IR component with its own eval, not
another scalar-weight tweak); role normalization + `BASED_ON` vs
`WRITTEN_BY` edge subtyping with provenance (explanations currently show a
shared person's role on the *seed*, not on the specific candidate — an
occasionally-misleading but not incorrect simplification); entity/version
dedup (e.g. a film re-released under a different IMDb entry); corpus
definition by feature completeness rather than votes; and an optional,
clearly-labelled institutional-attention re-ranker (awards, movement
membership, national registries) with its own acknowledged prestige bias.
