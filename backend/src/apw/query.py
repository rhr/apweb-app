"""Read API over the store. Shared by the CLI and (later) the web API / LLM tools."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from .db import norm


def resolve_source(con: sqlite3.Connection, ref: str | None) -> sqlite3.Row:
    """Accept a source key ('apg4', 'apweb@2026-10-29') or family ('apweb' = latest)."""
    ref = ref or "apweb"
    row = con.execute("SELECT * FROM source WHERE key = ?", (ref,)).fetchone()
    if row is None:
        row = con.execute("SELECT * FROM source WHERE family = ? ORDER BY version DESC LIMIT 1",
                          (ref,)).fetchone()
    if row is None:
        raise LookupError(f"unknown source {ref!r}")
    return row


def sources(con) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT s.*, (SELECT count(*) FROM concept WHERE source_id = s.id) AS n_concepts,"
        " (SELECT count(*) FROM claim WHERE source_id = s.id) AS n_claims FROM source s"
        " ORDER BY family, version").fetchall()


def find(con, name: str, source: str | None = None) -> list[sqlite3.Row]:
    """Concepts whose label or alternative label matches `name` (normalized)."""
    sid = resolve_source(con, source)["id"]
    n = norm(name)
    for cand in (n, *_variants(n)):
        rows = con.execute(
            "SELECT * FROM concept WHERE source_id = ? AND norm = ? "
            "UNION SELECT c.* FROM concept c JOIN concept_label l ON l.concept_id = c.id "
            "WHERE c.source_id = ? AND l.norm = ? AND l.kind != 'anchor'",
            (sid, cand, sid, cand)).fetchall()
        if rows:
            # prefer exact label matches, then non-synonym labels
            return sorted(rows, key=lambda r: (r["norm"] != cand, r["informal"]))
    return []


def _variants(n: str) -> list[str]:
    """'lamiids' <-> 'lamiidae' (informal vs. Linnaean-style clade names)."""
    if n.endswith("ids"):
        return [n[:-3] + "idae"]
    if n.endswith("idae"):
        return [n[:-4] + "ids"]
    return []


def get(con, concept_id: int) -> sqlite3.Row:
    return con.execute("SELECT * FROM concept WHERE id = ?", (concept_id,)).fetchone()


class Tree:
    """In-memory child_of graph for one source. Holds no connection, so it can be cached
    and shared across requests."""

    def __init__(self, con, source_id: int):
        self.source_id = source_id
        self.parent: dict[int, int] = {}
        self.children: dict[int, list[int]] = defaultdict(list)
        for r in con.execute("SELECT subject_id, object_id FROM claim WHERE source_id = ? "
                             "AND predicate = 'child_of'", (source_id,)):
            self.parent[r[0]] = r[1]
            self.children[r[1]].append(r[0])
        self.concepts: dict[int, dict] = {r["id"]: dict(r) for r in con.execute(
            "SELECT * FROM concept WHERE source_id = ?", (source_id,))}
        for kids in self.children.values():
            kids.sort(key=lambda k: self.concepts[k]["label"].lower())

    @cached_property
    def n_descendants(self) -> dict[int, int]:
        out: dict[int, int] = {}
        order, stack = [], list(self.roots())
        while stack:
            x = stack.pop()
            order.append(x)
            stack.extend(self.children.get(x, []))
        for x in reversed(order):
            out[x] = sum(1 + out[c] for c in self.children.get(x, []))
        return out

    def lineage(self, cid: int) -> list[int]:
        out, seen = [cid], {cid}
        while out[-1] in self.parent and self.parent[out[-1]] not in seen:
            out.append(self.parent[out[-1]])
            seen.add(out[-1])
        return out

    def ancestor_at_rank(self, cid: int, rank: str) -> int | None:
        for a in self.lineage(cid)[1:]:
            if self.concepts[a]["rank"] == rank:
                return a
        return None

    def descendants(self, cid: int) -> list[int]:
        out, stack = [], [cid]
        while stack:
            for c in self.children.get(stack.pop(), []):
                out.append(c)
                stack.append(c)
        return out

    def roots(self) -> list[int]:
        return [c for c in self.concepts if c not in self.parent]


_TREES: dict[tuple, Tree] = {}


def tree_for(con, source_id: int) -> Tree:
    """Cached Tree; the key changes whenever the source is re-ingested."""
    stamp = tuple(con.execute("SELECT max(id), count(*) FROM claim WHERE source_id = ? "
                              "AND predicate = 'child_of'", (source_id,)).fetchone())
    key = (source_id, *stamp)
    if key not in _TREES:
        for k in [k for k in _TREES if k[0] == source_id]:
            del _TREES[k]
        _TREES[key] = Tree(con, source_id)
    return _TREES[key]


def claims(con, concept_id: int, predicate: str | None = None) -> list[dict]:
    q = "SELECT * FROM claim WHERE subject_id = ?" + (" AND predicate = ?" if predicate else "")
    rows = con.execute(q + " ORDER BY id", (concept_id, predicate) if predicate else (concept_id,))
    out = []
    for r in rows:
        d = dict(r)
        d["qualifiers"] = json.loads(r["qualifiers"]) if r["qualifiers"] else {}
        out.append(d)
    return out


def literal(con, concept_id: int, predicate: str) -> str | None:
    r = con.execute("SELECT value FROM claim WHERE subject_id = ? AND predicate = ? LIMIT 1",
                    (concept_id, predicate)).fetchone()
    return r[0] if r else None


def citations_for(con, concept_id: int, topic: str | None = None,
                  exact: bool = False) -> list[sqlite3.Row]:
    """References cited where the source discusses this concept (optionally one topic)."""
    q = ("SELECT cl.value AS topic, ci.text, ci.confidence, ci.candidates, r.* FROM claim cl "
         "JOIN citation ci ON ci.claim_id = cl.id LEFT JOIN reference r ON r.id = ci.reference_id "
         "WHERE cl.subject_id = ? AND cl.predicate = 'discussed_in'")
    args: list = [concept_id]
    if topic:
        q += " AND cl.value = ?" if exact else " AND cl.value LIKE ?"
        args.append(topic if exact else f"%{topic}%")
    return con.execute(q + " ORDER BY cl.value, r.cite_year", args).fetchall()


def find_references(con, query: str, limit: int = 20) -> list[sqlite3.Row]:
    """'Magallon 2015', 'Wen et al. 2013', or free text (FTS over citation strings)."""
    m = re.match(r"^\s*([^\d]+?)\s*(?:et al\.?|&.*?|and .*?)?\s*,?\s*((?:1[6-9]|20)\d\d[a-z]?)\s*$", query)
    if m:
        rows = con.execute("SELECT * FROM reference WHERE first_norm = ? AND cite_year LIKE ? "
                           "ORDER BY cite_year LIMIT ?", (norm(m[1]), m[2] + "%", limit)).fetchall()
        if rows:
            return rows
    return con.execute(
        "SELECT r.* FROM search s JOIN reference r ON r.id = s.entity_id "
        "WHERE s.kind = 'reference' AND search MATCH ? ORDER BY rank LIMIT ?",
        (fts_query(query), limit)).fetchall()


def cited_by(con, reference_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT DISTINCT c.*, cl.value AS topic FROM citation ci JOIN claim cl ON cl.id = ci.claim_id "
        "JOIN concept c ON c.id = cl.subject_id WHERE ci.reference_id = ? ORDER BY c.label",
        (reference_id,)).fetchall()


def fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 query (implicit AND of quoted terms)."""
    terms = re.findall(r"[\w+-]+", q)
    return " ".join(f'"{t}"' for t in terms) or '""'


