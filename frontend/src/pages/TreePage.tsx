import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type TreeNode } from "../api";
import { PhyloTree } from "../components/PhyloTree";
import { ConceptLink, RankBadge, SourceBadge, Status, sourceKey, useAsync, useSources } from "../lib";

/** /tree → default root of the current source; /tree/:id → rooted at a concept. */
export function TreePage() {
  const { id } = useParams();
  const { sources, current } = useSources();
  const [params, setParams] = useSearchParams();
  const namedOnly = params.get("named") === "1";
  const nav = useNavigate();

  // without an id, root at the biggest root of the current source
  useEffect(() => {
    if (id || !sources.length) return;
    const src = sources.find((s) => s.key === sourceKey(sources, current));
    const root = src?.roots[0];
    if (root) nav(`/tree/${root.id}${namedOnly ? "?named=1" : ""}`, { replace: true });
  }, [id, sources, current, namedOnly, nav]);

  const rootId = Number(id);
  const rootState = useAsync(() => (id ? api.tree(rootId, 0) : Promise.resolve(null)), [id]);
  const [selected, setSelected] = useState<TreeNode | null>(null);
  useEffect(() => setSelected(null), [id]);

  if (!id) return <div className="loading">Loading…</div>;
  const root = rootState.data;
  const sel = selected ?? root ?? null;

  return (
    <div className="tree-page">
      <div className="tree-toolbar">
        <nav className="lineage" aria-label="Ancestors">
          {root?.lineage
            ?.slice()
            .reverse()
            .filter((c) => !c.informal)
            .map((c) => (
              <span key={c.id}>
                <Link to={`/tree/${c.id}${namedOnly ? "?named=1" : ""}`} title={`Re-root at ${c.label}`}>
                  {c.label}
                </Link>
                <span className="sep">›</span>
              </span>
            ))}
          {root && <strong>{root.label}</strong>}
        </nav>
        <label className="toggle">
          <input
            type="checkbox"
            checked={namedOnly}
            onChange={(e) => setParams(e.target.checked ? { named: "1" } : {})}
          />{" "}
          Named groups only (hide bracketed clades)
        </label>
      </div>
      <Status state={rootState} what="tree" />
      <div className="tree-layout">
        <PhyloTree rootId={rootId} namedOnly={namedOnly} selectedId={sel?.id} onSelect={setSelected} />
        {sel && (
          <aside className="tree-side">
            <h2 className={sel.informal ? "informal" : undefined}>{sel.label}</h2>
            <p>
              <RankBadge rank={sel.rank} /> {sel.authorship && <span className="authorship">{sel.authorship}</span>}
            </p>
            <SideSource id={sel.source_id} />
            <p className="muted small">
              {sel.n_children} immediate subgroups · {sel.n_descendants?.toLocaleString() ?? 0} groups below
            </p>
            <p className="side-actions">
              <Link to={`/taxon/${sel.id}`} className="button">Taxon page</Link>
              {sel.id !== rootId && (
                <Link to={`/tree/${sel.id}${namedOnly ? "?named=1" : ""}`} className="button secondary">
                  Re-root here
                </Link>
              )}
            </p>
            <SelectedSummary id={sel.id} />
          </aside>
        )}
      </div>
    </div>
  );
}

function SideSource({ id }: { id: number }) {
  const { byId } = useSources();
  return <SourceBadge sourceKey={byId(id)?.key} />;
}

function SelectedSummary({ id }: { id: number }) {
  const state = useAsync(() => api.taxon(id), [id]);
  const t = state.data;
  if (!t || t.id !== id) return <Status state={state} />;
  const apo = t.characters.filter((c) => c.apomorphy).slice(0, 6);
  return (
    <div className="small">
      {t.stats.species_count && (
        <p>
          {t.stats.genus_count && `${Number(t.stats.genus_count).toLocaleString()} genera, `}
          {Number(t.stats.species_count).toLocaleString()} species
          {t.stats.distribution && ` · ${t.stats.distribution}`}
        </p>
      )}
      {apo.length > 0 && (
        <>
          <h3>Possible apomorphies</h3>
          <ul className="characters compact">
            {apo.map((c, i) => (
              <li key={i}>{c.apo.join("; ")}</li>
            ))}
          </ul>
        </>
      )}
      {t.other_sources.length > 0 && (
        <>
          <h3>In other sources</h3>
          <ul className="compact">
            {t.other_sources.map((o) => (
              <li key={o.id}>
                <SourceBadge sourceKey={o.source} /> <ConceptLink c={o} max={30} />
                {o.order && o.order.id !== o.id ? <span className="muted"> in {o.order.label}</span> : null}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
