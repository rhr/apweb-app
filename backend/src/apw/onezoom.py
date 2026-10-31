"""Serve a source's tree in the formats read by the OneZoom tree viewer (prototype).

OneZoom's viewer (github.com/OneZoom/OZtree) loads the tree topology once, as a Newick
string stripped of everything except brackets (`completetree.js`), then fetches names
and numbers for the nodes on screen from a `node_details` endpoint. Nodes are addressed
by "OZid": interior nodes are numbered 1.. in the order of their opening bracket, tips
1.. from left to right (sent as negative numbers).

The bracket string can only describe a strictly bifurcating tree in which tips are
empty. So:
- a node with k > 2 children is split into nested two-way splits; every split after
  the first is written with `{}` instead of `()`. OneZoom's polytomy view merges these
  back into one node; its spiral view shows them as extra (unnamed) forks;
- the viewer finds a node's first child by scanning from its closing bracket, so a tip
  can only be told apart from its sibling if it comes first. Children are ordered tips
  first, then by label;
- a node with a single child (e.g. an order with one family) can't be written at all,
  so a chain of single children is drawn as one node labelled "Vitales / Vitaceae";
- a source with several roots (APG I) gets an unnamed root above them.

OneZoom keys its look-ups on Open Tree ids ("ott"). Until the store has those, the
concept id stands in for them, which lets the viewer map a clicked tip back to a
concept. Nothing about sponsorship, images, IUCN status or vernacular names is served.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from . import query

CUT_THRESHOLD = 10000  # as in OneZoom's tree-build; trees this small barely use cut maps

NODE_COLS = ["id", "ott", "popularity", "age", "name", "iucnNE", "iucnDD", "iucnLC",
             "iucnNT", "iucnVU", "iucnEN", "iucnCR", "iucnEW", "iucnEX",
             *(f"{{pic}}{i}" for i in range(1, 9))]
LEAF_COLS = ["id", "ott", "popularity", "name", "extinction_date", "price"]
IMAGE_COLS = ["ott", "src_id", "src", "rating"]
RESERVATION_COLS = ["OTT_ID", "verified_kind", "verified_name", "verified_more_info",
                    "verified_url"]


@dataclass
class OzTree:
    raw: str                        # brackets only
    nodes: list[list[int]]          # OZid -> concepts drawn as that node ([] = split or added root)
    leaves: list[list[int]]         # -OZid -> concepts drawn as that tip
    ozid: dict[int, int]            # concept id -> OZid (negative for tips)
    species: dict[int, int]         # concept id -> species below it, where known


def build(con: sqlite3.Connection, t: query.Tree) -> OzTree:
    claimed = {r[0]: int(r[1]) for r in con.execute(
        "SELECT subject_id, value FROM claim WHERE source_id = ? AND predicate = 'species_count'"
        " AND value GLOB '[0-9]*'", (t.source_id,))}
    nodes: list[list[int]] = [[]]   # index 0 unused: OZids start at 1
    leaves: list[list[int]] = [[]]
    out: list[str] = []

    def chain(cid: int) -> list[int]:
        ids = [cid]
        while len(kids := t.children.get(ids[-1], [])) == 1 and kids[0] not in ids:
            ids.append(kids[0])
        return ids

    def is_tip(ids: list[int]) -> bool:
        return not t.children.get(ids[-1])

    def node(ids: list[int], children: list[list[int]]) -> None:
        nodes.append(ids)
        out.append("(")
        split(sorted(children, key=lambda c: not is_tip(c)))
        out.append(")")

    def split(children: list[list[int]]) -> None:
        emit(children[0])
        if len(children) == 2:
            emit(children[1])
        else:
            nodes.append([])
            out.append("{")
            split(children[1:])
            out.append("}")

    def emit(ids: list[int]) -> None:
        if is_tip(ids):
            leaves.append(ids)
        else:
            node(ids, [chain(k) for k in t.children[ids[-1]]])

    roots = sorted(t.roots(), key=lambda r: t.concepts[r]["label"].lower())
    roots = [r for r in roots if t.children.get(r)] or roots
    if len(roots) == 1 and not is_tip(chain(roots[0])):
        emit(chain(roots[0]))
    else:
        node([], [chain(r) for r in roots])

    ozid = {c: i for i, ids in enumerate(nodes) for c in ids}
    ozid.update({c: -i for i, ids in enumerate(leaves) for c in ids})
    return OzTree("".join(out), nodes, leaves, ozid, _species(t, claimed))


def _species(t: query.Tree, claimed: dict[int, int]) -> dict[int, int]:
    """Claimed species counts, summed upwards where a group has none of its own."""
    out: dict[int, int] = {}
    order, stack = [], list(t.roots())
    while stack:
        x = stack.pop()
        order.append(x)
        stack.extend(t.children.get(x, []))
    for x in reversed(order):
        n = claimed.get(x) or sum(out.get(c, 0) for c in t.children.get(x, []))
        if n:
            out[x] = n
    return out


_CACHE: dict[int, tuple[query.Tree, OzTree]] = {}


def tree_for(con, source: str | None) -> OzTree:
    t = query.tree_for(con, query.resolve_source(con, source)["id"])
    hit = _CACHE.get(t.source_id)
    if hit is None or hit[0] is not t:
        hit = _CACHE[t.source_id] = (t, build(con, t))
    return hit[1]


def completetree_js(oz: OzTree) -> str:
    return f"var rawData = '{oz.raw}';"


def cut_position_map_js(oz: OzTree, threshold: int = CUT_THRESHOLD) -> str:
    return (f"var cut_position_map_json_str = '{json.dumps(_binary_cut_map(oz.raw, threshold))}';\n"
            f"var polytomy_cut_position_map_json_str = "
            f"'{json.dumps(_polytomy_cut_map(oz.raw, threshold))}';\n"
            f"var cut_threshold = {threshold};")


def node_details(con, oz: OzTree, node_ids: list[int], leaf_ids: list[int]) -> dict:
    """The response of OneZoom's API/node_details.json for these OZids. Unknown ids are
    skipped. A call with no ids returns just the column headers."""
    labels = {}
    wanted = {c for i in node_ids if 0 < i < len(oz.nodes) for c in oz.nodes[i]}
    wanted |= {c for i in leaf_ids if 0 < i < len(oz.leaves) for c in oz.leaves[i]}
    if wanted:
        marks = ",".join("?" * len(wanted))
        labels = dict(con.execute(f"SELECT id, label FROM concept WHERE id IN ({marks})",
                                  list(wanted)).fetchall())

    def describe(ids: list[int]) -> tuple[int | None, int | None, str | None]:
        if not ids:
            return None, None, None
        return ids[-1], oz.species.get(ids[0]), " / ".join(labels[c] for c in ids)

    nodes = []
    for i in node_ids:
        if 0 < i < len(oz.nodes):
            ott, spp, name = describe(oz.nodes[i])
            nodes.append([i, ott, spp, None, name] + [None] * (len(NODE_COLS) - 5))
    leaves = []
    for i in leaf_ids:
        if 0 < i < len(oz.leaves):
            ott, spp, name = describe(oz.leaves[i])
            leaves.append([i, ott, spp, name, None, None])
    return {
        "colnames_nodes": {c: i for i, c in enumerate(NODE_COLS)},
        "colnames_leaves": {c: i for i, c in enumerate(LEAF_COLS)},
        "colnames_images": {c: i for i, c in enumerate(IMAGE_COLS)},
        "colnames_reservations": {c: i for i, c in enumerate(RESERVATION_COLS)},
        "nodes": nodes, "leaves": leaves,
        "leafIucn": [], "leafPic": [], "reservations": [],
        "vernacular_by_ott": [], "vernacular_by_name": [], "tours_by_ott": [],
    }


# The two cut maps below are ported from OneZoom tree-build's
# oz_tree_build/utilities/make_js_treefiles.py (MIT licence, © OneZoom developers).
# Keys are the index of a node's closing bracket; values say where its children split.

def _binary_cut_map(s: str, threshold: int) -> dict[int, int]:
    depth, count = [0] * len(s), 0
    for i in range(len(s) - 1, -1, -1):
        count += 1 if s[i] in ")}" else -1
        depth[i] = count
    out: dict[int, int] = {}
    todo = [(0, len(s) - 1)]
    while todo:
        start, end = todo.pop(0)
        for i in range(end - 1, start - 1, -1):
            if depth[i] == depth[end]:
                out[end] = i - 1
                if i - start - 2 >= threshold:
                    todo.append((start + 1, i - 1))
                if end - i - 1 >= threshold:
                    todo.append((i, end - 1))
                break
    return out


def _polytomy_cut_map(s: str, threshold: int) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    todo = [(0, len(s) - 1)]

    def children(start: int, end: int, nested: bool = False) -> list[int]:
        if end <= start or (nested and s[end] == ")"):
            if end - start > threshold:
                todo.append((start, end))
            return [start, end]
        count = 0
        for i in range(end, start - 1, -1):
            count += 1 if s[i] in ")}" else -1
            if count == 1 and s[i] in "({":
                return children(start + 1, i - 1, True) + children(i, end - 1, True)
        return [start, start, end, end]

    while todo:
        start, end = todo.pop(0)
        out[end] = children(start, end)
    return out