def search(con, q: str, kind: str | None = None, source: str | None = None,
           limit: int = 30) -> list[dict]:
    sql = ("SELECT s.kind, s.entity_id, snippet(search, 2, '«', '»', '…', 12) AS snip, rank "
           "FROM search s WHERE search MATCH ?")
    args: list = [fts_query(q)]
    if kind:
        sql += " AND s.kind = ?"
        args.append(kind)
    rows = con.execute(sql + " ORDER BY rank LIMIT ?", args + [limit * 4]).fetchall()
    sid = resolve_source(con, source)["id"] if source else None
    out = []
    for r in rows:
        if r["kind"] == "concept":
            c = get(con, r["entity_id"])
        elif r["kind"] == "claim":
            cl = con.execute("SELECT * FROM claim WHERE id = ?", (r["entity_id"],)).fetchone()
            c = get(con, cl["subject_id"])
        else:
            c = None
        if sid and (c is None or c["source_id"] != sid):
            continue
        out.append({"kind": r["kind"], "id": r["entity_id"], "concept": c, "snippet": r["snip"]})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- comparisons

@dataclass
class Placement:
    name: str
    a: str | None               # parent (e.g. order) in source a, or how a treats the name
    b: str | None
    a_id: int | None = None     # the taxon's concept in each source
    b_id: int | None = None


