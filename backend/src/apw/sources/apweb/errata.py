"""Errata report: errors, contradictions and ambiguities found in the APWeb pages.

Built at the end of each ingest from the same parse, and written as a standalone HTML page
(<data>/reports/apweb-errata-<snapshot>.html) that can be passed on to the site's authors.
Every item quotes the header line as written and links to the page and anchor.

Sections:
  backbone/within  clades left out of the hierarchy because better supported clades
                   contradict them (Assembler.conflicts), split into those that differ
                   between pages and those contradicted on their own page; within also
                   lists brackets naming a family or below treated on another order's page
  alternatives     header lines marked as hypotheses ('- if this clade exists', '[if a
                   clade]', 'or - Author (year) -'), whose wording varies
  spellings        one name written two ways on a page, a header unlike its anchor, or a
                   misspelt member the ingest matched to the right taxon
  run-on names     two names with no comma between them ('Ixorodae Airospermeae'), which
                   the parser splits (pages.RUN_ON)
  brackets         header lines whose [ and ] do not balance
  anc              anc attributes naming an anchor that exists nowhere on the site
  links            order pages linked from other pages that the crawl could not fetch
"""

from __future__ import annotations

import difflib
import html as htmllib
import re
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Comment

from ... import config
from . import crawl, pages

ALT_MARKER = re.compile(r"(?i)\bif (?:this|the|a)\b[^:]{0,30}?\b(?:clade|node|exists?)\b|\bor - ")
SUFFIXES = ("oideae", "odeae", "aceae", "ineae", "inae", "odae", "idae", "anae", "ales", "eae")
WILDCARD = re.compile(r"(?i)\betc\b|\bet al\b| = ")
STRAY = re.compile(r"[\[\]<>]")


@dataclass
class Quote:
    page: str
    anchor: str | None
    text: str


@dataclass
class Conflict:
    rejected: str
    accepted: str
    pages: list[str]                 # pages stating the rejected clade
    accepted_pages: list[str]
    quotes: list[Quote]              # the rejected clade as written
    rival: list[Quote]               # the accepted clade as written


@dataclass
class Errata:
    snapshot: str
    n_pages: int
    backbone: list[Conflict] = field(default_factory=list)
    within: list[Conflict] = field(default_factory=list)
    alternatives: list[Quote] = field(default_factory=list)
    foreign: list[tuple[Quote, str, list[str]]] = field(default_factory=list)
    run_on: list[tuple[Quote, str]] = field(default_factory=list)
    spellings: list[tuple[str, str | None, str, int, str, int, str]] = field(default_factory=list)
    brackets: list[tuple[list[str], str | None, str]] = field(default_factory=list)
    anc: list[tuple[str, str | None, str, str, str | None]] = field(default_factory=list)
    stray_anchors: list[tuple[str, str]] = field(default_factory=list)
    dead_links: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[list[str]] = field(default_factory=list)


def _suffix(w: str) -> str | None:
    return next((s for s in SUFFIXES if w.endswith(s)), None)


def _edits(a: str, b: str) -> int:
    """Levenshtein distance."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _fold(w: str) -> str:
    return unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode().lower()


def _variant(a: str, b: str) -> bool:
    """Two spellings of one name: same start and rank ending, one edit apart (two in a
    long name). Chioneae and Chironieae are different tribes."""
    a, b = _fold(a), _fold(b)
    if a == b or a[:3] != b[:3] or _suffix(a) is None or _suffix(a) != _suffix(b):
        return False
    return _edits(a, b) <= (2 if min(len(a), len(b)) >= 11 else 1)


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    return re.sub(r"\s+", " ", soup.get_text(" "))


def _anchors(html: str) -> set[str]:
    return {a or b or c for a, b, c in re.findall(
        r"""(?i)<a\b[^>]*?\bname\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", html)}


