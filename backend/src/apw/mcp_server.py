"""MCP server exposing the knowledge store to AI clients (`apw mcp`, stdio transport).

Example client config (Claude Code):
    claude mcp add apw -- /path/to/backend/.venv/bin/apw mcp
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import db, service

INSTRUCTIONS = """\
Knowledge store of angiosperm (and other land plant) phylogeny with provenance.
Sources: 'apweb' (Stevens' Angiosperm Phylogeny Website, latest snapshot) and the APG
classifications 'apg1' (1998), 'apg2' (2003), 'apg3' (2009), 'apg4' (2016). Each source has
its own taxon concepts; the same name in two sources is linked as 'same_name', and clades
with identical order-level membership as 'congruent'. Taxa can be given by name (resolved
within `source`, default 'apweb') or by numeric concept id.

APWeb characterizations are lists of character states; 'apomorphy': true marks states
APWeb bolds as possible apomorphies; 'uncertain' marks states in parentheses. Literature is
grouped by APWeb's discussion topics (Age, Evolution/..., Phylogeny, Classification, ...).
Always cite the source (and APWeb URL or reference) for facts you report."""

server = MCPServer(name="apw", title="Angiosperm phylogeny knowledge store",
                   instructions=INSTRUCTIONS, version="0.2.0")


def _run(fn, *args, **kw) -> Any:
    con = db.connect_ro()
    try:
        return fn(con, *args, **kw)
    except LookupError as e:
        return {"error": str(e).strip("'\"")}
    finally:
        con.close()


@server.tool()
def list_sources() -> list[dict]:
    """List the sources in the store (APWeb snapshots, APG versions) with citation and license."""
    return _run(service.sources)


@server.tool()
def search(query: str, kind: str | None = None, source: str | None = None, limit: int = 15) -> list[dict]:
    """Full-text search over taxon names, character statements, discussion topics and
    references. kind: 'concept' | 'claim' | 'reference' (default: all)."""
    return _run(service.search, query, kind, source, min(limit, 50))


@server.tool()
def get_taxon(name: str, source: str | None = None, max_characters: int = 40) -> dict:
    """Everything known about one taxon or clade in one source: rank, authority, lineage,
    children, genus/species counts, distribution, synonyms, characterization (with
    apomorphy flags), literature topics, competing placements, and the same taxon in
    other sources (with each source's placement)."""
    return _run(service.taxon, name, source, max_characters)


@server.tool()
def get_subtree(name: str, source: str | None = None, depth: int = 2, named_only: bool = False) -> dict:
    """The subtree below a taxon to `depth` levels. named_only=True skips informal
    (bracketed) clades, giving a Linnaean-style classification."""
    return _run(service.subtree, name, source, min(depth, 4), named_only, 200)


@server.tool()
def get_literature(name: str, source: str | None = None, topic: str | None = None) -> dict:
    """References cited where APWeb discusses a taxon, grouped by topic (e.g. 'Age',
    'Phylogeny', 'Evolution', 'Chemistry'). Unresolved in-text citations are included
    with their candidate count."""
    return _run(service.citations, name, source, topic)


@server.tool()
def find_references(query: str, limit: int = 10) -> list[dict]:
    """Find bibliography entries, e.g. 'Magallon et al. 2015' or free text from a title."""
    return _run(service.find_references, query, min(limit, 30))


@server.tool()
def get_reference(reference_id: int) -> dict:
    """A bibliography entry and the taxa/topics where it is cited."""
    return _run(service.reference, reference_id)


@server.tool()
def compare_classifications(a: str, b: str, rank: str = "family") -> dict:
    """Compare where each family (rank='family', placed in orders) or order (rank='order',
    placed in clades) sits in two sources, e.g. a='apg3', b='apg4'. Returns moved taxa,
    taxa only in one source (with how the other source treats the name, if as a synonym
    or lower-rank group), and unchanged taxa."""
    return _run(service.compare, a, b, rank)


@server.tool()
def clade_agreement(a: str, b: str) -> dict:
    """Which clades of source b have a clade with exactly the same orders in source a."""
    return _run(service.clades, a, b)


@server.tool()
def list_conflicts(source: str | None = None, limit: int = 50) -> list[dict]:
    """Placements where a source contradicts itself (e.g. APWeb pages giving different
    bracketed topologies), with the competing parents and their support."""
    return _run(service.conflicts, source, min(limit, 200))


def main() -> None:
    server.run("stdio")
