"""APG I-IV classifications, from the English Wikipedia transcriptions.

Topology comes from the order-level cladogram ({{clade}} template) where the page
has one (APG III, IV), otherwise from the indented outline (APG, APG II).
Family placement comes from the detailed family lists. Each version is its own
source, so versions can be compared claim by claim.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass, field

import httpx
from rich.console import Console

from ... import config, db
from ...db import Store, norm

console = Console(stderr=True)

API = "https://en.wikipedia.org/w/api.php"

VERSIONS = {
    "apg1": dict(version="I", year=1998, page="APG system",
                 citation="Angiosperm Phylogeny Group (1998). An ordinal classification for the "
                          "families of flowering plants. Ann. Missouri Bot. Gard. 85: 531-553.",
                 doi="10.2307/2992015"),
    "apg2": dict(version="II", year=2003, page="APG II system",
                 citation="Angiosperm Phylogeny Group (2003). An update of the Angiosperm Phylogeny "
                          "Group classification for the orders and families of flowering plants: "
                          "APG II. Bot. J. Linn. Soc. 141: 399-436.",
                 doi="10.1046/j.1095-8339.2003.t01-1-00158.x"),
    "apg3": dict(version="III", year=2009, page="APG III system",
                 citation="Angiosperm Phylogeny Group (2009). An update of the Angiosperm Phylogeny "
                          "Group classification for the orders and families of flowering plants: "
                          "APG III. Bot. J. Linn. Soc. 161: 105-121.",
                 doi="10.1111/j.1095-8339.2009.00996.x"),
    "apg4": dict(version="IV", year=2016, page="APG IV system",
                 citation="Angiosperm Phylogeny Group (2016). An update of the Angiosperm Phylogeny "
                          "Group classification for the orders and families of flowering plants: "
                          "APG IV. Bot. J. Linn. Soc. 181: 1-20.",
                 doi="10.1111/boj.12385"),
}

NOT_CLADES = re.compile(r"basal angiosperms|unplaced|probable sister|uncertain position|incertae|"
                        r"paraphyletic|grade", re.I)


def raw_dir():
    return config.data_dir() / "raw" / "apg"


def fetch(key: str, force: bool = False) -> dict:
    """Fetch (or load cached) wikitext + revision id for one APG version."""
    path = raw_dir() / f"{key}.json"
    if path.exists() and not force:
        return json.loads(path.read_text())
    if not os.environ.get("APW_CONTACT"):
        raise RuntimeError(
            "Wikimedia requires contact info in the User-Agent. Set APW_CONTACT to an email "
            "address or project URL, e.g. `export APW_CONTACT=you@example.org`.")
    params = dict(action="query", prop="revisions", titles=VERSIONS[key]["page"],
                  rvprop="ids|timestamp|content", rvslots="main", format="json", formatversion=2)
    r = httpx.get(API, params=params, headers={"User-Agent": config.user_agent()}, timeout=60)
    r.raise_for_status()
    page = r.json()["query"]["pages"][0]
    rev = page["revisions"][0]
    data = dict(title=page["title"], revid=rev["revid"], timestamp=rev["timestamp"],
                fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                wikitext=rev["slots"]["main"]["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def import_wikitext(key: str, path: Path, fetched_at: str) -> None:
    """Seed the cache from a wikitext file fetched by other means (revision unknown)."""
    data = dict(title=VERSIONS[key]["page"], revid=None, timestamp=None, fetched_at=fetched_at,
                wikitext=Path(path).read_text())
    raw_dir().mkdir(parents=True, exist_ok=True)
    (raw_dir() / f"{key}.json").write_text(json.dumps(data))


# ---------------------------------------------------------------- wikitext helpers

LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def link_names(s: str) -> list[str]:
    """Link targets; for in-page anchors ('[[#PENTA|continued]]') the '#target'."""
    return [clean_label(m[1]) for m in LINK.finditer(s)]


def clean_label(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s).replace("\xa0", " ")).strip()


def strip_markup(s: str) -> str:
    s = re.sub(r"<small>.*?</small>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = LINK.sub(lambda m: m[2] or m[1], s)
    return re.sub(r"'{2,}", "", s).strip()


def section(text: str, title: str) -> str:
    m = re.search(rf"^==\s*{re.escape(title)}\s*==\s*$", text, re.M)
    if not m:
        return ""
    nxt = re.search(r"^==[^=].*==\s*$", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start() if nxt else len(text)]


# ---------------------------------------------------------------- cladogram

@dataclass
class TNode:
    label: str | None = None
    leaf: str | None = None
    children: list["TNode"] = field(default_factory=list)
    targets: dict[str, "TNode"] = field(default_factory=dict)   # {{cladex}} targetA/subcladeA

    def leaves(self) -> list[str]:
        return [self.leaf] if self.leaf else [x for c in self.children for x in c.leaves()]

    def render(self) -> str:
        if self.leaf:
            return self.leaf
        if self.label:
            return self.label
        return "[" + " + ".join(c.render() for c in self.children) + "]"


def _split_params(body: str) -> list[str]:
    parts, depth, cur, i = [], 0, "", 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur += two
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur += two
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += body[i]
        i += 1
    parts.append(cur)
    return parts


def parse_clade(s: str) -> TNode:
    """Parse '{{clade |label1=[[x]] |1=... |2=...}}' into a tree."""
    s = s.strip()
    assert s.startswith("{{") and s.endswith("}}"), s[:40]
    params = _split_params(s[2:-2])[1:]          # drop 'clade'
    kids: dict[int, TNode] = {}
    labels: dict[int, str] = {}
    targets: dict[str, str] = {}
    subclades: dict[str, TNode] = {}
    for p in params:
        k, _, v = p.partition("=")
        k, v = k.strip(), re.sub(r"<!--.*?-->", "", v, flags=re.S).strip()
        if m := re.fullmatch(r"target([A-Z])", k):
            targets[m[1]] = v
        elif (m := re.fullmatch(r"subclade([A-Z])", k)) and v.startswith("{{"):
            subclades[m[1]] = parse_clade(v)
        elif re.fullmatch(r"\d+", k):
            if v.startswith("{{"):
                kids[int(k)] = parse_clade(v)
            else:
                names = link_names(v) or [clean_label(strip_markup(v))]
                if names and names[0]:
                    kids[int(k)] = TNode(leaf=names[0])
        elif re.fullmatch(r"label\d+", k):
            names = link_names(v) or [clean_label(strip_markup(v))]
            if names and names[0]:
                labels[int(k[5:])] = names[0]
    node = TNode(targets={targets[x]: subclades[x] for x in targets if x in subclades})
    for i in sorted(kids):
        c = kids[i]
        if i in labels:
            if c.leaf:   # a labelled leaf: keep leaf name, label as alternative clade name
                c = TNode(label=labels[i], children=[c])
            else:
                c.label = labels[i]
        node.children.append(c)
    return node


def _clade_blocks(text: str) -> list[tuple[str | None, str]]:
    """Top-level {{clade}} blocks, each with the {{anchor|X}} that precedes it, if any."""
    out, i = [], 0
    while (start := text.find("{{clade", i)) >= 0:
        depth, j = 0, start
        while j < len(text):
            if text.startswith("{{", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        anchor = re.search(r"\{\{anchor\|([^}|]+)\}\}\s*$", text[i:start])
        out.append((anchor[1].strip() if anchor else None, text[start:j]))
        i = j
    return out


def find_cladogram(text: str) -> TNode | None:
    """The Phylogeny section's tree. Wikipedia splits big trees into several blocks,
    linked by leaves like '([[#PENTA|continued]])' and '{{anchor|PENTA}} {{clade ...}}'."""
    blocks = _clade_blocks(section(text, "Phylogeny"))
    if not blocks:
        return None
    parsed = [(a, parse_clade(b)) for a, b in blocks]
    continued = {a: t for a, t in parsed if a}

    def graft(node: TNode) -> None:
        for i, c in enumerate(node.children):
            if c.leaf and c.leaf.startswith("#") and c.leaf[1:] in continued:
                sub = continued[c.leaf[1:]]
                node.children[i] = TNode(label=sub.label, children=sub.children)
            elif not c.leaf and len(c.children) == 1 and (c.children[0].leaf or "").startswith("#"):
                sub = continued.get(c.children[0].leaf[1:])     # labelled continuation
                if sub:
                    c.children = sub.children
            graft(node.children[i])

    root = parsed[0][1]
    graft(root)

    # {{cladex}}: a bare leaf 'CORE_EUDICOTS' continues at |targetA=CORE_EUDICOTS |subcladeA=...
    targets: dict[str, TNode] = {}

    def collect(n: TNode) -> None:
        targets.update(n.targets)
        for c in n.children:
            collect(c)

    def graft_targets(n: TNode) -> None:
        for i, c in enumerate(n.children):
            if c.leaf in targets:
                n.children[i] = TNode(children=targets[c.leaf].children)
            elif not c.leaf and len(c.children) == 1 and c.children[0].leaf in targets:
                c.children = targets[c.children[0].leaf].children
            graft_targets(n.children[i])

    collect(root)
    graft_targets(root)
    return root.children[0] if len(root.children) == 1 and not root.leaf else root


# ---------------------------------------------------------------- outline & family lists

@dataclass
class Entry:
    kind: str                 # clade | order | family | container
    name: str
    depth: int
    alt: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)   # APG II bracketed families
    includes: list[str] = field(default_factory=list)   # '(including Asaraceae, ...)'
    authority: str | None = None
    note: str | None = None


def parse_outline(text: str) -> list[Entry]:
    """'*:: order [[X]]' style outlines (APG, APG II, APG III short version)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^([*:]+)\s*(.*)$", line)
        if not m or not m[2].strip():
            continue
        depth, body = len(m[1]), m[2]
        names = link_names(body)
        low = strip_markup(body).lower()
        opt = re.findall(r"\[\+\s*family\s*\[\[([^\]|]+)", body)
        main_names = [n for n in names if n not in opt]
        if re.match(r"^\s*order\b", low) and main_names:
            out.append(Entry("order", main_names[0], depth))
        elif re.match(r"^\s*family\b", low) and main_names:
            note = "unplaced in an order" if "unplaced" in low else None
            out.append(Entry("family", main_names[0], depth, optional=opt, note=note))
        elif main_names and not NOT_CLADES.search(low):
            out.append(Entry("clade", main_names[0], depth, alt=main_names[1:]))
        else:
            out.append(Entry("container", strip_markup(body)[:80], depth))
    return out


