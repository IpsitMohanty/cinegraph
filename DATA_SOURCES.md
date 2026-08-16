# Data Sources

CineGraph is built on a single primary data source, used under non-commercial terms
and never redistributed. This file records what the project uses and the licensing
boundaries it operates within.

## Primary source

**IMDb non-commercial datasets** (https://developer.imdb.com/non-commercial-datasets/)

Used locally to build the backbone:

- `title.basics` — canonical films (title, year, runtime, genres)
- `title.akas` — title variants, for resolution
- `title.crew`, `title.principals` — the credit graph (directors, writers,
  cinematographers, editors, composers, producers, cast)
- `name.basics` — person names and birth/death years (used by the temporal gate)
- `title.ratings` — loaded but NOT used for ranking (see below)

Filtered to `titleType = movie`. The local backbone is 744,866 films with a credit
graph over ~2.2M people.

## Licensing boundary (the important part)

- IMDb's non-commercial datasets are used under IMDb's applicable personal /
  non-commercial terms. Their terms do not permit redistributing the data as a
  database.
- Therefore the raw IMDb data and the derived `film.duckdb` backbone are
  **gitignored and never committed**.
- The **public Streamlit demo does not redistribute IMDb data**. It ships only a
  small set of *precomputed derived outputs* (72 curated films: Explore/Follow/
  Connect results) as `demo/artifact.json` — analysis outputs, not the underlying
  credit tables.
- The full engine runs locally against the complete backbone; the public demo is
  intentionally a precomputed window into it.

## Signals deliberately excluded

- **Audience ratings and vote counts** (`title.ratings`) are loaded as factual
  columns but are never a ranking or filtering signal. Popularity is anti-correlated
  with the discovery target, so it is kept out of the value path by design.

## Not used

TMDB is not used in the model path (its terms restrict ML use). No user-behaviour or
rating-interaction data is used. Enrichment sources such as Wikidata are noted in the
roadmap as future work but are not part of the current v1 backbone.

## Code license

Code is MIT. This is a non-commercial portfolio project.
