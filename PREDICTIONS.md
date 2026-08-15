# CineGraph Explore -- pre-registered predictions

Written and committed **before** `eval_explore.py` is run over the full seed
set and before any of that output has been looked at. This is the
methodology: predictions locked first, results reported honestly against
them afterward, negatives included.

**Disclosure, in the interest of the same honesty:** during development, two
ad hoc, single-film sanity calls to `explore()` were made against the real
`film.duckdb` -- both on *The Conformist* (tt0065571) -- to confirm the
engine wasn't structurally broken (that a real seed resolves correctly and
returns real, sane collaborator names rather than garbage). Those calls
showed Vittorio Storaro / Bernardo Bertolucci / Franco Arcalli connections,
which is what a working engine should produce. That was a code-correctness
check, not an exploration of the evaluation seed set, and it did not shape
the predictions below -- but it's disclosed rather than left implicit,
because pre-registration only means something if the exceptions are named.
The 12-seed `eval_explore.py` table has not been generated or viewed as of
this writing.

## Predictions

**(a) Cinematographer/composer/editor-driven results will be judged more
interesting than cast-driven results.**
Rationale: `CATEGORY_WEIGHT` puts cinematographer/composer/editor at the top
(1.0 / 0.9 / 0.85) and actor/actress at the bottom (0.2), and these are also
generally lower-degree (less ubiquitous) roles than acting, so `idf` should
compound the effect rather than fight it. If this holds, a below-the-line
craft edge should dominate visible top-N connections far more often than a
shared-cast edge does.

**(b) idf weighting will surface some non-obvious (low-fame) films in most
seeds' top-10.**
Rationale: `idf(degree)` actively rewards rare collaborators over prolific
ones, so a seed's obscure early collaborator (a cinematographer or editor
who only has a handful of credits) should be able to out-rank a famous but
ubiquitous co-star. If the top-10 for most seeds is just "other famous films
by the same star," this prediction fails.

**(c) Same-director results, when present, will read as more obvious than
cross-collaborator ones.**
Rationale: this is exactly why `novelty` exists -- a same-director
connection is the least surprising kind of "this is also by X," compared to
a shared cinematographer/composer connecting two different directors'
films. Predicted to hold with `novelty=false` (default); the `novelty=true`
damping exists specifically because this prediction is expected to be true
by default.

**(d) Genre/decade-only-flavored connections will be judged least
interesting.**
Precisely: candidates whose score is dominated by the `DECADE_BONUS`/
`GENRE_BONUS` terms rather than by a strong specific-person edge (i.e. the
bonus is doing most of the ranking work, not just riding along on top of a
real collaborator edge) are predicted to be judged the weakest results in
the set -- the exact failure mode the weighting design (small bonus, real
edges dominant) is meant to suppress. If these still get judged
"interesting," the bonus constants are probably too large relative to the
weak end of `CATEGORY_WEIGHT` (ubiquitous actor/actress edges) and should be
tuned down.

## What would falsify each prediction

- (a): cast-driven connections rated interesting as often as (or more often
  than) craft-driven ones.
- (b): top-10 lists dominated by famous/high-degree people's other famous
  films, with obscure collaborators never surfacing near the top.
- (c): same-director results rated just as interesting as cross-collaborator
  ones -- would suggest `novelty` isn't solving a real problem, or that
  familiarity isn't actually a liability for this product.
- (d): genre/decade-dominated candidates rated interesting -- would suggest
  the bonus constants are miscalibrated (too large) relative to weak
  specific-person edges, or that period/genre affinity matters more to a
  cinephile audience than this design assumes.

## Judging protocol

`eval_explore.py` produces `eval/explore_eval.md`: for each of ~12 seed
films (spanning eras and countries, resolved by title/year through
`resolve_one_title()` -- not hand-typed tconsts, to avoid exactly the kind
of ID mixup a lookup step exists to prevent), the seed and its top-10
Explore results with full explanations, plus an empty `judgment` column for
a manual `interesting` / `trivial` / `wrong` pass. The harness produces the
judgeable artifact; the labeling is a separate, human pass over that file --
not automated, not inferred by the harness or by whichever model runs it.
