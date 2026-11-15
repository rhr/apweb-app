"""JSON-ready views over the store, shared by the web API and the MCP server.

Everything returns plain dicts/lists. Concepts are always summarized the same way
(`summary`), so clients can link any concept to /taxon/<id>.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from . import query
from .db import norm

STAT_PREDICATES = ("family_count", "genus_count", "species_count", "distribution")


class NotFound(LookupError):
    pass


def _source_keys(con) -> dict[int, str]:
    return {r["id"]: r["key"] for r in con.execute("SELECT id, key FROM source")}


def summary(c, tree: query.Tree | None = None, keys: dict[int, str] | None = None) -> dict:
    out = {"id": c["id"], "label": c["label"], "rank": c["rank"], "informal": bool(c["informal"]),
           "authorship": c["authorship"], "url": c["url"], "source_id": c["source_id"]}
    if keys:
        out["source"] = keys.get(c["source_id"])
    if tree is not None:
        out["n_children"] = len(tree.children.get(c["id"], ()))
        out["n_descendants"] = tree.n_descendants.get(c["id"], 0)
    return out


def sources(con) -> list[dict]:
    return [{"id": r["id"], "key": r["key"], "family": r["family"], "version": r["version"], "title": r["title"],
             "kind": r["kind"], "url": r["url"], "citation": r["citation"], "license": r["license"],
             "retrieved_at": r["retrieved_at"], "notes": r["notes"],
             "n_concepts": r["n_concepts"], "n_claims": r["n_claims"],
             "roots": _roots(con, r["id"])} for r in query.sources(con)]


def _roots(con, source_id: int) -> list[dict]:
    t = query.tree_for(con, source_id)
    roots = sorted(t.roots(), key=lambda r: -t.n_descendants.get(r, 0))
    return [summary(t.concepts[r], t) for r in roots[:3]]


def resolve(con, ref: str | int, source: str | None = None) -> dict:
    """A concept by id, or by name within a source (default: latest APWeb)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        c = query.get(con, int(ref))
        if c is None:
            raise NotFound(f"no concept with id {ref}")
        return c
    rows = query.find(con, ref, source)
    if not rows:
        raise NotFound(f"no concept named {ref!r} in {query.resolve_source(con, source)['key']}")
    return rows[0]


def taxon(con, ref: str | int, source: str | None = None, max_characters: int | None = None) -> dict:
    c = resolve(con, ref, source)
    cid = c["id"]
    keys = _source_keys(con)
    t = query.tree_for(con, c["source_id"])
    out = summary(c, t, keys)
    out["labels"] = [{"label": r["label"], "kind": r["kind"]} for r in con.execute(
        "SELECT label, kind FROM concept_label WHERE concept_id = ? AND kind != 'anchor' "
        "ORDER BY kind, label", (cid,))]
    out["lineage"] = [summary(t.concepts[a], t) for a in t.lineage(cid)[1:]]
    out["children"] = [summary(t.concepts[k], t) for k in t.children.get(cid, [])]

    claims = query.claims(con, cid)
    by_pred = defaultdict(list)
    for cl in claims:
        by_pred[cl["predicate"]].append(cl)
    out["stats"] = {p: by_pred[p][0]["value"] for p in STAT_PREDICATES if by_pred.get(p)}
    out["synonyms"] = [cl["value"] for cl in by_pred.get("synonym", [])]
    chars = [_character(cl) for cl in by_pred.get("has_character", [])]
    out["n_characters"] = len(chars)
    out["n_apomorphies"] = sum(ch["apomorphy"] for ch in chars)
    out["characters"] = chars[:max_characters] if max_characters is not None else chars
    out["topics"] = [{"topic": r["value"], "n_citations": r["n"]} for r in con.execute(
        "SELECT cl.value, count(ci.id) AS n FROM claim cl JOIN citation ci ON ci.claim_id = cl.id "
        "WHERE cl.subject_id = ? AND cl.predicate = 'discussed_in' GROUP BY cl.id ORDER BY n DESC",
        (cid,))]
    out["ages"] = ages(con, cid, by_pred)
    out["placement"] = _placement(by_pred.get("child_of", []), con)
    out["other_sources"] = other_sources(con, cid, keys)
    return out


AGE_PREDICATES = ("crown_age", "stem_age", "fossil_age")


