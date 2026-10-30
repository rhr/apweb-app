"""MCP server exposing the knowledge store to AI clients (`apw mcp`, stdio transport).

Example client config (Claude Code):
    claude mcp add apw -- /path/to/backend/.venv/bin/apw mcp
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import tools

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
                   instructions=INSTRUCTIONS, version="0.3.0")
for fn in tools.ALL:
    server.add_tool(fn)


def main() -> None:
    server.run("stdio")