def build(snapshot: str, manifest: dict, htmls: dict[str, str], asm) -> Errata:
    """`htmls`: order page -> HTML (one per distinct page); `asm`: the ingest's Assembler."""
    from .ingest import LEVEL          # ingest imports this module
    page_headers = asm.page_headers
    out = Errata(snapshot, len(page_headers))

    # -- conflicting clades, quoted where they are written
    where: dict[str, list[tuple[bool, pages.Header]]] = defaultdict(list)
    for hs in page_headers.values():
        for h in hs:
            where[asm.node_of(h).key].append((True, h))
            for g in h.groups:
                for sub in g.groups():
                    k = asm.node_for_key(asm._group_key(sub)).key
                    where[k].append((sub is g, h))

    def quotes(key: str, limit: int = 3) -> list[Quote]:
        seen, qs = set(), []
        for exact, h in sorted(where[key], key=lambda x: (not x[0], x[1].page, x[1].seq)):
            if h.page in seen:
                continue
            seen.add(h.page)
            qs.append(Quote(h.page, h.anchor, h.text))
        return qs[:limit]

    for key, other in sorted(asm.conflicts.items(), key=lambda kv: asm.nodes[kv[0]].label):
        c = Conflict(asm.nodes[key].label, asm.nodes[other].label, sorted(asm.support[key]),
                     sorted(asm.support[other]), quotes(key), quotes(other))
        if c.quotes and all(ALT_MARKER.search(q.text) for q in c.quotes):
            continue                       # a marked alternative; listed under alternatives
        if WILDCARD.search(c.rejected) or WILDCARD.search(c.accepted):
            continue                       # 'etc' members make the overlap uncertain
        (out.within if c.pages == c.accepted_pages else out.backbone).append(c)
    out.backbone.sort(key=lambda c: -len(c.accepted_pages))

    # -- a bracket naming a family or below that only another order's page treats
    # (Apocynaceae's APSA clade lists Aristolochioideae, of Piperales)
    for p, hs in page_headers.items():
        own = {asm.node_of(h).key for h in hs}
        for h in hs:
            for a in sorted(h.atoms):
                n = asm.nodes.get(asm.uf.find(asm.atom_key(a)))
                if n and n.headers and n.key not in own and LEVEL.get(n.rank, 0) >= 2 \
                        and all(x.page != p for x in n.headers):
                    out.foreign.append((Quote(p, h.anchor, h.text), pages.display_name(a),
                                        sorted({x.page for x in n.headers})))

    # -- slips the parse works around: names run together, misspelt members of a clade
    for p, hs in page_headers.items():
        for h in hs:
            for tok in re.split(r"[\[\]+,]", h.text):
                name = pages.clean_atom(tok)
                if pages.RUN_ON.fullmatch(name):
                    out.run_on.append((Quote(p, h.anchor, h.text), name))

    # -- alternatives as marked on the page
    for p, hs in page_headers.items():
        for h in hs:
            if ALT_MARKER.search(h.text):
                out.alternatives.append(Quote(p, h.anchor, h.text))

    # -- spellings: two forms of one name on a page (same rank ending, nearly the same)
    for p, hs in page_headers.items():
        text = _visible_text(htmls[p])
        words = Counter()
        for h in hs:
            for w in [*h.names[:1], *(pages.display_name(a) for a in h.atoms)]:
                if re.fullmatch(r"[A-Z][a-zë]{4,}", w):
                    words[w] += 1
        forms = sorted(words)
        done = set()
        for w in forms:
            for m in difflib.get_close_matches(w, forms, n=4, cutoff=0.8):
                pair = tuple(sorted((w, m)))
                if pair in done or not _variant(w, m):
                    continue
                done.add(pair)
                a, b = pair
                na = len(re.findall(rf"\b{a}\b", text, re.I))
                nb = len(re.findall(rf"\b{b}\b", text, re.I))
                if na and nb:
                    rare, common = (a, b) if (na, a) <= (nb, b) else (b, a)
                    anchor = next((h.anchor for h in hs if rare in h.text and h.anchor), None)
                    out.spellings.append((p, anchor, rare, min(na, nb), common, max(na, nb),
                                          "two spellings on the page"))
        for h in hs:
            n, a = (h.names[0] if h.names else ""), h.anchor or ""
            if re.fullmatch(r"[A-Z][a-zë]{4,}", n) and re.fullmatch(r"[A-Z][a-z]{4,}", a) \
                    and _variant(n, a):
                out.spellings.append((p, h.anchor, n, 0, a, 0, "header and its anchor differ"))
    listed = {(r[0], frozenset((r[2], r[4]))) for r in out.spellings}
    for p, hs in page_headers.items():
        for h in hs:
            for a in sorted(h.atoms):
                k = asm.uf.find(asm.atom_key(a)) if asm.atom_key(a) not in asm.misspellings \
                    else asm.atom_key(a)
                right = asm.misspellings.get(k)
                if not right or right not in asm.nodes:
                    continue
                w, r = pages.display_name(a), asm.nodes[right].label
                if w.lower().startswith(r.lower() + " "):
                    continue                   # 'Dioscoreaceae s.l': a qualifier, not a typo
                if (p, frozenset((w, r))) not in listed:
                    listed.add((p, frozenset((w, r))))
                    out.spellings.append((p, h.anchor, w, 0, r, 0, "misspelt member of a clade"))
    out.spellings.sort(key=lambda r: (r[0], r[2], r[4]))

    # -- unbalanced brackets in header lines; a line repeated on many pages is listed once
    lines: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for p, hs in page_headers.items():
        for h in hs:
            t = h.text
            if "[" in t and t.count("[") != t.count("]"):
                lines[t].append((p, h.anchor))
    for t, locs in sorted(lines.items(), key=lambda kv: (-len(kv[1]), kv[1][0][0], kv[0])):
        out.brackets.append(([p for p, _ in locs], locs[0][1] if len(locs) == 1 else None, t))

    # -- anc attributes and anchors
    anchors = {p: _anchors(h) for p, h in htmls.items()}
    site = set().union(*anchors.values()) if anchors else set()
    lower = {a.lower(): a for a in site}
    for p, hs in page_headers.items():
        for h in hs:
            if not h.anc or h.anc in site:
                continue
            if h.anc.lower() in lower:
                hint = lower[h.anc.lower()] + " (differs in case)"
            else:
                close = [c for c in difflib.get_close_matches(h.anc, sorted(anchors[p]), n=2,
                                                              cutoff=0.7) if c != h.anchor]
                hint = close[0] if close else None
            out.anc.append((p, h.anchor, h.label, h.anc, hint))
        out.stray_anchors += [(p, a) for a in sorted(anchors[p]) if STRAY.search(a)]
    out.anc.sort(key=lambda r: (r[0], r[3], r[1] or ""))

    # -- links to order pages the crawl could not fetch, and pages served under two names
    for p, h in htmls.items():
        for rel in crawl.linked_order_pages(h):
            if rel not in manifest:
                out.dead_links.append((p, rel))
    out.dead_links = sorted(set(out.dead_links))
    by_sha = defaultdict(list)
    for rel, meta in manifest.items():
        if rel.startswith("orders/"):
            by_sha[meta["sha256"]].append(rel.removeprefix("orders/"))
    out.duplicates = sorted(sorted(v) for v in by_sha.values() if len(v) > 1)
    return out


