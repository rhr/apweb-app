"""Pass 2: merge per-page headers into one node per clade and write claims to the store.

The same clade appears on many pages (the backbone 'spine' is repeated on every
order page), under varying labels. Nodes are merged by union-find over names
and bracket member sets. Each node's parent is chosen by weighted vote over:

  bracket   nesting inside bracket expressions, e.g. [Cornaceae [Nyssaceae + ...]]   (3)
  anc       the anchor's anc="..." attribute, resolved exactly on the page          (2)
  anc~      ... resolved elsewhere, or fuzzily (APWeb's anc values have typos)     (1.5/1)
  structure document order (previous spine line; nearest higher-ranked header)     (1)
"""

from __future__ import annotations

import difflib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ... import db
from ...db import Store, norm
from . import citations, crawl, pages, references

console = Console(stderr=True)

WEIGHTS = {"bracket": 3.0, "anc": 2.0, "anc-global": 1.5, "anc-fuzzy": 1.0, "structure": 1.0}
LEVEL = {"order": 1, "suborder": 1.5, "family": 2, "subfamily": 3, "supertribe": 3.5, "tribe": 4, "subtribe": 5, "genus": 3}
NON_ORDER_PAGES = {"orders/characters.htm"}
NAME_ALIASES = {"monocotyledons": "monocots", "monocotyledoneae": "monocots"}


def canon(name: str) -> str:
    n = norm(name)
    return NAME_ALIASES.get(n, n)


def variants(n: str) -> list[str]:
    """'asterids' <-> 'asteridae'. Only a fallback: Magnoliidae (Chase & Reveal's
    angiosperms) is not the magnoliids clade."""
    if n.endswith("ids"):
        return [n[:-3] + "idae"]
    if n.endswith("idae"):
        return [n[:-4] + "ids"]
    return []


class UF:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


@dataclass
class Node:
    key: str
    headers: list[pages.Header] = field(default_factory=list)
    implicit_labels: Counter = field(default_factory=Counter)
    atoms: set[str] = field(default_factory=set)
    votes: dict[str, dict[tuple[str, str], float]] = field(default_factory=lambda: defaultdict(dict))

    @property
    def names(self) -> list[str]:
        c = Counter(n for h in self.headers for n in h.names)
        return [n for n, _ in c.most_common()]

    @property
    def label(self) -> str:
        if self.headers:
            named = [h.names[0] for h in self.headers if h.names]
            if named:
                return Counter(named).most_common(1)[0][0]
            return Counter(h.label for h in self.headers).most_common(1)[0][0]
        return self.implicit_labels.most_common(1)[0][0]

    @property
    def informal(self) -> bool:
        return not self.names and not (self.implicit_labels and
                                       not self.label.startswith("["))

    @property
    def rank(self) -> str:
        if self.headers:
            return Counter(h.rank for h in self.headers).most_common(1)[0][0]
        return pages.infer_rank(self.label, self.informal, False)

    def home(self) -> pages.Header | None:
        """The occurrence with the most content (characters + topics + anchor)."""
        if not self.headers:
            return None
        return max(self.headers, key=lambda h: (len(h.characters) + sum(map(len, h.topics.values())),
                                                h.anchor is not None, -h.seq))


