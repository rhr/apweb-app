# Timetree from APWeb clade ages: status

Goal: a dated tree of the APWeb hierarchy, built from the age estimates the site summarises under
each clade's "Age." heading. Users choose how one age is picked for each node from the competing
estimates.

The work is planned in three steps:

1. Store the age estimates as claims and report statements that could not be parsed. **Done.**
2. Choose one age per node by a configurable rule, then make the ages consistent with the tree. **Not started.**
3. A Timetree page that draws the dated tree. **Not started.**

Status as of 2026-11-14 (commit `3c33272`). Numbers are from the 2026-11-14 snapshot.

## Step 1: ages stored as claims (done)

### What exists

| Piece | Where |
|---|---|
| Parser for "Age." paragraphs | `backend/src/apw/sources/apweb/ages.py` |
| Age text collected per header | `pages.py` (`Header.age_texts`) |
| Claims written during ingest | `ingest.py`, the clade-ages block before the commit |
| Predicates `crown_age`, `stem_age`, `fossil_age` | `db.py` (`PREDICATES`) |
| Errata section 8, "Age statements" | `errata.py` |
| API: `ages` field on `/api/taxa/{ref}` | `service.py` (`ages()`) |
| Age chart on the taxon page | `frontend/src/components/AgeChart.tsx` |
| Tests | `backend/tests/test_apweb_parsing.py` (the `test_age_*` tests) |

### The data

- Each claim holds one study's estimate for one clade. The claim's value is a representative age:
  the median of the point estimates, or the midpoint of a range.
- Qualifiers keep the rest:
  - `points` and `form`: point, points, range or alternatives
  - `older` and `younger`: the interval
  - `approx`: the age was written with "ca"
  - `methods`: method names in the sentence, such as BEAST or r8s
  - `kind_explicit`: the text said "crown" or "stem" explicitly
  - `subject` and `subject_resolved`: the clade the sentence names, and whether the site has a
    header for it
  - `stated_under`: the header the statement appears under
  - `problems`: any parse problems
  - `text`: the age expression only
- The study is attached as a citation and resolved to a bibliography reference where possible.
- Copyright: only numbers, the age expression and the page locator are stored, never the sentence
  (content © P. F. Stevens).

| | Claims | Clades |
|---|---|---|
| `crown_age` | 6,871 | 2,209 |
| `stem_age` | 156 | 93 |
| `fossil_age` | 371 | 267 |

- 6,405 age claims have a citation resolved to a reference.
- Crown ages per clade:

  | Estimates | Clades |
  |---|---|
  | 1 | 836 |
  | 2–3 | 789 |
  | 4–10 | 500 |
  | More than 10 | 84 (the most is 42) |

- Coverage by rank: 351 of 500 families and 60 of 104 orders have at least one crown age. The
  tree has 4,285 concepts in all, so most nodes have no age, especially unnamed clades, tribes and
  subfamilies.

### Known limits

These carry into step 2.

- Citation binding was right in about 93–97% of hand-checked samples. "The latter" references bind
  to the wrong study.
- A few durations ("within 10 Ma") are read as ages.
- The split between fossil and estimate is decided by keywords.
- Some statements are attributed to the wrong header. For example, about 400 Ma ages appear under
  Gymnogynoideae.
- Errata counts:

  | Problem | Count |
  |---|---|
  | No citation | 186 |
  | Interval doesn't contain the estimate | 33 |
  | Number with "Ma" not parsed | 48 |
  | "My" or "million years" ambiguous | 54 |
  | Older than 500 Ma | 20 |
  | Named clade has no header | 55 |

  Claims with `problems` should be excluded from automatic selection or given low weight.
- The live database (`data/apw.sqlite`) needs `apw ingest apweb` before ages appear in the app or
  the Docker image. The numbers above come from a scratch copy.

## Step 2: one age per node (to do)

Input: the `apweb@<snapshot>` tree (`child_of` claims, loaded by `query.Tree`) and the age claims.
Output: one age per node, recording where it came from.

### 2a. Selection rules

Pick one crown age per node from its `crown_age` claims. Possible rules:

| Rule | Picks |
|---|---|
| `study` | One named study (e.g. Magallón et al. 2015, or Ramírez-Barahona et al. 2020) wherever it gives an age. Falls back to another rule elsewhere. |
| `recent` | The estimate from the most recent publication |
| `median` | The median of all estimates |
| `oldest` / `youngest` | The maximum or minimum estimate, for a sensitivity range |
| `manual` | Picks a user has made per node, stored as overrides |