# ---------------------------------------------------------------- HTML

def _e(s) -> str:
    return htmllib.escape(str(s))


def _link(page: str, anchor: str | None = None, text: str | None = None) -> str:
    url = crawl.BASE + "orders/" + page + ("#" + urllib.parse.quote(anchor, safe="") if anchor else "")
    return f'<a href="{_e(url)}">{_e(text or page + (f"#{anchor}" if anchor else ""))}</a>'


def _quotes(qs: list[Quote]) -> str:
    return '<div class="quotes">' + "".join(
        f'<div class="q"><div class="qsrc">{_link(q.page, q.anchor)}</div><code>{_e(q.text)}</code></div>'
        for q in qs) + "</div>"


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _pages(ps: list[str], limit: int = 8) -> str:
    shown = ", ".join(_e(p) for p in ps[:limit])
    return shown + (f" and {len(ps) - limit} more" if len(ps) > limit else "")


def _conflict(c: Conflict, kind: str) -> str:
    n, m = len(c.pages), len(c.accepted_pages)
    note = (f"Stated on {n} page{'s' * (n != 1)} ({_pages(c.pages)}). It overlaps "
            f"<code>{_e(c.accepted)}</code> without nesting, which is stated on {m} page{'s' * (m != 1)}"
            + (f" ({_pages(c.accepted_pages)})" if m <= 8 else "") + ".")
    return (f'<article class="item {kind}"><h3>{_e(c.rejected)}</h3>{_quotes(c.quotes)}'
            f'<div class="vs">contradicted by</div>{_quotes(c.rival)}<p>{note}</p></article>')


