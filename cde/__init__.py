"""cde - cinephile discovery engine.

Stage 1: a local, metadata-only IMDb backbone (tconst spine) plus a
title/year resolver for mapping arbitrary title,year data onto it.
"""

__all__ = ["config", "imdb", "resolve", "cli"]
