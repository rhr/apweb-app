# Angiosperm phylogeny knowledge store

A data-driven engine for synthesizing knowledge of angiosperm phylogeny: taxa, clades,
relationships, characters and the primary literature behind them. It is drawn from
multiple sources, keeps their provenance, and is meant to be queried by people and by AI.

The first source is P. F. Stevens' [Angiosperm Phylogeny Website](https://www.mobot.org/mobot/research/apweb/)
(APWeb). The second is the APG I–IV classifications.

## Design

Every fact is a **claim** made by a **source** about a taxon **concept**, which is a name
*sensu* that source. Claims can cite **references**:

```
source ──< concept ──< claim >── concept      (child_of: Vitaceae → Vitales)
                         │
                         ├── value             (has_character: "raphide bundles +", {apomorphy: true})
                         └──< citation >── reference   (discussed_in "Age": Wen et al. 2013)
concept >──< alignment >──< concept           (APWeb "Vitaceae" same_name APG IV "Vitaceae";
                                               APWeb clade congruent with an unnamed APG IV node)
```

- **Sources never overwrite each other.** APWeb snapshots (`apweb@2026-10-29`) and APG
  versions are separate sources, so comparing them is a query, not a merge.
- **Name ≠ concept.** "Saxifragales" in APG II and in APG IV are different concepts. They
  are linked by `alignment` rows: `same_name`, or `congruent` when both contain the same
  set of orders.
- **Predicates** (`predicate` table): `child_of`, `has_character`, `family_count`,
  `genus_count`, `species_count`, `distribution`, `synonym`, `discussed_in`.
- **Full-text search** (SQLite FTS5) covers labels, characters, topics and references.

Schema: [`backend/src/apw/schema.sql`](backend/src/apw/schema.sql).

### Content policy: facts, not prose

APWeb's text is © P. F. Stevens. The store holds extracted facts and deep links back to
APWeb: names, placements, short character statements with apomorphy flags, counts,
distributions, synonyms, and which works are cited on which topic for which taxon. It
never holds APWeb's discussion prose. Raw pages are cached locally under `data/raw/`
(gitignored) and are not redistributed. Before any public release, confirm the policy
with Stevens / MBG.

## Quick start

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

