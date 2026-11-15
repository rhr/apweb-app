"""Pass 2: merge per-page headers into one node per clade and write claims to the store.

The same clade appears on many pages (the backbone 'spine' is repeated on every
order page), under varying labels. Nodes are merged by union-find over names
and bracket member sets. Each node's parent is chosen by weighted vote over:

  bracket   nesting inside bracket expressions, e.g. [Cornaceae [Nyssaceae + ...]]   (3)
  anc       the anchor's anc="..." attribute, resolved exactly on the page          (2)
  anc~      ... resolved elsewhere, or fuzzily (APWeb's anc values have typos)     (1.5/1)
  structure document order (previous spine line; nearest higher-ranked header)     (1)

Pages are updated at different times, so they can disagree on topology (e.g. asterid i
on the Cardiopteridales vs the Lamiales page, or a stale [Metteniusales + [Garryales ...]]
line). Clades whose member sets are incompatible with a better-supported clade are left
out of the hierarchy; see Assembler._resolve_conflicts.
"""

from __future__ import annotations

import difflib
import email.utils
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from ... import db
from ...db import Store, norm
from . import ages, citations, crawl, errata, pages, references

console = Console(stderr=True)

WEIGHTS = {"bracket": 3.0, "anc": 2.0, "anc-global": 1.5, "anc-fuzzy": 1.0, "structure": 1.0}
LEVEL = {"subclass": 0.6, "superorder": 0.8, "order": 1, "suborder": 1.5, "family": 2, "subfamily": 3, "supertribe": 3.5, "tribe": 4, "subtribe": 5, "genus": 3}
NON_ORDER_PAGES = {"orders/characters.htm"}
NAME_ALIASES = {"monocotyledons": "monocots", "monocotyledoneae": "monocots",
                "centrosperms": "centrospermae"}
VERNACULAR = re.compile(r"(?i)\s(?:clade|group|grade|alliance)$")
RANK_SUFFIXES = ("oideae", "aceae", "ineae", "inae", "odae", "idae", "anae", "ales", "eae")
FILLER = {"n:etc", "n:the rest", "n:rest", "n:others"}     # not taxa, though some head a section


