# cinephile-discovery-engine

A metadata-only, graph-based cinematic discovery engine. It builds a local
backbone of film identity and structural relationships (title, year, cast,
crew, and — in later stages — country, movement, and based-on/collaborator
edges) from public metadata sources. It is not a rating recommender: nothing
here optimizes for what audiences liked.

## Stance

`imdb_rating` and `imdb_votes` are loaded as factual columns and are **never**
used as a filter or a ranker anywhere in this package. Mass-user preference
does not enter the ontology. The only place `imdb_votes` is touched at all is
the resolver's tie-break, and only as identity disambiguation (deciding which
real film a title+year pair refers to when more than one candidate matches) —
not as a value judgment about quality. That resolver path is currently
dormant: external canon lists were cut from this stage, and the planned
Wikidata enrichment (stage 2) joins on `tconst` directly rather than through
the resolver. It's retained here as tested infrastructure for when a
title/year-keyed source shows up.

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

## Quickstart

```bash
pip install -r requirements.txt
python -m cde.cli build                 # movies only, downloads + builds data/processed/film.duckdb
python -m cde.cli build --with-people    # also loads name.basics / title.principals
python -m cde.cli resolve my_titles.csv --title-col title --year-col year
```

For development:

```bash
pip install -r requirements.txt -r requirements-dev.txt
flake8 cde tests
pytest -q -m "not integration"          # unit tests only (what CI runs)
pytest -q -m integration                # + integration, needs a built film.duckdb
```

## Status

V1, metadata-only. Stage 1 (IMDb backbone) complete and tested. Wikidata
enrichment is next. Plot text and expert/critic lists are deferred.

Unit tests (what CI runs): 14 passing. Unit + integration (run locally
against the real ~429 MB `film.duckdb`, integration not run in CI): 18
passing, confirming the package reproduces the reconstruction's verified
numbers (744,866 movies, 46.2% rated, title_lookup reconciliation, resolver
correctness on known anchors).

## Out of scope (next)

Wikidata CC0 enrichment (country, movement, based-on, series, collaborator
edges via SPARQL on IMDb id P345), corpus definition by feature completeness
rather than votes, and an optional, clearly-labelled institutional-attention
re-ranker (awards, movement membership, national registries) with its own
acknowledged prestige bias.