def parse_family_lists(text: str) -> list[tuple[str | None, str | None, Entry]]:
    """Detailed version: '* [[Order]]' then '** [[Family]] <small>auth</small>' lines,
    under '=== [[Clade]] ===' headings. Returns (heading, order, family entry)."""
    body = section(text, "Detailed version")
    heading = order = None
    out = []
    for line in body.splitlines():
        h = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", line)
        if h:
            heading, order = strip_markup(h[2]), None
            continue
        m = re.match(r"^(\*+)\s*(.*)$", line)
        if not m:
            continue
        names = link_names(m[2])
        if not names:
            continue
        if len(m[1]) == 1 and not names[0].endswith("aceae"):
            order = names[0] if names[0].endswith("ales") else None   # skip unplaced genera
            continue
        if len(m[1]) == 1:          # a family placed directly in a clade
            order = None
        auth = re.search(r"<small>(.*?)</small>", m[2])
        incl = re.search(r"\(including (.*?)\)", m[2])
        includes = [strip_markup(x).split("<")[0].strip() for x in re.split(r",\s*(?=[A-Z])",
                    re.sub(r"<small>.*?</small>", "", incl[1]))] if incl else []
        out.append((heading, order, Entry("family", names[0], len(m[1]),
                                          authority=strip_markup(auth[1]) if auth else None,
                                          includes=[x for x in includes if x])))
    return out