def _run_on(q: Quote, name: str) -> str:
    return (f'<article class="item within"><h3>Missing comma: {_e(name)}</h3>{_quotes([q])}'
            f'<p>Without a separator, <code>{_e(name)}</code> reads as one taxon. '
            f'Probably <code>{_e(", ".join(name.split()))}</code>.</p></article>')


def _foreign(q: Quote, name: str, homes: list[str]) -> str:
    return (f'<article class="item within"><h3>{_e(name)} in a {_e(q.page)} bracket</h3>{_quotes([q])}'
            f'<p><code>{_e(name)}</code> is treated only on {_pages(homes)}, a different order. '
            f'Another name was probably meant.</p></article>')


CSS = """
:root {
  --bg: #fbfcfa; --surface: #f1f4ef; --ink: #1d2620; --muted: #5b6a60; --rule: #d9e0d8;
  --accent: #2f6b4f; --error: #a4442a; --conflict: #8a6a12; --alt: #4b6584;
  --display: "Spectral", Georgia, serif; --body: "Public Sans", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #121714; --surface: #1a211c; --ink: #e2e8e1; --muted: #98a69b; --rule: #2c362f;
  --accent: #7cc4a0; --error: #e58f74; --conflict: #d9b55c; --alt: #93afd3; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #121714; --surface: #1a211c; --ink: #e2e8e1; --muted: #98a69b; --rule: #2c362f;
  --accent: #7cc4a0; --error: #e58f74; --conflict: #d9b55c; --alt: #93afd3; color-scheme: dark }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 var(--body); }
.wrap { max-width: 920px; margin: 0 auto; padding-block: 40px 80px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
h1, h2, h3 { font-family: var(--display); text-wrap: balance; font-weight: 600; }
h1 { font-size: 2.3rem; line-height: 1.15; margin: 0 0 .4rem; }
h2 { font-size: 1.45rem; margin: 3rem 0 .4rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
h2 .n { color: var(--accent); margin-right: .4em; }
h3 { font-size: 1.02rem; margin: 0 0 .5rem; font-family: var(--mono); font-weight: 400; overflow-wrap: anywhere; }
p { max-width: 68ch; }
a { color: var(--accent); text-underline-offset: 2px; }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.lede { color: var(--muted); font-size: 1.05rem; max-width: 66ch; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 1.2rem 0 0; padding: 0; list-style: none; font-size: .9rem; }
.meta li { display: flex; gap: 8px; align-items: baseline; }
.meta b { font-variant-numeric: tabular-nums; font-size: 1.05rem; }
.meta a { color: var(--ink); }
.sec-intro, .dim, .pages { color: var(--muted); }
.items { display: grid; gap: 16px; margin-top: 1rem; }
.item { border-left: 3px solid var(--c); padding: 4px 0 4px 16px; min-width: 0; }
.item.conflict { --c: var(--conflict); } .item.within { --c: var(--error); } .item.alt { --c: var(--alt); }
.item p { margin: .5rem 0 0; }
.vs { font-size: .78rem; color: var(--muted); margin: 6px 0 4px; text-transform: uppercase; letter-spacing: .05em; }
.quotes { display: grid; gap: 6px; }
.q { background: var(--surface); border-radius: 4px; padding: 8px 10px; min-width: 0; }
.qsrc { font-size: .78rem; margin-bottom: 2px; overflow-wrap: anywhere; }
.q code { display: block; font: 12.5px/1.5 var(--mono); overflow-wrap: anywhere; white-space: pre-wrap; }
code { font-family: var(--mono); font-size: .9em; overflow-wrap: anywhere; }
.tbl { overflow-x: auto; margin-top: .8rem; border: 1px solid var(--rule); border-radius: 4px; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; }
th, td { text-align: left; vertical-align: top; padding: 6px 10px; border-bottom: 1px solid var(--rule); }
th { background: var(--surface); font-weight: 600; }
td { font-variant-numeric: tabular-nums; }
details { margin-top: .8rem; }
summary { cursor: pointer; color: var(--accent); font-weight: 500; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule); color: var(--muted); font-size: .85rem; }
"""


