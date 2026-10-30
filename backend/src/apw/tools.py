"""Query tools for AI clients, defined once and used by both the MCP server and the
in-app question answering agent (`apw.ask`).

Each tool is a typed function; its signature and docstring are its schema and
description. Outputs are shaped for a model's context: long lists are capped, with
counts of what was left out.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from . import db, service


def _run(fn, *args, **kw) -> Any:
    con = db.connect_ro()
    try:
        return fn(con, *args, **kw)
    except LookupError as e:
        return {"error": str(e).strip("'\"")}
    finally:
        con.close()


def _cap(items: list, n: int, what: str) -> dict:
    out = {what: items[:n]}
    if len(items) > n:
        out[f"{what}_omitted"] = len(items) - n
    return out


def list_sources() -> list[dict]:
    """List the sources in the store (APWeb snapshots, APG versions) with citation and license."""
    return [{k: s[k] for k in ("key", "title", "kind", "citation", "license", "notes", "n_concepts")}
            for s in _run(service.sources)]


def search(query: str, kind: Literal["concept", "claim", "reference"] | None = None,
           source: str | None = None, limit: int = 15) -> list[dict] | dict:
    """Full-text search over taxon names, character statements, discussion topics and
    references. kind: 'concept' (taxa), 'claim' (statements, e.g. characters), or
    'reference'; default all. source: e.g. 'apweb' (default), 'apg4'."""
    return _run(service.search, query, kind, source, max(1, min(limit, 40)))


def get_taxon(name: str, source: str | None = None, max_characters: int = 40) -> dict:
    """Everything known about one taxon or clade in one source: rank, authority, lineage,
    children, genus/species counts, distribution, synonyms, characterization (character
    states, with 'apomorphy': true for possible apomorphies), literature topics with
    citation counts, competing placements, and the same taxon in other sources with each
    source's placement. `name` may also be a numeric concept id."""
    t = _run(service.taxon, name, source, max(0, min(max_characters, 200)))
    if "error" in t:
        return t
    # lineage: named groups only, plus the immediate parent
    lin = t["lineage"]
    t["lineage"] = [c for i, c in enumerate(lin) if i == 0 or not c["informal"]]
    for c in [*t["lineage"], *t["children"]]:
        for k in ("url", "source_id", "authorship"):
            c.pop(k, None)
    return t


def get_subtree(name: str, source: str | None = None, depth: int = 2,
                named_only: bool = False) -> dict:
    """The subtree below a taxon, `depth` levels deep (max 4). named_only=True skips
    informal (bracketed) clades, giving a Linnaean-style classification."""
    t = _run(service.subtree, name, source, max(0, min(depth, 4)), named_only, 150)

    def slim(n: dict) -> dict:
        out = {k: n[k] for k in ("id", "label", "rank", "n_children", "n_descendants") if k in n}
        if n.get("children"):
            out["children"] = [slim(c) for c in n["children"]]
        return out

    return t if "error" in t else slim(t)


def get_literature(name: str, source: str | None = None, topic: str | None = None,
                   max_per_topic: int = 40) -> dict:
    """References cited where APWeb discusses a taxon, grouped by discussion topic (e.g.
    'Age', 'Phylogeny', 'Evolution/Divergence & Distribution', 'Chemistry, Morphology,
    etc'); `topic` filters by substring. Each citation has the in-text form and, when
    resolved, the full reference with id and DOI. For heavily discussed taxa, a call
    without `topic` returns only the topics and their citation counts."""
    r = _run(service.citations, name, source, topic)
    if "error" in r:
        return r
    concept = {k: r["concept"][k] for k in ("id", "label", "rank", "url")}
    if topic is None and sum(len(g["citations"]) for g in r["topics"]) > 150:
        return {"concept": concept,
                "note": "Many citations; call again with `topic` for the references.",
                "topics": [{"topic": g["topic"], "n_citations": len(g["citations"])}
                           for g in r["topics"]]}
    topics = []
    for g in r["topics"]:
        cites = []
        for c in g["citations"]:
            ref = c["reference"]
            cites.append({"cited_as": c["text"],
                          **({"reference_id": ref["id"], "reference": ref["raw"], "doi": ref["doi"]}
                             if ref else {"unresolved": True, "candidates": c["candidates"]}),
                          **({"match_confidence": c["confidence"]} if ref and c["confidence"] < 1 else {})})
        topics.append({"topic": g["topic"], **_cap(cites, max(1, min(max_per_topic, 100)), "citations")})
    return {"concept": concept, "topics": topics}


def find_references(query: str, limit: int = 10) -> list[dict] | dict:
    """Find bibliography entries, e.g. 'Magallon et al. 2015' or words from a title."""
    return _run(service.find_references, query, max(1, min(limit, 30)))


def get_reference(reference_id: int) -> dict:
    """A bibliography entry and the taxa and topics where it is cited."""
    r = _run(service.reference, reference_id)
    if "error" in r:
        return r
    cited = [{"id": c["id"], "label": c["label"], "rank": c["rank"], "topics": c["topics"]}
             for c in r["cited_by"]]
    return {**{k: v for k, v in r.items() if k != "cited_by"}, **_cap(cited, 80, "cited_by")}


def compare_classifications(a: str, b: str, rank: Literal["family", "order"] = "family",
                            include_unchanged: bool = False) -> dict:
    """Compare where each family (rank='family', placed in orders) or order (rank='order',
    placed in clades) sits in two sources, e.g. a='apg3', b='apg4' or a='apg4', b='apweb'.
    Returns taxa placed differently, taxa only in one source (with how the other source
    treats the name, e.g. as a synonym or a lower-rank group), and counts."""
    r = _run(service.compare, a, b, rank)
    if "error" in r:
        return r
    if not include_unchanged:
        r.pop("same", None)
    return r


def clade_agreement(a: str, b: str) -> dict:
    """Which clades of source b have a clade in source a with exactly the same member
    orders, and which do not (with each clade's orders)."""
    r = _run(service.clades, a, b)
    if "error" in r:
        return r
    slim = lambda row: {"id": row["id"], "label": row["label"], "match": row["match"],
                        "orders": row["orders"]}
    return {"a": a, "b": b, "matched": [{k: v for k, v in slim(x).items() if k != "orders"}
                                        for x in r["matched"]],
            "unmatched": [slim(x) for x in r["unmatched"]]}


def list_conflicts(source: str | None = None, limit: int = 30) -> list[dict] | dict:
    """Placements where a source contradicts itself (e.g. APWeb pages giving different
    bracketed topologies), with the competing parents and their support."""
    return _run(service.conflicts, source, max(1, min(limit, 200)))


ALL: list[Callable[..., Any]] = [list_sources, search, get_taxon, get_subtree, get_literature,
                                 find_references, get_reference, compare_classifications,
                                 clade_agreement, list_conflicts]
BY_NAME = {f.__name__: f for f in ALL}