# ---------------------------------------------------------------- ingest

def _rank(name: str, kind: str) -> str:
    if kind in ("order", "family"):
        return kind
    n = name.lower()
    return "order" if n.endswith("ales") else "family" if n.endswith("aceae") else "clade"


def ingest(keys: list[str] | None = None, con=None) -> dict:
    con = con or db.connect()
    db.init(con)
    stats = {}
    for key in keys or list(VERSIONS):
        meta = VERSIONS[key]
        data = fetch(key)
        text = data["wikitext"]
        wiki_url = (f"https://en.wikipedia.org/w/index.php?oldid={data['revid']}" if data["revid"]
                    else "https://en.wikipedia.org/wiki/" + data["title"].replace(" ", "_"))
        store = Store.for_source(
            con, key=key, family="apg", version=meta["version"],
            title=f"APG {meta['version']} ({meta['year']})", kind="classification",
            url=f"https://doi.org/{meta['doi']}", citation=meta["citation"],
            license="Classification facts; transcribed from English Wikipedia (CC BY-SA 4.0)",
            retrieved_at=data["fetched_at"],
            notes=f"Transcription: {data['title']}, revision {data['revid'] or 'unknown'} "
                  f"({data['timestamp'] or 'fetched ' + data['fetched_at']})")
        store.document(url=wiki_url, path=f"raw/apg/{key}.json", sha256=str(data["revid"] or ""),
                       fetched_at=data["fetched_at"], last_modified=data["timestamp"])
        concepts: dict[str, int] = {}

        def concept(name: str, kind: str = "clade", **kw) -> int:
            local = "n:" + norm(name)
            if local not in concepts:
                concepts[local] = store.concept(local, name, name=name, rank=_rank(name, kind),
                                                url=wiki_url, **kw)
            return concepts[local]

        edges: dict[int, tuple[int, dict]] = {}

        def child_of(child: int, parent: int, **q) -> None:
            if child != parent and child not in edges:
                edges[child] = (parent, q)

        tree = find_cladogram(text)
        if tree:
            def walk(node: TNode, parent: int | None) -> None:
                if node.leaf:
                    cid = concept(node.leaf)
                else:
                    leaves = sorted({norm(x) for x in node.leaves()})
                    if node.label:
                        cid = concept(node.label)
                    else:
                        local = "s:" + "|".join(leaves)
                        label = node.render()
                        if local not in concepts:
                            concepts[local] = store.concept(local, label, rank="clade",
                                                            informal=True, url=wiki_url)
                        cid = concepts[local]
                if parent is not None:
                    child_of(cid, parent, basis="cladogram")
                for c in node.children:
                    walk(c, cid)
            walk(tree, None)
            outline = []
        else:
            outline = parse_outline(section(text, "Groups") or text)

        # outline (APG I, II): nested clades/orders/families by indentation
        stack: list[tuple[int, int | None]] = []
        for e in outline:
            while stack and stack[-1][0] >= e.depth:
                stack.pop()
            parent = next((cid for d, cid in reversed(stack) if cid is not None), None)
            if e.kind == "container":
                stack.append((e.depth, None))
                continue
            cid = concept(e.name, e.kind)
            for alt in e.alt:
                store.label(cid, alt)
            if parent is not None:
                child_of(cid, parent, basis="outline", **({"note": e.note} if e.note else {}))
            for opt in e.optional:
                oid = concept(opt, "family")
                if parent is not None:
                    child_of(oid, parent, basis="outline", optional_segregate_of=e.name)
            stack.append((e.depth, cid))

        # family lists (APG III, IV)
        for heading, order, fam in parse_family_lists(text):
            fid = concept(fam.name, "family", authorship=fam.authority)
            if order:
                child_of(fid, concept(order, "order"), basis="family list")
            elif heading and not NOT_CLADES.search(heading):
                child_of(fid, concept(heading), basis="family list", note="not placed in an order")
            for inc in fam.includes:
                name = inc.split()[0]
                store.claim(fid, "synonym", value=inc, qualifiers={"kind": "included family"},
                            locator=wiki_url)
                store.label(fid, name, kind="synonym")

        for child, (parent, q) in edges.items():
            store.claim(child, "child_of", object_id=parent, qualifiers=q, locator=wiki_url,
                        index=False)
        n_fam = con.execute("SELECT count(*) FROM concept WHERE source_id=? AND rank='family'",
                            (store.source_id,)).fetchone()[0]
        n_ord = con.execute("SELECT count(*) FROM concept WHERE source_id=? AND rank='order'",
                            (store.source_id,)).fetchone()[0]
        stats[key] = {"concepts": len(concepts), "orders": n_ord, "families": n_fam,
                      "edges": len(edges), "revid": data["revid"]}
    con.commit()
    return stats