def render(er: Errata) -> str:
    def details(n: int, what: str, body: str) -> str:
        return f"<details><summary>Show {n} {what}</summary>{body}</details>" if n else "<p class=dim>None.</p>"

    alt = _table(["Page", "Header line"],
                 [[_link(q.page, q.anchor, q.page + (f"#{q.anchor}" if q.anchor else "")),
                   f"<code>{_e(q.text)}</code>"] for q in er.alternatives])
    wording = Counter(m.group(0).lower() for q in er.alternatives
                      for m in [ALT_MARKER.search(q.text)] if m)
    spell = _table(["Page", "Written", "×", "Also written", "×", "Kind"],
                   [[_link(p, a, p + (f"#{a}" if a else "")), f"<code>{_e(w)}</code>", n or "",
                     f"<code>{_e(o)}</code>", m or "", _e(k)] for p, a, w, n, o, m, k in er.spellings])
    br = _table(["Page", "Header line", "[ / ]"],
                [[_pages(ps, 4) if len(ps) > 1 else _link(ps[0], a, ps[0] + (f"#{a}" if a else "")),
                  f"<code>{_e(t)}</code>", f"{t.count('[')} / {t.count(']')}"] for ps, a, t in er.brackets])
    anc = _table(["Page", "Header (its anchor)", "anc=", "Nearest anchor on the page"],
                 [[_link(p), f"{_e(l)} <span class=dim>#{_e(o or '')}</span>", f"<code>{_e(t)}</code>",
                   f"<code>{_e(s)}</code>" if s else "<span class=dim>none close</span>"]
                  for p, o, l, t, s in er.anc])
    links = "".join(f"<li>{_link(p)} links to <code>{_e(rel)}</code>, which could not be fetched</li>"
                    for p, rel in er.dead_links)
    dups = "".join(f"<li>{' = '.join(_e(p) for p in d)}</li>" for d in er.duplicates)
    stray = ", ".join(_link(p, a, f"{p}#{a}") for p, a in er.stray_anchors)
    n_links = len(er.dead_links) + len(er.duplicates)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="claude:viewport-fit-sides" content="cover">