def rank_suffix(name: str) -> str | None:
    """Ophioglossoideae and Ophioglossaceae differ in rank, not by a typo."""
    return next((x for x in RANK_SUFFIXES if name.endswith(x)), None)


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
    def __init__(self, page_headers: dict[str, list[pages.Header]],
                 page_dates: dict[str, float] | None = None):
        self.page_headers = page_headers
        self.page_dates = page_dates or {}
        self.uf = UF()
        self.nodes: dict[str, Node] = {}
        self._group_keys: dict[int, str] = {}      # frozen after _merge
        self.header_names = {canon(n) for hs in page_headers.values() for h in hs for n in h.names}
        self.page_terms = {p: {canon(a) for h in hs for a in h.atoms} |
                           {canon(n) for h in hs for n in h.names[:1]} for p, hs in page_headers.items()}
        self._merge()
        self._resolve_conflicts()
        self._vote()

    # -- identity

    def atom_key(self, atom: str) -> str:
        n = canon(atom)
        if n not in self.header_names:
            n = next((v for v in variants(n) if v in self.header_names), n)
        return "n:" + n

    def _group_key(self, g: pages.Group) -> str:
        if id(g) in self._group_keys:
            return self._group_keys[id(g)]
        return "s:" + "|".join(sorted({self.uf.find(self.atom_key(a)) for a in g.atoms()}))

    def identity_names(self, h: pages.Header) -> list[str]:
        """Names that identify the clade site-wide. Vernacular alternatives are not unique
        ('Old World clade' is in both Fabaceae and Apocynaceae): they count only where
        their own page uses them ('Dalbergioid clade' in a Fabales bracket)."""
        return h.names[:1] + [n for n in h.names[1:] if not VERNACULAR.search(n)
                              or canon(n) in self.page_terms[h.page]]

    def _keys(self, h: pages.Header) -> list[str]:
        return ["n:" + canon(n) for n in self.identity_names(h)] + \
            [self._group_key(g) for g in h.groups]

    def _merge(self) -> None:
        all_headers = [h for hs in self.page_headers.values() for h in hs]
        for h in all_headers:
            for n in self.identity_names(h)[1:]:
                self.uf.union("n:" + canon(h.names[0]), "n:" + canon(n))
        for _ in range(2):  # group keys depend on name merges, which groups can extend
            for h in all_headers:
                ks = self._keys(h)
                for k in ks[1:]:
                    self.uf.union(ks[0], k)
        self._group_keys = {id(sub): self._group_key(sub)
                            for h in all_headers for g in h.groups for sub in g.groups()}
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

    def lookup(self, label: str) -> str | None:
        """Key of the clade a name or bracket written in prose refers to, if it has one."""
        label = label.strip()
        if label.startswith("["):
            g = pages.parse_brackets(label)
            if g is None:
                return None
            key = self.uf.find(self._group_key(g))
        else:
            key = self.uf.find(self.atom_key(label))
        node = self.nodes.get(key)
        return key if node is not None and (node.headers or node.implicit_labels) else None

    # -- conflicting clades

    def clusters(self) -> dict[str, tuple[frozenset[str], frozenset[str]]]:
        """Clades with members, as (terminal clades beneath them, unknown members).
        Members that are themselves clades with members are expanded (e.g. Lamiidae in
        [Lamiidae + Campanulidae]). Only self.terminals are terminals; other members ('etc',
        typos like 'Ecdeicoleaceae') are unknowns that may stand for any taxon."""
        direct: dict[str, set[str]] = {}
        for key, node in self.nodes.items():
            atoms = node.atoms | {a for h in node.headers for a in h.atoms}
            if atoms:
                direct[key] = {self.uf.find(self.atom_key(a)) for a in atoms} - {key}
        memo: dict[str, tuple[frozenset[str], frozenset[str]]] = {}

        def leaves(key: str, stack: set[str]) -> tuple[frozenset[str], frozenset[str]]:
            if key in memo:
                return memo[key]
            if key not in direct or key in stack:
                known = key in self.terminals
                return (frozenset({key}), frozenset()) if known else (frozenset(), frozenset({key}))
            stack.add(key)
            subs = [leaves(k, stack) for k in direct[key]]
            stack.discard(key)
            memo[key] = (frozenset().union(*(t for t, _ in subs)),
                         frozenset().union(*(u for _, u in subs)))
            return memo[key]
        return {k: leaves(k, set()) for k in direct}

    def _terminals(self) -> dict[str, set[str]]:
        """Taxa that count as members of clades, with the pages that treat them: those with
        a header, and those without one (genera, fern subfamilies) that a page names in two
        or more of its headers. A name without a header must be one Latin word ('Ixorodae
        Airospermeae' lacks a comma; 'Ochnoids' is informal), and not a near-spelling of a
        header-backed taxon on the same page ('Alzataeaceae' for Alzateaceae)."""
        out: dict[str, set[str]] = defaultdict(set)
        for k, n in self.nodes.items():
            if n.headers and k not in FILLER:
                out[k] |= {h.page for h in n.headers}
        for page, hs in self.page_headers.items():
            names = sorted(self.header_names & self.page_terms[page])
            seen: Counter[str] = Counter()
            for h in hs:
                seen.update({self.uf.find(self.atom_key(a)) for a in h.atoms if a[:1].isupper()})
            for k, c in seen.items():
                if c < 2 or k in FILLER or self.nodes[k].headers \
                        or not re.fullmatch(r"n:[a-zë]+", k) or k.endswith("oids"):
                    continue
                if not any(rank_suffix(m) == rank_suffix(k[2:]) or not rank_suffix(m)
                           for m in difflib.get_close_matches(k[2:], names, n=3, cutoff=0.85)):
                    out[k].add(page)
        return out

    def _resolve_conflicts(self) -> None:
        """Accept clades greedily, best supported first (pages asserting them; then fewest
        contradictions with other clades, as a typo contradicts many; then the most recently
        modified page). A clade that overlaps an accepted one without nesting is rejected.
        self.conflicts maps rejected key -> the accepted clade it contradicts."""
        self.support: dict[str, set[str]] = defaultdict(set)
        for page, hs in self.page_headers.items():
            for h in hs:
                self.support[self.node_of(h).key].add(page)
                for g in h.groups:
                    for sub in g.groups():
                        self.support[self.node_for_key(self._group_key(sub)).key].add(page)
        # A page speaks only for the families and below that it treats; orders and above
        # are on every page's spine. (gentianalesweb's APSA clade lists Aristolochioideae
        # for Asclepiadoideae, which must not contradict Piperales.)
        self.terminals = home = self._terminals()
        spine = {k for k, n in self.nodes.items() if n.headers and LEVEL.get(n.rank, 0) < 2}
        clusters: dict[str, tuple[frozenset[str], int]] = {}
        unknown: dict[str, frozenset[str]] = {}
        for key, (s, u) in self.clusters().items():
            known = frozenset(t for t in s if t in spine or home[t] & self.support[key])
            if known:
                unknown[key] = u | (s - known)
                clusters[key] = (known, len(unknown[key]))

        def compatible(a: tuple[frozenset[str], int], b: tuple[frozenset[str], int]) -> bool:
            """Disjoint, or nested once each side's unknowns are filled in."""
            (s, ws), (t, wt) = a, b
            return not (s & t) or len(s - t) <= wt or len(t - s) <= ws

        by_terminal: dict[str, list[str]] = defaultdict(list)
        for key, (s, _) in clusters.items():
            for t in s:
                by_terminal[t].append(key)
        n_clash = {key: sum(1 for o in {o for t in c[0] for o in by_terminal[t]}
                            if not compatible(c, clusters[o])) for key, c in clusters.items()}
        order = sorted(clusters, key=lambda k: (-len(self.support[k]), n_clash[k],
                                                -max((self.page_dates.get(p, 0.0)
                                                      for p in self.support[k]), default=0.0), k))
        accepted: dict[str, tuple[frozenset[str], int]] = {}
        self.conflicts: dict[str, str] = {}
        for key in order:
            c = clusters[key]
            clash = next((a for a, d in accepted.items() if not compatible(c, d)), None)
            if clash:
                self.conflicts[key] = clash
            else:
                accepted[key] = c

        # The same clade written twice, with a typo ([Aneomoneae + Ranunculeae] /
        # [Anemoneae + Ranunculeae]) or with the same members nested differently: one node,
        # under the better supported key. (The contradicting inner clade is rejected above.)
        def misspelt(a: str, b: str) -> bool:
            """Each unknown member of a not also in b is a near-spelling of a member only
            b has."""
            extra = accepted[b][0] - accepted[a][0]
            typos = unknown[a] - unknown[b]
            return len(typos) == len(extra) and all(
                difflib.get_close_matches(u, extra, n=1, cutoff=0.85) for u in typos)

        rank = {k: i for i, k in enumerate(accepted)}
        same: dict[str, str] = {}
        for key, (s, ws) in accepted.items():
            for o in sorted({o for t in s for o in by_terminal[t] if o in rank}, key=rank.get):
                if rank[o] >= rank[key]:
                    break
                t, wt = accepted[o]
                if o not in same and len(s) + ws == len(t) + wt and (
                        (s <= t and misspelt(key, o)) or (t <= s and misspelt(o, key))):
                    same[key] = o
                    break
        # what was misspelt, for the errata: unknown member -> the terminal it stands for
        self.misspellings: dict[str, str] = {}
        for dup, keep in same.items():
            for a, b in ((dup, keep), (keep, dup)):
                extra = accepted[b][0] - accepted[a][0]
                for u in unknown[a] - unknown[b]:
                    m = difflib.get_close_matches(u, extra, n=1, cutoff=0.85)
                    if m:
                        self.misspellings[u] = m[0]
        for dup, keep in same.items():
            self._absorb(keep, dup)
        f = self.uf.find
        support: dict[str, set[str]] = defaultdict(set)
        for k, v in self.support.items():
            support[f(k)] |= v
        self.support = support
        self.conflicts = {k: f(v) for k, v in self.conflicts.items()}
        self.accepted = {k: (s, w, rank[k]) for k, (s, w) in accepted.items() if k not in same}

    def _absorb(self, keep: str, dup: str) -> None:
        """Merge node dup into node keep; keep's key stays the root."""
        a, b = self.nodes.pop(keep), self.nodes.pop(dup)
        self.uf.parent[self.uf.find(dup)] = keep
        self.nodes[keep] = Node(keep, a.headers + b.headers, a.implicit_labels + b.implicit_labels,
                                a.atoms | b.atoms)

    def clade_parents(self) -> dict[str, str]:
        """Parents entailed by the accepted clades: a terminal goes in the smallest clade
        listing it, a clade in the smallest larger one containing it (unknown members
        absorb the difference). Clades of equal size nest as their votes say: APWeb writes
        a grade as a run of headers, [Senegalia ii + R], [Senegalia iii + R], ..., each
        anc-linked to the one before. Otherwise the better supported clade is the outer."""
        acc = self.accepted
        size = {k: len(s) + w for k, (s, w, _) in acc.items()}
        by_terminal: dict[str, list[str]] = defaultdict(list)
        for k, (s, _, _) in acc.items():
            for t in s:
                by_terminal[t].append(k)

        def contains(o: str, k: str) -> bool:
            return len(acc[k][0] - acc[o][0]) <= acc[o][1]

        reached: dict[str, set[str]] = {}

        def reach(k: str) -> set[str]:
            """Equal-size clades that k is voted into, directly or through each other."""
            if k not in reached:
                out, stack = set(), [k]
                while stack:
                    x = stack.pop()
                    for o in self.nodes[x].votes:
                        if o in acc and o != k and o not in out and size[o] == size[k] \
                                and contains(o, x):
                            out.add(o)
                            stack.append(o)
                reached[k] = out
            return reached[k]

        def inside(k: str, o: str) -> bool:
            if size[o] != size[k]:
                return size[o] > size[k]
            if o in reach(k) or k in reach(o):
                return o in reach(k)
            return acc[o][2] < acc[k][2]

        def innermost(ks: list[str]) -> str:
            m = min(size[o] for o in ks)
            ties = [o for o in ks if size[o] == m]
            return max(ties, key=lambda o: (sum(inside(o, x) for x in ties if x != o), acc[o][2]))

        out = {t: innermost(ks) for t, ks in by_terminal.items()}
        homeless = []
        for k, (s, _, _) in acc.items():
            outer = [o for o in {o for t in s for o in by_terminal[t]}
                     if o != k and contains(o, k) and inside(k, o)]
            if outer:
                out[k] = innermost(outer)
            else:
                homeless.append(k)
        # A clade no other contains (Old World Clade's own bracket is in abbreviations,
        # [Indig, Mill.-Phas, Rob, IRL Clade], so it holds less than the Millettioid clade
        # nested in it) goes where its largest nested clade goes.
        for k in homeless:
            s = acc[k][0]
            inner = [o for o in {o for t in s for o in by_terminal[t]}
                     if o != k and o in out and out[o] != k and contains(k, o) and inside(o, k)]
            if inner:
                o = max(inner, key=lambda o: (size[o], -acc[o][2]))
                p, seen = out[o], set()
                while p in out and p != k and p not in seen:      # unless that is inside k
                    seen.add(p)
                    p = out[p]
                if p != k:
                    out[k] = out[o]
        return out

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
                # just above the members, and not level with a main rank: a clade of
                # supertribes (3.5) is inside the subfamily (3)
                lv = min(known) - 0.5
                if lv in (1, 2, 3, 4):
                    lv += 0.25
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
            last_order = max((i for i, h in enumerate(hs) if h.rank == "order"), default=-1)
            levels = []
            for i, h in enumerate(hs):
                if i < first_order:                    # backbone
                    levels.append(0.0)
                elif i == first_order or h.rank == "order":
                    levels.append(1.0)
                else:
                    # below the order, except on pages that treat several orders
                    # (Sporing.html): subclasses, clades of orders and above, and named
                    # clades of unknown level followed by another order (Polypodiopsida)
                    node = self.node_of(h)
                    lv = self.node_level(node)
                    if lv is None and i < last_order and h.rank == "clade" and not h.informal:
                        lv = 0.0
                    elif lv is not None and lv < 1 and any(
                            (x := self.node_level(self.node_for_key(self.atom_key(a)))) is not None
                            and x >= 2 for a in node.atoms | h.atoms):
                        lv = 1.1
                    levels.append(5.5 if lv is None else lv if lv < 1 else max(1.1, lv))
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
        clade_parent = self.clade_parents()

        def within(p: str, c: str) -> bool:
            seen = set()
            while p in clade_parent and p not in seen:
                seen.add(p)
                p = clade_parent[p]
                if p == c:
                    return True
            return False

        def fits(p: str, key: str) -> bool:
            """An accepted clade can hold key only if key's members are among its own."""
            if p not in self.accepted:
                return True
            s = self.accepted[key][0] if key in self.accepted else \
                {key} if key in self.terminals else set()
            return len(s - self.accepted[p][0]) <= self.accepted[p][1]

        def lift(p: str) -> str | None:
            """A vote for a rejected clade goes to where that clade itself was voted."""
            seen = set()
            while p in self.conflicts:
                if p in seen or not self.nodes[p].votes:
                    return None
                seen.add(p)
                vs = self.nodes[p].votes
                p = max(vs, key=lambda q: (sum(vs[q].values()), q))
            return p

        for key, node in self.nodes.items():
            if key in self.conflicts:
                continue
            votes: dict[str, dict] = defaultdict(dict)
            for pkey, ev in node.votes.items():
                pkey = lift(pkey)
                if pkey is not None and pkey != key:
                    votes[pkey].update(ev)
            cands = []
            has_order = any(self.nodes[p].rank == "order" for p in votes)
            for pkey, ev in votes.items():
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
            # the accepted clades decide, unless the votes pick something inside that clade
            # (e.g. a family that is itself a member) that can hold this one
            c = clade_parent.get(key)
            top = cands[0][1] if cands else None
            if c and not (top == c or top and within(top, c) and not within(top, key)
                          and fits(top, key)):
                ev = next((x for x in cands if x[1] == c), (0.0, c, {}))
                cands = [(ev[0], c, {**ev[2], "members": 1})] + [x for x in cands if x[1] != c]
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
                k = fallback[k] if k in fallback else scored[k][choice[k]][1]
            return seen | {k}

        fallback: dict[str, str] = {}
        for key, node in self.nodes.items():
            if key in choice or key in self.conflicts or not node.headers:
                continue
            h = node.headers[0]
            hs = self.page_headers[h.page]
            for prev in reversed(hs[:h.seq]):
                pnode = self.node_of(prev)
                if prev.rank in ("family", "order") and pnode.key not in self.conflicts \
                        and key not in ancestors(pnode.key):
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
    htmls = {}
    seen_sha = set()
    for rel in sorted(manifest):
        if not rel.startswith("orders/") or rel in NON_ORDER_PAGES:
            continue
        if manifest[rel]["sha256"] in seen_sha:     # e.g. Cycadales.html == cycadales.html
            continue
        seen_sha.add(manifest[rel]["sha256"])
        htmls[rel.removeprefix("orders/")] = crawl.decode((root / rel).read_bytes())
    # two passes: the second knows which names are families anywhere on the site
    first = {p: pages.parse_page(p, h) for p, h in htmls.items()}
    families = {norm(n) for hs in first.values() for h in hs if h.rank == "family" for n in h.names}
    page_headers = {p: pages.parse_page(p, h, families) for p, h in htmls.items()}
    page_dates = {}
    for p in page_headers:
        lm = manifest["orders/" + p].get("last_modified")
        if lm:
            page_dates[p] = email.utils.parsedate_to_datetime(lm).timestamp()
    asm = Assembler(page_headers, page_dates)
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
    for key, other in asm.conflicts.items():
        store.claim(cid[key], "conflicts_with", object_id=cid[other],
                    qualifiers={"pages": sorted(asm.support[key]),
                                "support": len(asm.support[key]),
                                "object_support": len(asm.support[other])}, index=False)
    console.print(f"conflicting clades left out of the hierarchy: {len(asm.conflicts)}")

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

    # clade ages: one claim per estimate, on the clade it dates
    records, _ = ages.collect(asm)
    contexts: dict[str, set[str]] = {}
    done = set()
    for r in records:
        e = r.est
        pred = "fossil_age" if e.basis == "fossil" else f"{e.kind}_age"
        sig = (r.target, pred, e.text, e.cite.text if e.cite else None)
        if sig in done:         # the same sentence under two headers of one clade
            continue
        done.add(sig)
        q: dict = {"points": e.points, "form": e.form, "text": e.text}
        if e.older is not None:
            q["older"] = e.older
        if e.younger is not None:
            q["younger"] = e.younger
        if e.approx:
            q["approx"] = True
        if pred != "fossil_age":
            q["kind_explicit"] = e.kind_explicit
        if e.methods:
            q["methods"] = e.methods
        if r.subject_resolved is not None:
            q["subject"] = e.subject
            q["subject_resolved"] = r.subject_resolved
        if r.target != r.node:
            q["stated_under"] = asm.nodes[r.node].label
        if e.problems:
            q["problems"] = e.problems
        claim = store.claim(cid[r.target], pred, value=f"{e.value:g}", qualifiers=q,
                            locator=page_url(r.header.page, r.header.anchor), index=False)
        stats[pred] += 1
        if e.pers_comm:
            store.citation(claim, e.pers_comm, None, 1.0, 0)
        if e.cite is None:
            continue
        if not resolver.known(e.cite):
            store.citation(claim, e.cite.text, None, 0.0, 0)
            continue
        if r.target not in contexts:
            contexts[r.target] = context(r.target)
        for y in e.cite.years:
            ref, conf, n = resolver.resolve(e.cite, y, contexts[r.target])
            store.citation(claim, e.cite.text, ref, conf, n)
            stats["age_citations_resolved"] += ref is not None

    con.commit()
    stats.update(nodes=len(asm.nodes), references=n_refs, parented=len(parents))

    report = errata.write(errata.build(snapshot, manifest, htmls, asm))
    console.print(f"errata: {report}")
    return dict(stats)