def ages(con, cid: int, by_pred: dict | None = None) -> list[dict]:
    """Age estimates (Ma) for a clade, one per study and age expression, with citations."""
    if by_pred is None:
        by_pred = defaultdict(list)
        for cl in query.claims(con, cid):
            by_pred[cl["predicate"]].append(cl)
    claims = [cl for p in AGE_PREDICATES for cl in by_pred.get(p, [])]
    if not claims:
        return []
    cites = defaultdict(list)
    ids = [cl["id"] for cl in claims]
    for r in con.execute(
            f"SELECT ci.claim_id, ci.text, ci.confidence, r.id AS rid, r.year, r.cite_year, r.raw, r.doi "
            f"FROM citation ci LEFT JOIN reference r ON r.id = ci.reference_id "
            f"WHERE ci.claim_id IN ({','.join('?' * len(ids))}) ORDER BY ci.id", ids):
        cites[r["claim_id"]].append({
            "text": r["text"], "confidence": r["confidence"],
            "reference": {"id": r["rid"], "year": r["year"], "raw": r["raw"], "doi": r["doi"]}
            if r["rid"] else None})
    out = []
    for cl in claims:
        q = cl["qualifiers"]
        cs = cites.get(cl["id"], [])
        years = [c["reference"]["year"] for c in cs if c["reference"] and c["reference"]["year"]] \
            or [y for c in cs for y in re.findall(r"\b(?:1[6-9]|20)\d\d", c["text"])[:1]]
        out.append({
            "id": cl["id"], "kind": cl["predicate"].removesuffix("_age"),
            "value": float(cl["value"]), "points": q.get("points", []), "form": q.get("form"),
            "older": q.get("older"), "younger": q.get("younger"), "approx": bool(q.get("approx")),
            "text": q.get("text"), "methods": q.get("methods", []),
            "kind_explicit": q.get("kind_explicit"), "subject": q.get("subject"),
            "subject_resolved": q.get("subject_resolved"), "stated_under": q.get("stated_under"),
            "problems": q.get("problems", []), "locator": cl["locator"], "citations": cs,
            "year": max(years) if years else None})
    order = {k: i for i, k in enumerate(("crown", "stem", "fossil"))}
    return sorted(out, key=lambda a: (order[a["kind"]], a["year"] or "9999", -a["value"]))


def _character(cl: dict) -> dict:
    q = cl["qualifiers"]
    return {"text": cl["value"], "apomorphy": bool(q.get("apomorphy")), "apo": q.get("apo", []),
            "uncertain": bool(q.get("uncertain")), "locator": cl["locator"]}


def _placement(child_of: list[dict], con) -> dict | None:
    """How the parent was chosen, and competing parents (APWeb pages that disagree)."""
    if not child_of:
        return None
    cl = child_of[0]
    q = cl["qualifiers"]
    alts = []
    for a in q.get("alternatives", []):
        alts.append({"parent": a["parent"], "score": a["score"], "methods": a["methods"]})
    return {"parent_id": cl["object_id"], "score": q.get("score"), "methods": q.get("methods"),
            "basis": q.get("basis"), "alternatives": alts, "locator": cl["locator"]}


def other_sources(con, cid: int, keys: dict[int, str] | None = None) -> list[dict]:
    """The same taxon in other sources, with where each source places it."""
    keys = keys or _source_keys(con)
    out = []
    for r in con.execute(
            "SELECT x.*, group_concat(a.relation) AS relations FROM alignment a "
            "JOIN concept x ON x.id = a.b_id WHERE a.a_id = ? GROUP BY x.id", (cid,)):
        t = query.tree_for(con, r["source_id"])
        lin = t.lineage(r["id"])
        parent = t.concepts[lin[1]] if len(lin) > 1 else None
        order = t.ancestor_at_rank(r["id"], "order")
        out.append({**summary(r, t, keys), "relations": sorted(set(r["relations"].split(","))),
                    "parent": summary(parent) if parent else None,
                    "order": summary(t.concepts[order]) if order else None})
    return sorted(out, key=lambda d: d["source"] or "")


def subtree(con, ref: str | int, source: str | None = None, depth: int = 2,
            named_only: bool = False, max_children: int = 500) -> dict:
    """Nested subtree for the phylogeny viewer. With named_only, informal (bracketed)
    clades are skipped and their members promoted, giving a classification view."""
    c = resolve(con, ref, source)
    t = query.tree_for(con, c["source_id"])

    def kids(cid: int) -> list[int]:
        out = []
        for k in t.children.get(cid, []):
            if named_only and t.concepts[k]["informal"]:
                out.extend(kids(k))
            else:
                out.append(k)
        return out

    def build(cid: int, d: int) -> dict:
        node = summary(t.concepts[cid], t)
        ks = kids(cid)
        node["n_children"] = len(ks)
        if d > 0 and ks:
            node["children"] = [build(k, d - 1) for k in ks[:max_children]]
            node["truncated"] = len(ks) > max_children
        return node

    out = build(c["id"], depth)
    out["lineage"] = [summary(t.concepts[a]) for a in t.lineage(c["id"])[1:]]
    return out


def citations(con, ref: str | int, source: str | None = None, topic: str | None = None,
              exact: bool = False) -> dict:
    c = resolve(con, ref, source)
    groups: dict[str, list] = defaultdict(list)
    for r in query.citations_for(con, c["id"], topic, exact):
        groups[r["topic"]].append({
            "text": r["text"], "confidence": r["confidence"], "candidates": r["candidates"],
            "reference": _reference(r) if r["id"] is not None else None})
    return {"concept": summary(c), "topics": [{"topic": k, "citations": v} for k, v in groups.items()]}