Decisions needed:

- **Filter before the rule.** Should the rule drop claims that have `problems`, have no resolved
  reference, are `approx` only, or were moved by `subject`? Proposed default: drop claims with
  `problems`, and keep the rest.
- **Ages with more than one value.** For `alternatives` and multi-point ages, should the rule use
  the claim's value (the median of its points), or treat each point as a separate candidate?
- **Ranking studies for `study` and for fallback.** Ranking by number of ages given would favour
  the big studies (Magallón, Ramírez-Barahona, Tank, Bell, Wikström). A user-ordered list is more
  flexible.
- **Where manual picks are stored.** Proposal: a small table separate from the claims, so
  re-ingesting does not wipe them. Picks are keyed by the clade's label or members, not its id,
  because ids change between snapshots.

### 2b. Make the ages consistent with the tree

Ages picked independently will contradict the tree: a child older than its parent, or a stem age
older than the parent's crown. Planned handling:

- **Lower bounds.** A node's age must be at least:
  - each child's crown age;
  - each child's `stem_age`, since a stem age dates the split at the parent;
  - any `fossil_age` within the node, since fossils are minimum ages.
- **Fixing a violation.** Options:
  - raise the parent to just above its oldest child;
  - lower the child;
  - drop the conflicting estimate and use the next candidate.

  The fix depends on the rule. Proposed default: raise the parent, and flag the node as adjusted.
- **Root age.** Needed for interpolation. Take it from the crown angiosperm estimates, or let the
  user set it.

### 2c. Fill in undated nodes

Most nodes have no estimate. Their ages are interpolated between the nearest dated ancestor and
descendants.

- Methods: equal splits along the path (BLADJ-style), or splits weighted by number of descendants.
- Tips with no age below them need a tip age. Tips are present-day lineages, so the tip age is 0.
  Tips are families, genera or tribes, depending on how far the tree is expanded.
- Every interpolated age is marked as invented, with the method used, so the UI can show it
  differently.

### 2d. Where it runs

- Proposal: compute on request in `service.py` (or a new `timetree.py`), cached by tree, rule and
  parameters. The tree is about 4,300 nodes, so this is cheap. Nothing derived is written to the
  claims store, which keeps it facts only.
- Planned API: `GET /api/timetree?source=apweb&rule=median&fallback=recent&adjust=raise&root=…`.
  It returns, per node:
  - the age;
  - the status: `estimate`, `adjusted` or `interpolated`;
  - the chosen claim's id and citation;
  - the number of candidates;
  - the lower bound that applied.
- Tests: small synthetic trees covering each rule, an inconsistent child, a stem age bound, and
  interpolation.

## Step 3: Timetree page (to do)

- **Layout.**
  - A rectangular tree on a time axis in Ma, with the geological timescale (periods and epochs)
    as a band under the axis.
  - Branch lengths come from the step 2 ages.
  - The existing `PhyloTree.tsx` is a lazily loaded cladogram. It is a starting point, but a
    timetree needs the whole subtree's ages up front.
- **Controls.**
  - Rule drop-down, with fallback and adjustment options.
  - Collapse to orders or families.
  - Pick the root clade.
  - Show or hide interpolated ages.
- **Encoding.**
  - Dated, adjusted and interpolated nodes are drawn differently: solid, outlined, or dashed
    branches.
  - Optionally, the spread of estimates is shown as a bar at each node.
  - Clicking a node opens its taxon page, which already has the age chart.
- **OneZoom.** The OneZoom prototype already requests `/api/oz/{source}/dates.js`. It currently
  returns `var tree_date = {};`. Filling it from the same timetree service would date the OneZoom
  view too, but OneZoom's licensing is still unresolved.
- **Phone width.** Pan and zoom, and collapse to orders by default.

## Suggested order of work

1. Run `apw ingest apweb` on the live database. Review errata section 8, and fix or exclude the
   worst offenders, such as the Gymnogynoideae ages and the durations.
2. Settle the step 2a decisions, especially the default filter and the study ranking.
3. Build `timetree.py` (selection, bounds, interpolation) with tests, and add `/api/timetree`.
4. Build the Timetree page.
5. Optionally, wire up `dates.js` for OneZoom.
