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
  `genus_count`, `species_count`, `distribution`, `synonym`, `discussed_in`, `conflicts_with`,
  `crown_age`, `stem_age`, `fossil_age` (Ma, one claim per study, read from APWeb "Age." paragraphs;
  qualifiers hold the interval, all point estimates and any parse problems).
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
                                     # also writes data/reports/apweb-errata-<date>.html
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
export ANTHROPIC_API_KEY=...        # or: ant auth login
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

### OneZoom viewer (prototype)

The **OneZoom** page draws the current source with [OneZoom](https://www.onezoom.org)'s
zoomable tree viewer, fed from this store:

```bash
frontend/scripts/fetch-onezoom.sh    # OneZoom's viewer bundle + UI strings → frontend/public/oz-viewer/vendor/
cd frontend && npm run build
```

- `apw/onezoom.py` turns a source's `child_of` tree into OneZoom's formats: a
  brackets-only Newick string (`/api/oz/<source>/completetree.js`, plus its cut map)
  and a `node_details` endpoint for names and species counts. The concept id stands
  in for OneZoom's Open Tree id.
- `frontend/public/oz-viewer/` runs the viewer in an iframe, because OneZoom keeps its
  state in globals. The React page talks to it with postMessage: fly to a taxon, switch
  view, and select a clicked name.
- Limits: groups that are their parent's only child are drawn as one node
  ("Vitales / Vitaceae"); no images, common names, IUCN status or dates.
- **Licence.** The viewer is © OneZoom CIO under the
  [OneZoom Non-Profit Source-Available Licence](https://github.com/OneZoom/OZtree/blob/main/license.txt):
  non-commercial use for science and education only, no sponsorable trees, and changes
  made public must go back to OneZoom. It is not committed here. Talk to OneZoom
  (mail@onezoom.org) before any public deployment.

## Deployment (Fly.io)

The app ships as a single Docker image: the Python backend serves both the API and
the built React frontend. The SQLite database is baked in at build time.

**Prerequisites:** `data/apw.sqlite` must exist locally (run `apw ingest all` first).
Install [flyctl](https://fly.io/docs/hands-on/install-flyctl/).

```bash
# Build and test locally
docker build -t apw-app .
docker run -p 8080:8080 \
  -e APW_PASSWORD=secret \
  -e APW_ASK_MODE=server \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  apw-app

# Deploy to Fly.io (free tier: 1 shared-cpu VM, no persistent volume needed)
fly auth login
fly launch --no-deploy        # choose a unique app name; updates app = "..." in fly.toml
fly secrets set APW_PASSWORD=secret ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

**Re-deploy after a new ingest:** `fly deploy` rebuilds the image with the updated DB.

## Deployment (Apache reverse proxy, sub-path)

To run behind an existing Apache at `https://<host>/apweb/` (files in `deploy/`):

```bash
cd frontend && npm ci && scripts/fetch-onezoom.sh && npm run build -- --base=/apweb/
cd ../backend && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/apw ingest all

sudo install -d -m 755 /etc/apweb
sudo install -m 600 deploy/apweb.env.example /etc/apweb/apweb.env   # then set the password and key
sudo cp deploy/apweb.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now apweb                                    # uvicorn on 127.0.0.1:8010
# paste deploy/apache-apweb.conf into the host's <VirtualHost *:443>, then:
sudo apache2ctl configtest && sudo systemctl reload apache2
```

Apache strips `/apweb` before proxying. The frontend gets the prefix at build time
(`--base`, read as `import.meta.env.BASE_URL`), and uvicorn's `--root-path` gives it to
the API docs. **After a new ingest:** `sudo systemctl restart apweb`. **After frontend
changes:** rebuild with the same `--base`; no restart needed. Details, updating and
troubleshooting: [`docs/deployment-apache.md`](docs/deployment-apache.md).

### Password protection (`APW_PASSWORD`)

Set `APW_PASSWORD` to require HTTP Basic Auth on every request. The browser prompts
once and remembers the password. Leave it unset for open access (the default for local
development and future public releases).

### Ask feature in server mode

Set `APW_ASK_MODE=server` and `ANTHROPIC_API_KEY` to make the server answer questions
using its own key — the API-key panel is hidden from the UI. Use this for private
or collaborator deployments where the server pays. Keep it behind `APW_PASSWORD` or
equivalent auth, as there is no per-user rate limiting.

## Asking questions (Claude)

The **Ask** page, plus the question box on the home page, answers natural-language
questions. Claude queries the store with the same tools the MCP server exposes, then
writes an answer that links every taxon and reference it used. The page shows each
lookup as it happens.

**Who pays.** `APW_ASK_MODE` decides whose Anthropic credentials are used:

| mode | behavior | use for |
|---|---|---|
| `user` (default) | each visitor enters their own API key on the Ask page; the server's credentials are never used | public deployment |
| `server` | the server's `ANTHROPIC_API_KEY` (or `ant auth login` profile) | a private or lab-only deployment |
| `both` | a visitor's key if they give one, otherwise the server's | trusted users plus optional BYOK |

```bash
apw serve                                              # public: visitors bring their own keys
APW_ASK_MODE=server ANTHROPIC_API_KEY=... apw serve    # private: the server pays
```

**Visitor keys:**
- The browser keeps the key for the tab (`sessionStorage`), or on the device if the
  visitor opts in.
- It is sent in an `X-Anthropic-Api-Key` header with each question and used for that
  request only. The server never stores or logs it, and scrubs it from any error text.
- Each conversation is bound to a keyed hash of the key that started it, so a different
  key can't continue it.
- Visitors can choose Opus 5.5 or Sonnet 5.5 (about half the cost), configurable with
  `APW_ASK_MODELS`.
- Serve over HTTPS.

- Code: `backend/src/apw/ask.py` (streaming tool-use loop), `apw/tools.py` (tool
  definitions shared with the MCP server), `POST /api/ask` (Server-Sent Events).
- Model settings: `claude-opus-5-5` with adaptive thinking at `medium` effort. Override
  with `APW_MODEL` / `APW_EFFORT`. Server-side refusal fallback is on
  (`fallbacks: "default"`).
- Conversations are kept in server memory (follow-up questions work) and are lost on
  restart. `APW_ASK_DISABLED=1` turns the feature off.
- **Cost:** each question is several model calls. In `server` or `both` mode these are
  billed to the server's key, and there is no per-user rate limiting, so keep those modes
  behind authentication.

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

Each order page carries its own copy of the backbone, revised at different times, so
pages can disagree: `cardiopteridalesweb.htm` has `[[Icacinales, Garryales, Aquifoliales]
+ …]`, while six other lamiid pages have `[Garryales, Aquifoliales, [Icacinales + …]]`.
Before voting, every bracket clade is treated as a set of header-backed members and the
clades are checked for compatibility (two clades must nest or be disjoint). Clades are
accepted greedily, ranked by:
1. the number of pages stating them;
2. fewest clashes with other clades;
3. the most recent page date (from the manifest's `Last-Modified`).

A clade that partly overlaps an accepted one is left out of the hierarchy and recorded
as `conflicts_with` the accepted clade, with the pages stating each. Votes for it are
passed up to wherever it was placed. The accepted clades then decide each member's
parent. Members are taxa with a header, or single Latin names a page uses in two or
more of its headers (genera, fern subfamilies). Members that a page does not itself
treat ("etc", misspellings, a subfamily from another order) count as wildcards rather
than contradictions. Copies of one clade, whether misspelt or with the same members
nested differently, are merged into one node. Clades with the same members that APWeb
writes as a grade (`[Senegalia ii + R]`, `[Senegalia iii + R]`, …) nest in the order
their `anc` links give.

Rank-consistency rules and cycle breaking follow the vote. Where the votes for a parent
still disagree (e.g. `[[Fabales + Fagales] [Cucurbitales + Rosales]]` on one page and
`[Rosales [Cucurbitales + Fagales]]` on another), the alternatives are kept in the claim's
qualifiers and listed by `apw conflicts`.

Vernacular names ("Spermacoceae alliance", "Old World Clade") are kept whole rather than
split into a name and an authority. When one is given as an alternative name, it merges
nodes only on its own page, because several families reuse names like "Old World clade".

Each APWeb ingest also writes an errata report, `data/reports/apweb-errata-<snapshot>.html`
(`sources/apweb/errata.py`), meant for the site's authors. It lists:
- clades left out because other pages, or the same page, contradict them;
- bracket names for a family or below that only another order's page treats;
- lines marked as alternatives ("if this clade exists", in its various wordings);
- names spelled two ways on a page;
- slips the parse repairs, so the authors can fix them: names run together without a
  comma, and misspelt bracket members matched to the right taxon;
- header lines with unbalanced brackets;
- `anc` attributes naming anchors that exist nowhere;
- links to order pages the crawl could not fetch.

Each item quotes the header line and links to its page and anchor.

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
- **M3 — Natural-language queries** (done): Claude with tool use over the query layer,
  with streamed, linked answers. Possible next steps: embeddings for semantic retrieval
  over characters and references, saved conversations, and an eval set of real questions.
- **M4+ — More sources and synthesis.**
  - Open Tree of Life, WFO/IPNI identifiers, CrossRef/OpenAlex DOIs and abstracts, trait
    databases, published trees.
  - Conflict and support summaries; how hypotheses change across APWeb snapshots.