def _reference(r) -> dict:
    return {"id": r["id"], "raw": r["raw"], "authors": r["authors"], "year": r["year"],
            "cite_year": r["cite_year"], "title": r["title"], "container": r["container"],
            "volume": r["volume"], "pages": r["pages"], "doi": r["doi"], "url": r["url"]}


def reference(con, reference_id: int) -> dict:
    r = con.execute("SELECT * FROM reference WHERE id = ?", (reference_id,)).fetchone()
    if r is None:
        raise NotFound(f"no reference with id {reference_id}")
    by: dict[int, dict] = {}
    for x in query.cited_by(con, reference_id):
        d = by.setdefault(x["id"], {**summary(x), "topics": []})
        d["topics"].append(x["topic"])
    return {**_reference(r), "cited_by": sorted(by.values(), key=lambda d: d["label"])}


def find_references(con, q: str, limit: int = 20) -> list[dict]:
    out = []
    for r in query.find_references(con, q, limit):
        n = con.execute("SELECT count(DISTINCT cl.subject_id) FROM citation ci JOIN claim cl "
                        "ON cl.id = ci.claim_id WHERE ci.reference_id = ?", (r["id"],)).fetchone()[0]
        out.append({**_reference(r), "n_taxa": n})
    return out


def search(con, q: str, kind: str | None = None, source: str | None = None,
           limit: int = 20) -> list[dict]:
    keys = _source_keys(con)
    out = []
    # exact name matches first: typing 'Vitaceae' should land on Vitaceae
    if kind in (None, "concept"):
        for c in query.find(con, q, source):
            out.append({"kind": "concept", "concept": summary(c, keys=keys), "snippet": c["label"]})
    seen = {h["concept"]["id"] for h in out}
    for h in query.search(con, q, kind, source, limit * 2):
        c = h["concept"]
        if h["kind"] == "concept" and c and c["id"] in seen:
            continue
        if c is not None:
            seen.add(c["id"])
        item = {"kind": h["kind"], "snippet": h["snippet"],
                "concept": summary(c, keys=keys) if c else None}
        if h["kind"] == "reference":
            item["reference_id"] = h["id"]
        out.append(item)
        if len(out) >= limit:
            break
    return out[:limit]


def suggest(con, prefix: str, source: str | None = None, limit: int = 12) -> list[dict]:
    """Name autocomplete within one source."""
    sid = query.resolve_source(con, source)["id"]
    n = norm(prefix)
    if not n:
        return []
    rows = con.execute(
        "SELECT DISTINCT c.* FROM concept c LEFT JOIN concept_label l ON l.concept_id = c.id "
        "AND l.kind != 'anchor' WHERE c.source_id = ? AND (c.norm LIKE ? OR l.norm LIKE ?) "
        "ORDER BY c.informal, length(c.label) LIMIT ?",
        (sid, n + "%", n + "%", limit)).fetchall()
    t = query.tree_for(con, sid)
    return [summary(r, t) for r in rows]


def compare(con, a: str, b: str, rank: str = "family") -> dict:
    parent_rank = "order" if rank == "family" else "clade"
    res = query.compare_placements(con, a, b, rank, parent_rank)
    rows = {k: [vars(p) for p in v] for k, v in res.items()}
    return {"a": a, "b": b, "rank": rank, "parent_rank": parent_rank,
            "counts": {k: len(v) for k, v in rows.items()}, **rows}


def clades(con, a: str, b: str) -> dict:
    res = query.clade_agreement(con, a, b)
    tb = query.tree_for(con, query.resolve_source(con, b)["id"])

    def row(r) -> dict:
        orders = sorted(tb.concepts[d]["label"] for d in tb.descendants(r["id"])
                        if tb.concepts[d]["rank"] == "order")
        return {**summary(r, tb), "match": r["match"], "orders": orders}

    matched = [row(r) for r in res["matched"]]
    unmatched = [row(r) for r in res["unmatched"]]
    return {"a": a, "b": b, "matched": matched, "unmatched": unmatched}


def conflicts(con, source: str | None = None, limit: int = 200) -> list[dict]:
    return query.parent_conflicts(con, source)[:limit]


def overview(con) -> dict:
    one = lambda q: con.execute(q).fetchone()[0]
    tot = one("SELECT count(*) FROM citation")
    res = one("SELECT count(*) FROM citation WHERE reference_id IS NOT NULL")
    return {"sources": sources(con),
            "counts": {"concepts": one("SELECT count(*) FROM concept"),
                       "claims": one("SELECT count(*) FROM claim"),
                       "references": one("SELECT count(*) FROM reference"),
                       "citations": tot, "citations_resolved": res}}