class Assembler:
    def __init__(self, page_headers: dict[str, list[pages.Header]]):
        self.page_headers = page_headers
        self.uf = UF()
        self.nodes: dict[str, Node] = {}
        self.header_names = {canon(n) for hs in page_headers.values() for h in hs for n in h.names}
        self._merge()
        self._vote()

    # -- identity

    def atom_key(self, atom: str) -> str:
        n = canon(atom)
        if n not in self.header_names:
            n = next((v for v in variants(n) if v in self.header_names), n)
        return "n:" + n

    def _group_key(self, g: pages.Group) -> str:
        return "s:" + "|".join(sorted({self.uf.find(self.atom_key(a)) for a in g.atoms()}))

    def _keys(self, h: pages.Header) -> list[str]:
        return ["n:" + canon(n) for n in h.names] + [self._group_key(g) for g in h.groups]

    def _merge(self) -> None:
        all_headers = [h for hs in self.page_headers.values() for h in hs]
        for h in all_headers:
            for n in h.names[1:]:
                self.uf.union("n:" + canon(h.names[0]), "n:" + canon(n))
        for _ in range(2):  # group keys depend on name merges, which groups can extend
            for h in all_headers:
                ks = self._keys(h)
                for k in ks[1:]:
                    self.uf.union(ks[0], k)
        for h in all_headers:
            self.node_for_key(self._keys(h)[0]).headers.append(h)
            for g in h.groups:
                for sub in g.groups():
                    gnode = self.node_for_key(self._group_key(sub))
                    gnode.implicit_labels[sub.render()] += 1
                    gnode.atoms |= sub.atoms()
                    for m in sub.members:
                        if isinstance(m, str):
                            self.node_for_key(self.atom_key(m)).implicit_labels[pages.display_name(m)] += 1

    def node_for_key(self, key: str) -> Node:
        root = self.uf.find(key)
        if root not in self.nodes:
            self.nodes[root] = Node(root)
        return self.nodes[root]

    def node_of(self, h: pages.Header) -> Node:
        return self.node_for_key(self._keys(h)[0])

    # -- parents

    def node_level(self, node: Node, depth: int = 0) -> float | None:
        """Depth below the order for clades inside an order page: 2 = family, 3 = subfamily,
        ... Informal clades sit just above their highest-ranked member."""
        if node.key in self._levels:
            return self._levels[node.key]
        self._levels[node.key] = None             # guard against cycles
        lv = None
        ranks = [LEVEL[h.rank] for h in node.headers if not h.informal and h.rank in LEVEL]
        if ranks:
            lv = min(ranks)
        elif depth < 8:
            atoms = node.atoms | {a for h in node.headers for a in h.atoms}
            known = [x for x in (self.node_level(self.node_for_key(self.atom_key(a)), depth + 1)
                                 for a in atoms) if x is not None]
            if known:
                lv = min(known) - 0.5
            else:      # named clade without members in its label: use its bracket children
                kids = [x for x in (self.node_level(self.nodes[k], depth + 1)
                                    for k in self.bracket_children.get(node.key, ())) if x is not None]
                if kids:
                    lv = min(kids) - 0.1
        self._levels[node.key] = lv
        return lv

    def _vote(self) -> None:
        global_anchors: dict[str, set[str]] = defaultdict(set)
        for hs in self.page_headers.values():
            for h in hs:
                if h.anchor:
                    global_anchors[h.anchor].add(self.node_of(h).key)

        def vote(page: str, child: Node, parent: Node, method: str) -> None:
            if child.key != parent.key:
                child.votes[parent.key][(page, method)] = WEIGHTS[method]

        # phase 1: topology written out in bracket expressions
        self.bracket_children: dict[str, set[str]] = defaultdict(set)
        for page, hs in self.page_headers.items():
            for h in hs:
                for g in h.groups:
                    for sub in g.groups():
                        snode = self.node_for_key(self._group_key(sub))
                        for m in sub.members:
                            mnode = (self.node_for_key(self._group_key(m)) if isinstance(m, pages.Group)
                                     else self.node_for_key(self.atom_key(m)))
                            vote(page, mnode, snode, "bracket")
                            if mnode.key != snode.key:
                                self.bracket_children[snode.key].add(mnode.key)

        # phase 2: anc attributes and document structure
        self._levels: dict[str, float | None] = {}
        for page, hs in self.page_headers.items():
            anchors = {h.anchor: self.node_of(h) for h in hs if h.anchor}
            first_order = next((i for i, h in enumerate(hs) if h.rank == "order"), len(hs))
            levels = []
            for i, h in enumerate(hs):
                if i < first_order:                    # backbone
                    levels.append(0.0)
                elif i == first_order or h.rank == "order":
                    levels.append(1.0)
                else:
                    lv = self.node_level(self.node_of(h))
                    levels.append(5.5 if lv is None else max(1.1, lv))
            for i, h in enumerate(hs):
                node = self.node_of(h)
                if h.anc:
                    if h.anc in anchors:
                        vote(page, node, anchors[h.anc], "anc")
                    elif len(global_anchors.get(h.anc, ())) == 1:
                        vote(page, node, self.nodes[next(iter(global_anchors[h.anc]))], "anc-global")
                    else:
                        close = difflib.get_close_matches(h.anc, list(anchors), n=1, cutoff=0.75)
                        if close:
                            vote(page, node, anchors[close[0]], "anc-fuzzy")
                lv = levels[i]
                for j in range(i - 1, -1, -1):
                    if levels[j] < lv or (lv == 0 and levels[j] == 0):
                        vote(page, node, self.node_of(hs[j]), "structure")
                        break

    def parents(self) -> dict[str, tuple[str, float, dict]]:
        """Pick a parent per node; returns key -> (parent key, score, detail)."""
        scored: dict[str, list[tuple[float, str, dict]]] = {}
        for key, node in self.nodes.items():
            cands = []
            has_order = any(self.nodes[p].rank == "order" for p in node.votes)
            for pkey, ev in node.votes.items():
                methods = Counter(m for _, m in ev)
                score = sum(ev.values())
                parent = self.nodes[pkey]
                # rank consistency: a family belongs in an order (or an informal clade
                # within one), not directly in a named supra-ordinal clade
                if node.rank == "family" and has_order and not parent.informal \
                        and parent.rank not in ("order", "family"):
                    score *= 0.25
                cands.append((score, pkey, dict(methods)))
            cands.sort(key=lambda c: (-c[0], -c[2].get("bracket", 0), c[1]))
            scored[key] = cands
        choice = {k: 0 for k, c in scored.items() if c}

        def chain_has_cycle(start: str) -> list[str] | None:
            seen, x = [], start
            while x in choice:
                if x in seen:
                    return seen[seen.index(x):]
                seen.append(x)
                x = scored[x][choice[x]][1]
            return None

        for key in list(choice):
            cyc = chain_has_cycle(key)
            while cyc:
                # move the weakest link in the cycle to its next candidate
                weakest = min(cyc, key=lambda k: scored[k][choice[k]][0])
                choice[weakest] += 1
                if choice[weakest] >= len(scored[weakest]):
                    del choice[weakest]
                cyc = chain_has_cycle(key)
        # Orphans inside an order page: attach to the nearest preceding family/order header.
        def ancestors(k: str) -> set[str]:
            seen = set()
            while k in choice and k not in seen:
                seen.add(k)
                k = scored[k][choice[k]][1]
            return seen | {k}

        fallback: dict[str, str] = {}
        for key, node in self.nodes.items():
            if key in choice or not node.headers:
                continue
            h = node.headers[0]
            hs = self.page_headers[h.page]
            for prev in reversed(hs[:h.seq]):
                pnode = self.node_of(prev)
                if prev.rank in ("family", "order") and key not in ancestors(pnode.key):
                    fallback[key] = pnode.key
                    choice[key] = -1
                    break

        out = {}
        for key, pkey in fallback.items():
            out[key] = (pkey, 0.0, {"methods": {"fallback": 1}, "alternatives": []})
            del choice[key]
        for key, i in choice.items():
            score, pkey, methods = scored[key][i]
            alts = [{"parent": self.nodes[p].label, "score": s, "methods": m}
                    for s, p, m in scored[key] if p != pkey]
            out[key] = (pkey, score, {"methods": methods, "alternatives": alts[:3]})
        return out