<title>APWeb Errata</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:wght@600&family=Public+Sans:wght@400;500;600&family=JetBrains+Mono&display=swap">
<style>{CSS}</style></head>
<body><div class="wrap">
<header>
<h1>APWeb errata</h1>
<p class="lede">Errors, contradictions and ambiguities in the order pages of the Angiosperm Phylogeny Website,
found while building a structured index of the site. Each item links to the page and anchor and quotes the header line as written.</p>
<ul class="meta">
<li><b>{len(er.backbone)}</b><a href="#backbone">clades that differ between pages</a></li>
<li><b>{len(er.within) + len(er.foreign) + len(er.run_on)}</b><a href="#within">contradictions within a page</a></li>
<li><b>{len(er.alternatives)}</b><a href="#alt">marked alternatives</a></li>
<li><b>{len(er.spellings)}</b><a href="#spell">spellings</a></li>
<li><b>{len(er.brackets)}</b><a href="#brackets">unbalanced brackets</a></li>
<li><b>{len(er.anc)}</b><a href="#anc">anc links to missing anchors</a></li>
<li><b>{n_links}</b><a href="#links">links</a></li>
</ul>
<p class="pages">Snapshot {_e(er.snapshot)}: {er.n_pages} order pages under <code>apweb/orders/</code>.
Text inside HTML comments is not read.</p>
</header>

<h2 id="backbone"><span class="n">1</span>Clades that differ between pages</h2>
<p class="sec-intro">Each order page repeats the part of the tree above it. Each clade below is written on some pages but
overlaps, without nesting, a clade that more pages (or more recently updated ones) give. Older lines left on a page are the usual cause.</p>
<div class="items">{''.join(_conflict(c, 'conflict') for c in er.backbone) or '<p class=dim>None.</p>'}</div>

<h2 id="within"><span class="n">2</span>Contradictions within a page</h2>
<p class="sec-intro">Both arrangements are on the same page, and the contradicted one is not marked as an alternative.
Some are slips (a wrong name, a missing comma). Others are an older and a newer version left side by side.</p>
<div class="items">{''.join(_run_on(q, n) for q, n in er.run_on)}{''.join(_foreign(q, n, ps) for q, n, ps in er.foreign)}{''.join(_conflict(c, 'within') for c in er.within) or '<p class=dim>None.</p>'}</div>

<h2 id="alt"><span class="n">3</span>Marked alternatives</h2>
<p class="sec-intro">These lines are marked as hypotheses. The marker is worded {len(wording)} ways
({', '.join(f'“{_e(w)}” ×{n}' for w, n in wording.most_common())}). A single form would let a reader, or a program, tell
the preferred tree from the alternatives. Clades contradicted without any marker are in section 2.</p>
{details(len(er.alternatives), 'lines', alt)}

<h2 id="spell"><span class="n">4</span>Names written two ways</h2>
<p class="sec-intro">The same name in two spellings on one page (counts are for the visible page text), a header whose
anchor is spelled differently, or a misspelt member of a bracket that matches the same clade written correctly elsewhere.
Some pairs may be two real names; the authors will know.</p>
{details(len(er.spellings), 'pairs', spell)}

<h2 id="brackets"><span class="n">5</span>Unbalanced brackets in header lines</h2>
<p class="sec-intro">Header lines where the number of [ and ] differ. A line repeated on several pages is listed once.</p>
{details(len(er.brackets), 'lines', br)}

<h2 id="anc"><span class="n">6</span>anc attributes that point to missing anchors</h2>
<p class="sec-intro">The <code>anc</code> attribute names the anchor of a header's parent. These name an anchor that exists nowhere
on the site. Readers are not affected, but a tree built from the links breaks. Where an anchor on the same page is close, it is shown.</p>
{'<p class="sec-intro">Anchors containing a stray character, which no link can match: ' + stray + '.</p>' if stray else ''}
{details(len(er.anc), 'links', anc)}

<h2 id="links"><span class="n">7</span>Links</h2>
{'<ul>' + links + '</ul>' if links else '<p class=dim>No dead links between order pages.</p>'}
{'<p class="sec-intro">Pages served under more than one name, with identical content:</p><ul>' + dups + '</ul>' if dups else ''}

<footer>Generated automatically by the APWeb ingest. Items are found by machine and may include deliberate choices that the
page text explains nearby.</footer>
</div></body></html>
"""


def write(er: Errata) -> Path:
    out = config.data_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"apweb-errata-{er.snapshot}.html"
    path.write_text(render(er))
    return path
