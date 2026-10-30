"""Command-line interface: `apw --help`."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape as E
from rich.table import Table
from rich.tree import Tree as RichTree

from . import align as align_mod
from . import db, query

app = typer.Typer(help="Angiosperm phylogeny knowledge store.", no_args_is_help=True,
                  add_completion=False)
console = Console()

SourceOpt = typer.Option(None, "--source", "-s", help="Source key or family (default: apweb).")


def _con():
    con = db.connect()
    db.init(con)
    return con


def _one(con, name: str, source: Optional[str]):
    rows = query.find(con, name, source)
    if not rows:
        console.print(f"[red]No concept named {E(repr(name))} in {query.resolve_source(con, source)['key']}[/]")
        hits = query.search(con, name, kind="concept", source=source, limit=8)
        if hits:
            console.print("Did you mean: " + E(", ".join(h["concept"]["label"] for h in hits)))
        raise typer.Exit(1)
    if len(rows) > 1:
        console.print(f"[dim]{len(rows)} matches; showing {E(rows[0]['label'])} "
                      f"(others: {E(', '.join(r['label'] for r in rows[1:4]))})[/]")
    return rows[0]


# ---------------------------------------------------------------- pipeline

@app.command()
def crawl(source: str = typer.Argument("apweb"), snapshot: Optional[str] = None,
          delay: float = typer.Option(2.0, help="Seconds between requests."),
          force: bool = False):
    """Fetch raw pages into the local cache (data/raw/...)."""
    if source == "apweb":
        from .sources.apweb.crawl import crawl as run
        console.print(f"cached to {run(snapshot, delay=delay, force=force)}")
    elif source == "apg":
        from .sources.apg.ingest import VERSIONS, fetch
        for key in VERSIONS:
            d = fetch(key, force=force)
            console.print(f"{key}: revision {d['revid']}")
    else:
        raise typer.BadParameter("source must be 'apweb' or 'apg'")


@app.command()
def ingest(source: str = typer.Argument("all", help="apweb | apg | all"),
           snapshot: Optional[str] = typer.Option(None, help="APWeb snapshot date.")):
    """Parse cached raw data into the store, then align sources."""
    con = _con()
    if source in ("apweb", "all"):
        from .sources.apweb.ingest import ingest as run
        console.print("apweb:", run(snapshot, con))
    if source in ("apg", "all"):
        from .sources.apg.ingest import ingest as run
        for k, v in run(con=con).items():
            console.print(f"{k}:", v)
    console.print("alignment:", align_mod.align(con))


@app.command()
def align():
    """Recompute cross-source concept alignments."""
    console.print(align_mod.align(_con()))


@app.command()
def sources():
    """List sources in the store."""
    t = Table("key", "title", "kind", "concepts", "claims", "retrieved")
    for r in query.sources(_con()):
        t.add_row(r["key"], r["title"], r["kind"], str(r["n_concepts"]), str(r["n_claims"]),
                  (r["retrieved_at"] or "")[:10])
    console.print(t)


# ---------------------------------------------------------------- browsing

@app.command()
def show(name: str, source: Optional[str] = SourceOpt,
         chars: int = typer.Option(8, help="Max characters to list.")):
    """Summary of a taxon/clade: placement, stats, apomorphies, literature."""
    con = _con()
    c = _one(con, name, source)
    tree = query.tree_for(con, c["source_id"])
    console.print(f"[bold]{E(c['label'])}[/] {E(c['authorship'] or '')}  [dim]{c['rank']}"
                  f"{' (informal)' if c['informal'] else ''}[/]")
    if c["url"]:
        console.print(f"[link={c['url']}]{c['url']}[/link]")
    alts = [r["label"] for r in con.execute(
        "SELECT label FROM concept_label WHERE concept_id = ? AND kind != 'anchor'", (c["id"],))]
    if alts:
        console.print("[dim]also:[/] " + E("; ".join(alts[:6])))
    lin = [tree.concepts[x]["label"] for x in tree.lineage(c["id"])[1:]]
    console.print("[dim]placement:[/] " + E(" < ".join(lin[:6])) + (" < …" if len(lin) > 6 else ""))
    kids = sorted(tree.concepts[k]["label"] for k in tree.children.get(c["id"], []))
    if kids:
        console.print(f"[dim]children ({len(kids)}):[/] " + E(", ".join(kids[:15]))
                      + (" …" if len(kids) > 15 else ""))
    stats = {p: query.literal(con, c["id"], p) for p in
             ("family_count", "genus_count", "species_count", "distribution")}
    line = " · ".join(f"{k.split('_')[0]}: {v}" for k, v in stats.items() if v)
    if line:
        console.print(E(line))
    cs = query.claims(con, c["id"], "has_character")
    if cs:
        apo = [x for x in cs if x["qualifiers"].get("apomorphy")]
        console.print(f"\n[bold]Characterization[/] ({len(cs)} statements, {len(apo)} with possible apomorphies)")
        for x in (apo or cs)[:chars]:
            console.print(f"  • {_fmt_char(x)}")
    cites = query.citations_for(con, c["id"])
    if cites:
        topics: dict[str, int] = {}
        for r in cites:
            topics[r["topic"]] = topics.get(r["topic"], 0) + 1
        console.print(f"\n[bold]Literature[/] ({len(cites)} citations): " +
                      E(", ".join(f"{t} ({n})" for t, n in sorted(topics.items(), key=lambda x: -x[1]))))
    al = con.execute(
        "SELECT s.key, x.label, a.relation FROM alignment a JOIN concept x ON x.id = a.b_id "
        "JOIN source s ON s.id = x.source_id WHERE a.a_id = ? ORDER BY s.key", (c["id"],)).fetchall()
    if al:
        console.print("\n[dim]in other sources:[/] " + E("; ".join(
            f"{r['key']}: {r['label']} ({r['relation']})" for r in al[:8])))


def _fmt_char(x: dict) -> str:
    t = E(x["value"])
    for a in x["qualifiers"].get("apo", []):
        t = t.replace(E(a), f"[bold]{E(a)}[/]", 1)
    if x["qualifiers"].get("uncertain"):
        t += " [dim](uncertain)[/]"
    return t


@app.command()
def lineage(name: str, source: Optional[str] = SourceOpt):
    """Path from a taxon to the root."""
    con = _con()
    c = _one(con, name, source)
    tree = query.tree_for(con, c["source_id"])
    for i, x in enumerate(tree.lineage(c["id"])):
        r = tree.concepts[x]
        console.print(f"{E(r['label'])} [dim]{r['rank']}[/]")


@app.command()
def children(name: str, source: Optional[str] = SourceOpt,
             depth: int = typer.Option(2, help="Levels to show.")):
    """Subtree below a taxon."""
    con = _con()
    c = _one(con, name, source)
    tree = query.tree_for(con, c["source_id"])

    def add(node, cid, d):
        for k in sorted(tree.children.get(cid, []), key=lambda k: tree.concepts[k]["label"]):
            r = tree.concepts[k]
            n = node.add(f"{E(r['label'])} [dim]{r['rank']}[/]")
            if d > 1:
                add(n, k, d - 1)

    root = RichTree(f"[bold]{E(c['label'])}[/]")
    add(root, c["id"], depth)
    console.print(root)


@app.command()
def chars(name: str, source: Optional[str] = SourceOpt,
          apo: bool = typer.Option(False, "--apo", help="Only possible apomorphies.")):
    """Characterization of a taxon (bold = possible apomorphy)."""
    con = _con()
    c = _one(con, name, source)
    for x in query.claims(con, c["id"], "has_character"):
        if not apo or x["qualifiers"].get("apomorphy"):
            console.print(f"• {_fmt_char(x)}")


@app.command()
def refs(name: str, source: Optional[str] = SourceOpt,
         topic: Optional[str] = typer.Option(None, help="e.g. Age, Phylogeny, Evolution")):
    """Literature cited for a taxon, by topic."""
    con = _con()
    c = _one(con, name, source)
    cur = None
    for r in query.citations_for(con, c["id"], topic):
        if r["topic"] != cur:
            cur = r["topic"]
            console.print(f"\n[bold]{E(cur)}[/]")
        if r["raw"]:
            doi = f" doi:{r['doi']}" if r["doi"] else ""
            conf = f" [dim](match confidence {r['confidence']:.1f})[/]" if r["confidence"] < 1 else ""
            console.print(f"  • {E(r['raw'][:220])}{E(doi)}{conf}")
        else:
            console.print(f"  • [yellow]{E(r['text'])}[/] [dim](unresolved, {r['candidates']} candidates)[/]")


@app.command()
def cites(reference: str, limit: int = 10):
    """Which taxa cite a work, e.g. `apw cites "Magallon et al. 2015"`."""
    con = _con()
    for r in query.find_references(con, reference, limit):
        console.print(f"[bold]{E(r['raw'][:200])}[/]")
        rows = query.cited_by(con, r["id"])
        by: dict[str, list[str]] = {}
        for x in rows:
            by.setdefault(x["label"], []).append(x["topic"])
        for label, topics in list(by.items())[:30]:
            console.print(f"   {E(label)} [dim]({E(', '.join(sorted(set(topics))))})[/]")
        if not rows:
            console.print("   [dim]not cited in taxon discussions[/]")


@app.command()
def search(q: str, kind: Optional[str] = typer.Option(None, help="concept | claim | reference"),
           source: Optional[str] = typer.Option(None, "--source", "-s"), limit: int = 20):
    """Full-text search over names, characters, topics and references."""
    con = _con()
    for h in query.search(con, q, kind, source, limit):
        who = h["concept"]["label"] if h["concept"] else ""
        console.print(f"[dim]{h['kind']:9}[/] [bold]{E(who)}[/] {E(h['snippet']).replace('«', '[reverse]').replace('»', '[/reverse]')}")


# ---------------------------------------------------------------- comparison & QA

@app.command()
def compare(a: str, b: str, rank: str = typer.Option("family", help="family | order"),
            show_same: bool = False):
    """Compare placements between two sources, e.g. `apw compare apg3 apg4`."""
    con = _con()
    parent_rank = "order" if rank == "family" else "clade"
    res = query.compare_placements(con, a, b, rank, parent_rank)
    console.print(f"{rank}: {len(res['same'])} same, {len(res['moved'])} moved, "
                  f"{len(res['only_a'])} only in {a}, {len(res['only_b'])} only in {b}")
    sections = [("moved", "Moved"), ("only_a", f"Only in {a}"), ("only_b", f"Only in {b}")]
    if show_same:
        sections.append(("same", "Same"))
    for key, title in sections:
        if res[key]:
            t = Table(title=title)
            t.add_column(rank)
            t.add_column(f"{parent_rank} in {a}")
            t.add_column(f"{parent_rank} in {b}")
            for p in res[key]:
                t.add_row(E(p.name), E(p.a or ""), E(p.b or ""))
            console.print(t)


@app.command()
def clades(a: str, b: str):
    """Which clades of B have a congruent clade in A (same set of orders)."""
    con = _con()
    res = query.clade_agreement(con, a, b)
    console.print(f"{len(res['matched'])} of {len(res['matched']) + len(res['unmatched'])} "
                  f"{b} clades have a counterpart in {a}")
    for r in res["unmatched"]:
        console.print(f"  [yellow]no match[/] {E(r['label'])}")


@app.command()
def conflicts(source: Optional[str] = SourceOpt, limit: int = 40):
    """Placements where the source itself is inconsistent (e.g. between APWeb pages)."""
    con = _con()
    rows = query.parent_conflicts(con, source)
    console.print(f"{len(rows)} placements with competing bracket-supported parents")
    for d in rows[:limit]:
        alts = "; ".join(f"{x['parent']} ({x['score']:g})" for x in d["alternatives"])
        console.print(f"[bold]{E(d['child'])}[/] → {E(d['parent'])} ({d['score']:g})  [dim]vs[/] {E(alts)}")


@app.command()
def stats():
    """Data quality summary."""
    con = _con()
    one = lambda q, *a: con.execute(q, a).fetchone()[0]
    console.print(f"concepts {one('SELECT count(*) FROM concept')}, claims {one('SELECT count(*) FROM claim')}, "
                  f"references {one('SELECT count(*) FROM reference')}")
    tot = one("SELECT count(*) FROM citation")
    res = one("SELECT count(*) FROM citation WHERE reference_id IS NOT NULL")
    console.print(f"citations {tot}, resolved {res} ({100 * res / max(tot, 1):.1f}%)")
    t = Table("source", "rank", "count")
    for r in con.execute("SELECT s.key, c.rank, count(*) FROM concept c JOIN source s ON s.id = c.source_id "
                         "WHERE c.rank IN ('order', 'family') GROUP BY 1, 2 ORDER BY 1, 2"):
        t.add_row(*map(str, r))
    console.print(t)
    try:
        res = query.compare_placements(con, "apg4", "apweb")
        console.print(f"APG IV families also in APWeb: {len(res['same']) + len(res['moved'])}/416; "
                      f"same order: {len(res['same'])}; different: {len(res['moved'])}; "
                      f"missing from APWeb: {len(res['only_a'])}")
    except LookupError:
        pass


# ---------------------------------------------------------------- servers

@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the HTTP API (and the built frontend, if present). Docs at /api/docs."""
    import uvicorn
    uvicorn.run("apw.api:app", host=host, port=port, reload=reload)


@app.command()
def mcp():
    """Run the MCP server on stdio, for AI clients (e.g. `claude mcp add apw -- apw mcp`)."""
    from .mcp_server import main
    main()


if __name__ == "__main__":
    app()