def compare_placements(con, a: str, b: str, rank: str = "family",
                       parent_rank: str = "order") -> dict[str, list[Placement]]:
    """Compare where each `rank` taxon sits (its `parent_rank` ancestor) in two sources."""
    sa, sb = resolve_source(con, a), resolve_source(con, b)
    ta, tb = tree_for(con, sa["id"]), tree_for(con, sb["id"])

    def placements(t: Tree) -> dict[str, tuple[str, str | None, int]]:
        out = {}
        for cid, c in t.concepts.items():
            if c["rank"] == rank:
                anc = t.ancestor_at_rank(cid, parent_rank)
                out[c["norm"]] = (c["label"], t.concepts[anc]["label"] if anc else None, cid)
        return out

    def treated_as(t: Tree, n: str) -> tuple[str | None, int | None]:
        """('= Frankenieae (tribe, in Tamaricaceae)', id) when n labels another concept."""
        r = con.execute("SELECT c.id FROM concept c JOIN concept_label l ON l.concept_id = c.id "
                        "WHERE c.source_id = ? AND l.norm = ? AND l.kind != 'anchor' LIMIT 1",
                        (t.source_id, n)).fetchone()
        if not r:
            return None, None
        c = t.concepts[r["id"]]
        fam = t.ancestor_at_rank(c["id"], "family") if c["rank"] != "family" else None
        where = t.concepts[fam]["label"] if fam else None
        return f"= {c['label']} ({c['rank']}{', in ' + where if where else ''})", c["id"]

    pa, pb = placements(ta), placements(tb)
    result: dict[str, list[Placement]] = {"moved": [], "only_a": [], "only_b": [], "same": []}
    for n in sorted(set(pa) | set(pb)):
        if n in pa and n in pb:
            la, lb = pa[n][1], pb[n][1]
            key = "same" if norm(la or "") == norm(lb or "") else "moved"
            result[key].append(Placement(pa[n][0], la, lb, pa[n][2], pb[n][2]))
        elif n in pa:
            how, bid = treated_as(tb, n)
            result["only_a"].append(Placement(pa[n][0], pa[n][1], how, pa[n][2], bid))
        else:
            how, aid = treated_as(ta, n)
            result["only_b"].append(Placement(pb[n][0], how, pb[n][1], aid, pb[n][2]))
    return result


def clade_agreement(con, a: str, b: str) -> dict[str, list[sqlite3.Row]]:
    """Clades of `b` with / without a congruent counterpart in `a` (via the alignment table)."""
    sa, sb = resolve_source(con, a), resolve_source(con, b)
    rows = con.execute(
        "SELECT c.*, (SELECT group_concat(x.label, ' | ') FROM alignment al "
        "  JOIN concept x ON x.id = al.a_id AND x.source_id = ? "
        "  WHERE al.b_id = c.id AND al.relation IN ('congruent', 'same_name')) AS match "
        "FROM concept c WHERE c.source_id = ? AND c.rank = 'clade'",
        (sa["id"], sb["id"])).fetchall()
    return {"matched": [r for r in rows if r["match"]],
            "unmatched": [r for r in rows if not r["match"]]}


def parent_conflicts(con, source: str | None = None, ratio: float = 0.5) -> list[dict]:
    """child_of claims where an alternative parent had comparable support."""
    sid = resolve_source(con, source)["id"]
    out = []
    for r in con.execute(
            "SELECT cl.*, c.label AS child, p.label AS parent FROM claim cl "
            "JOIN concept c ON c.id = cl.subject_id JOIN concept p ON p.id = cl.object_id "
            "WHERE cl.source_id = ? AND cl.predicate = 'child_of' AND cl.qualifiers LIKE '%alternatives%'",
            (sid,)):
        q = json.loads(r["qualifiers"])
        alts = [x for x in q.get("alternatives", []) if x["score"] >= ratio * q["score"]
                and "bracket" in x["methods"]]
        if alts:
            out.append({"child": r["child"], "parent": r["parent"], "score": q["score"],
                        "alternatives": alts, "child_id": r["subject_id"]})
    return sorted(out, key=lambda d: -max(a["score"] for a in d["alternatives"]))