.venv/bin/apw crawl apweb            # ~80 pages, 2 s apart → data/raw/apweb/<date>/
export APW_CONTACT=you@example.org   # Wikimedia requires contact info in the User-Agent
.venv/bin/apw crawl apg              # APG I–IV wikitext, pinned to revision ids
.venv/bin/apw ingest all             # parse → data/apw.sqlite, then align sources
.venv/bin/pytest -q
```

## Using it

```bash
apw show Vitaceae                    # placement, counts, apomorphies, literature by topic
apw lineage Poaceae                  # path to the root
apw children Vitaceae --depth 3      # subtree
apw chars Vitaceae --apo             # possible apomorphies only
apw refs Vitaceae --topic Age        # works cited on Vitaceae ages
apw cites "Magallon et al. 2015"     # which taxa cite a work, and on what topic
apw search "raphides twinned"        # full-text
apw compare apg3 apg4                # families that moved / were added or sunk
apw compare apg4 apweb               # where APWeb departs from APG IV
apw clades apweb apg4                # APG IV clades with/without a congruent APWeb clade
apw conflicts                        # placements where APWeb pages disagree with each other
apw stats                            # data-quality summary
apw -s apg2 show Saxifragales        # any command against another source
```

The same functions are available from Python in `apw.query`; `apw.service` wraps them
as JSON for the web API and the MCP server.

## Web app

```bash
cd frontend && npm install && npm run build    # → frontend/dist
cd ../backend && .venv/bin/apw serve           # http://127.0.0.1:8000 (API docs: /api/docs)
```

`apw serve` serves both the API and the built app. For frontend development, run
`apw serve` and `npm run dev` in `frontend/` together; Vite proxies `/api` to port 8000.

- **Taxon pages**: lineage, counts, distribution, synonymy, characterization (possible
  apomorphies in bold), literature by topic (with DOIs), the same taxon across APWeb and
  APG I–IV, and a note where APWeb's pages place the group differently.
- **Phylogeny viewer**: a zoomable cladogram that loads children as you expand them.
  "Named groups only" hides the bracketed informal clades. Click a name for a summary
  panel; re-root anywhere.
- **Compare**: family (or order) placements between any two sources, and which clades
  of one have a counterpart with the same orders in the other.
- **Conflicts**: groups that different APWeb pages place differently.
- **Search**: name autocomplete, plus full-text search over statements and references.

## MCP server (for AI clients)

```bash
claude mcp add apw -- /path/to/apweb-app/backend/.venv/bin/apw mcp
```

Tools: `list_sources`, `search`, `get_taxon`, `get_subtree`, `get_literature`,
`find_references`, `get_reference`, `compare_classifications`, `clade_agreement`,
`list_conflicts`. Each returns structured JSON with source keys and APWeb/DOI links, so
answers can cite their sources.

## How the APWeb parse works

Order pages are runs of `<p>` elements. `sources/apweb/pages.py` classifies each one as:
- a header: an anchored taxon (`<a name=X anc=Parent>`), or a backbone line such as
  `[CERATOPHYLLALES + EUDICOTS]: …`
- a characterization: `;`-separated characters, with `<b class="apo">` marking possible
  apomorphies
- a topic paragraph: Age, Evolution/…, Phylogeny…, mined for author–year citations
- stats or synonymy

`sources/apweb/ingest.py` merges the headers from all pages into one node per clade. The
backbone is repeated on every page, so merging uses union-find over names and bracket
member sets. Each node's parent is then chosen by weighted vote:

| evidence | weight |
|---|---|
| nesting in bracket expressions, e.g. `[Cornaceae [Nyssaceae [Grubbiaceae + Curtisiaceae]]]` | 3 |
| the `anc` attribute, resolved on the page | 2 |
| … resolved elsewhere, or fuzzily (APWeb's `anc` values have typos) | 1.5 / 1 |
| document structure | 1 |

Rank-consistency rules and cycle breaking follow the vote. Where APWeb's pages disagree
with each other (e.g. `[[Fabales + Fagales] [Cucurbitales + Rosales]]` on one page and
`[Rosales [Cucurbitales + Fagales]]` on another), the alternatives are kept in the claim's
qualifiers and listed by `apw conflicts`.

In-text citations ("Wen et al. (2013)", "(Magallón & Castillo 2009; Endress 2010c)") are
resolved against APWeb's bibliography using:
- the author count
- the year as cited (`[= 2021]`)
- initials
- for homonyms, overlap between the work's title and the citing taxon's lineage

## Current state (snapshot 2026-10-29)

| | |
|---|---|
| APWeb concepts | ~4,300 (104 orders, ~500 families, ~470 subfamilies, ~1,150 tribes, ~1,900 clades) |
| character statements | ~28,000 |
| references | 28,424 |
| in-text citations | ~54,000, **89% resolved** to a bibliography entry (2,100 by tie-breaking, flagged with confidence < 1) |
| APG IV check | 411/416 families found in APWeb; 406 in the same order. The other 10 are deliberate APWeb treatments (e.g. Huales, Cardiopteridales; Frankeniaceae → Frankenieae in Tamaricaceae), except Dasypogonaceae, which has no APWeb page (dead link) |
| APG III / IV | 59 / 64 orders and 413 / 416 families, matching the published counts |

### Known limitations

- APWeb marks stem/total groups with †. These are currently merged with the crown
  group of the same name; the flag is parsed but not yet modelled.
- About 5% of in-text citations are ambiguous (e.g. two Wang et al. 2009 papers with no
  context to choose between them), and about 6% name works missing from APWeb's
  bibliography. Unresolved citations are kept, with candidate counts.
- Taxa below tribe (genera, sections) are only captured where APWeb gives them headers.
- The APG transcriptions come from English Wikipedia (CC BY-SA). APG II there lists only
  unplaced families. Ingested data is pinned to a Wikipedia revision once fetched with
  `APW_CONTACT` set; until then, provenance says "revision unknown".

## Roadmap

- **M2 — API and browser** (done): FastAPI, a React/TypeScript app with a D3 tree
  viewer and source comparison, and an MCP server.
- **M3 — Natural-language queries.** Claude with tool use over the query layer, plus
  embeddings over claims and references for retrieval; answers cite claims and sources.
- **M4+ — More sources and synthesis.**
  - Open Tree of Life, WFO/IPNI identifiers, CrossRef/OpenAlex DOIs and abstracts, trait
    databases, published trees.
  - Conflict and support summaries; how hypotheses change across APWeb snapshots.