def page_url(page: str, anchor: str | None = None) -> str:
    return crawl.BASE + "orders/" + page + (f"#{anchor}" if anchor else "")


def ingest(snapshot: str | None = None, con=None) -> dict:
    snapshot = snapshot or crawl.latest_snapshot()
    if snapshot is None:
        raise RuntimeError("no APWeb snapshot; run `apw crawl apweb` first")
    root = crawl.snapshot_dir(snapshot)
    manifest = json.loads((root / "manifest.json").read_text())
    con = con or db.connect()
    db.init(con)

    store = Store.for_source(
        con, key=f"apweb@{snapshot}", family="apweb", version=snapshot,
        title="Angiosperm Phylogeny Website", kind="website", url=crawl.BASE,
        citation=f"Stevens, P. F. (2001 onwards). Angiosperm Phylogeny Website. "
                 f"Snapshot of {snapshot}.",
        license="Content © P. F. Stevens. Only extracted facts and links are stored.",
        retrieved_at=snapshot)
    for rel, meta in manifest.items():
        store.document(url=meta["url"], path=str(Path("raw/apweb") / snapshot / rel),
                       sha256=meta["sha256"], fetched_at=meta["fetched_at"],
                       last_modified=meta.get("last_modified"))

    # bibliography
    resolver = citations.Resolver()
    n_refs = skipped = 0
    seen_refs: set[str] = set()
    for rel in crawl.REFERENCE_PAGES:
        path = root / rel
        if not path.exists():
            continue
        refs, sk = references.parse_page(crawl.decode(path.read_bytes()))
        skipped += sk
        for r in refs:
            if r.local_id in seen_refs:       # same anchor/entry on two bibliography pages
                r.local_id += f"~{n_refs}"
            seen_refs.add(r.local_id)
            rid = store.reference(
                r.local_id, r.raw, first_author=r.first_author, first_norm=r.first_norm,
                initials=r.initials, second_norm=r.second_norm, n_authors=r.n_authors,
                authors=r.authors, year=r.year, cite_year=r.cite_year, title=r.title,
                container=r.container, volume=r.volume, pages=r.pages, doi=r.doi,
                url=crawl.BASE + rel + (f"#{r.anchor}" if r.anchor else ""))
            resolver.add(citations.RefKey(rid, r.first_norm, r.cite_year, r.n_authors,
                                          r.second_norm, r.initials, norm(r.authors),
                                          f" {norm(r.title or '')} "))
            n_refs += 1
    console.print(f"references: {n_refs} parsed, {skipped} skipped")

    # order pages
    page_headers = {}
    seen_sha = set()
    for rel in sorted(manifest):
        if not rel.startswith("orders/") or rel in NON_ORDER_PAGES:
            continue
        if manifest[rel]["sha256"] in seen_sha:     # e.g. Cycadales.html == cycadales.html
            continue
        seen_sha.add(manifest[rel]["sha256"])
        page = rel.removeprefix("orders/")
        page_headers[page] = pages.parse_page(page, crawl.decode((root / rel).read_bytes()))
    asm = Assembler(page_headers)
    console.print(f"pages: {len(page_headers)}, headers: "
                  f"{sum(map(len, page_headers.values()))}, nodes: {len(asm.nodes)}")

    # concepts
    cid: dict[str, int] = {}
    for key, node in asm.nodes.items():
        home = node.home()
        url = page_url(home.page, home.anchor) if home else None
        label = node.label
        cid[key] = store.concept(
            key, label, name=None if node.informal else label,
            authorship=home.authorship if home else None, rank=node.rank,
            informal=node.informal, url=url)
        for n in node.names[1:]:
            store.label(cid[key], n)
        for lbl in {h.label for h in node.headers if not h.names} | {
                g.render() for h in node.headers for g in h.groups} | set(node.implicit_labels):
            if lbl != label:
                store.label(cid[key], lbl)
        for h in node.headers:
            if h.anchor:
                store.label(cid[key], f"{h.page}#{h.anchor}", kind="anchor")

    # hierarchy
    parents = asm.parents()
    for key, (pkey, score, detail) in parents.items():
        store.claim(cid[key], "child_of", object_id=cid[pkey],
                    qualifiers={"score": score, **detail}, index=False)

    # characters, stats, synonyms, literature
    stats = Counter()

    def context(key: str) -> set[str]:
        """Names of the taxon, its named ancestors and children, for citation tie-breaking."""
        names, k = set(), key
        for _ in range(12):
            node = asm.nodes[k]
            names |= {f" {norm(n)} " for n in node.names if len(n) > 3}
            if k not in parents:
                break
            k = parents[k][0]
        for c, (p, _, _) in parents.items():
            if p == key:
                names |= {f" {norm(n)} " for n in asm.nodes[c].names if len(n) > 3}
        return names

    for key, node in asm.nodes.items():
        if not node.headers:
            continue
        home = node.home()
        loc = page_url(home.page, home.anchor)
        best = max(node.headers, key=lambda h: len(h.characters))
        best_loc = page_url(best.page, best.anchor)
        for i, c in enumerate(best.characters):
            q = {"position": i}
            if c.apomorphy:
                q["apomorphy"] = True
                q["apo"] = c.apo
            if c.uncertain:
                q["uncertain"] = True
            store.claim(cid[key], "has_character", value=c.text, qualifiers=q, locator=best_loc)
            stats["characters"] += 1
        for h in sorted(node.headers, key=lambda h: h is not home):
            if h.counts or h.distribution or h.synonyms:
                hl = page_url(h.page, h.anchor)
                for k, v in h.counts.items():
                    store.claim(cid[key], k, value=str(v), locator=hl, index=False)
                if h.distribution:
                    store.claim(cid[key], "distribution", value=h.distribution, locator=hl)
                for name, auth in dict(h.synonyms).items():
                    store.claim(cid[key], "synonym", value=f"{name} {auth}".strip(), locator=hl)
                    store.label(cid[key], name, kind="synonym")
                break
        topics: dict[str, dict[tuple, citations.Mention]] = defaultdict(dict)
        for h in node.headers:
            for topic, ms in h.topics.items():
                for m in ms:
                    topics[topic].setdefault((m.first, tuple(m.years), m.etal, m.second, m.also), m)
        for topic, ms in topics.items():
            mentions = [m for m in ms.values() if resolver.known(m)]
            if not mentions:
                continue
            claim = store.claim(cid[key], "discussed_in", value=topic, locator=loc)
            ctx = context(key)
            for m in mentions:
                for y in m.years:
                    ref, conf, n = resolver.resolve(m, y, ctx)
                    store.citation(claim, m.text, ref, conf, n)
                    stats["citations"] += 1
                    stats["citations_resolved"] += ref is not None
    con.commit()
    stats.update(nodes=len(asm.nodes), references=n_refs, parented=len(parents))
    return dict(stats)
