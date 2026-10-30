"""Align concepts across sources.

  same_name   labels (or alternative labels) match after normalization
  congruent   both clades contain exactly the same set of orders (counting only
              orders recognized by both sources), regardless of what they're called
"""

from __future__ import annotations

from collections import defaultdict

from .query import Tree


def align(con) -> dict[str, int]:
    con.execute("DELETE FROM alignment WHERE method IN ('name', 'circumscription')")
    srcs = [r["id"] for r in con.execute("SELECT id FROM source")]
    stats = defaultdict(int)

    # names
    by_norm: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r in con.execute("SELECT c.id, c.source_id, c.norm FROM concept c WHERE c.informal = 0 "
                         "UNION SELECT c.id, c.source_id, l.norm FROM concept c "
                         "JOIN concept_label l ON l.concept_id = c.id WHERE l.kind = 'alternative' "
                         "AND c.informal = 0"):
        by_norm[r["norm"]].append((r["id"], r["source_id"]))
    pairs = set()
    for items in by_norm.values():
        for a, sa in items:
            for b, sb in items:
                if sa != sb:
                    pairs.add((a, b))
    con.executemany("INSERT OR IGNORE INTO alignment(a_id, b_id, relation, method) "
                    "VALUES (?, ?, 'same_name', 'name')", sorted(pairs))
    stats["same_name"] = len(pairs)

    # circumscriptions in terms of orders
    trees = {s: Tree(con, s) for s in srcs}
    orders = {s: {cid: t.concepts[cid]["norm"] for cid in t.concepts
                  if t.concepts[cid]["rank"] == "order"} for s, t in trees.items()}
    circ: dict[int, dict[int, frozenset]] = {}
    for s, t in trees.items():
        circ[s] = {}
        for cid, c in t.concepts.items():
            if c["rank"] in ("clade", "superorder", "subclass"):
                ds = frozenset(orders[s][d] for d in t.descendants(cid) if d in orders[s])
                if len(ds) >= 2:
                    circ[s][cid] = ds
    cpairs = set()
    for sa in srcs:
        for sb in srcs:
            if sa >= sb:
                continue
            shared = set(orders[sa].values()) & set(orders[sb].values())
            index: dict[frozenset, list[int]] = defaultdict(list)
            for cid, ds in circ[sb].items():
                k = ds & shared
                if len(k) >= 2:
                    index[frozenset(k)].append(cid)
            for cid, ds in circ[sa].items():
                k = frozenset(ds & shared)
                for other in index.get(k, []):
                    cpairs.add((cid, other))
                    cpairs.add((other, cid))
    con.executemany("INSERT OR IGNORE INTO alignment(a_id, b_id, relation, method) "
                    "VALUES (?, ?, 'congruent', 'circumscription')", sorted(cpairs))
    stats["congruent"] = len(cpairs)
    con.commit()
    return dict(stats)
