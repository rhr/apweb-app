import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ConceptLink, RankBadge, Status, fmtNum, shortLabel, sourceLabel, useAsync, useSources } from "../lib";

function AskBox() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  return (
    <form
      className="ask-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (q.trim()) nav(`/ask?q=${encodeURIComponent(q.trim())}`);
      }}
    >
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Ask a question, e.g. “Which families moved between APG III and APG IV?”"
        aria-label="Ask a question"
      />
      <button className="button" type="submit">
        Ask
      </button>
    </form>
  );
}

export function HomePage() {
  const state = useAsync(() => api.overview(), []);
  const o = state.data;
  return (
    <div className="home">
      <section className="hero">
        <h1>Angiosperm Phylogeny Explorer</h1>
        <p className="lede">
          Taxa, clades, characters and literature from P. F. Stevens' Angiosperm Phylogeny Website and the APG
          classifications, linked to their sources and comparable across versions.
        </p>
        <AskBox />
        <div className="hero-actions">
          <Link to="/taxon/name/Angiospermae" className="button">Start at the angiosperms</Link>
          <Link to="/tree" className="button secondary">Phylogeny viewer</Link>
          <Link to="/compare?a=apg3&b=apg4" className="button secondary">APG III → IV</Link>
        </div>
      </section>
      <Status state={state} what="overview" />
      {o && (
        <>
          <dl className="stat-row big">
            <div className="stat">
              <dt>Taxon concepts</dt>
              <dd>{fmtNum(o.counts.concepts)}</dd>
            </div>
            <div className="stat">
              <dt>Claims</dt>
              <dd>{fmtNum(o.counts.claims)}</dd>
            </div>
            <div className="stat">
              <dt>References</dt>
              <dd>{fmtNum(o.counts.references)}</dd>
            </div>
            <div className="stat">
              <dt>Citations linked</dt>
              <dd>{Math.round((100 * o.counts.citations_resolved) / o.counts.citations)}%</dd>
            </div>
          </dl>
          <h2>Sources</h2>
          <table className="grid">
            <thead>
              <tr>
                <th>Source</th>
                <th>Concepts</th>
                <th>Claims</th>
                <th>Citation & provenance</th>
              </tr>
            </thead>
            <tbody>
              {o.sources.map((s) => (
                <tr key={s.key}>
                  <td>
                    <strong>{sourceLabel(s)}</strong>
                    <div>
                      {s.roots[0] && (
                        <Link to={`/tree/${s.roots[0].id}`} className="small">
                          tree
                        </Link>
                      )}
                    </div>
                  </td>
                  <td>{fmtNum(s.n_concepts)}</td>
                  <td>{fmtNum(s.n_claims)}</td>
                  <td className="small">
                    {s.citation}
                    <div className="muted">{s.notes ?? s.license}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/** /taxon/name/:name → resolve within the current source and redirect to /taxon/:id. */
export function TaxonByName() {
  const { name = "" } = useParams();
  const { current } = useSources();
  const nav = useNavigate();
  const state = useAsync(() => api.taxon(name, current), [name, current]);
  useEffect(() => {
    if (state.data) nav(`/taxon/${state.data.id}`, { replace: true });
  }, [state.data, nav]);
  return <Status state={state} what={name} />;
}

export function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const { current, sources } = useSources();
  const state = useAsync(() => api.search(q, current), [q, current]);
  const refs = useAsync(() => api.references(q), [q]);
  const hits = state.data ?? [];
  const src = sources.find((s) => s.key === current || s.family === current);
  return (
    <div className="search-page">
      <h1>
        Results for “{q}” <span className="muted small">in {sourceLabel(src, current)}</span>
      </h1>
      <Status state={state} what="results" />
      <ul className="hits">
        {hits.map((h, i) => (
          <li key={i}>
            <span className={`kind kind-${h.kind}`}>{h.kind === "claim" ? "statement" : h.kind}</span>{" "}
            {h.concept && <ConceptLink c={h.concept} max={60} />} {h.concept && <RankBadge rank={h.concept.rank} />}
            {h.kind !== "concept" && <div className="snippet">{h.snippet.replace(/[«»]/g, "")}</div>}
          </li>
        ))}
        {!state.loading && !hits.length && <li className="muted">No taxa or statements found.</li>}
      </ul>
      {refs.data && refs.data.length > 0 && (
        <>
          <h2>References</h2>
          <ul className="hits">
            {refs.data.map((r) => (
              <li key={r.id}>
                <Link to={`/reference/${r.id}`}>{r.raw}</Link>{" "}
                <span className="muted small">cited for {r.n_taxa} taxa</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function ReferencePage() {
  const { id = "" } = useParams();
  const state = useAsync(() => api.reference(Number(id)), [id]);
  const r = state.data;
  if (!r) return <Status state={state} what="reference" />;
  return (
    <article>
      <h1 className="ref-title">{r.title ?? r.raw}</h1>
      <p>{r.raw}</p>
      <p>
        {r.doi && (
          <a href={`https://doi.org/${r.doi}`} target="_blank" rel="noreferrer" className="external">
            doi:{r.doi} ↗
          </a>
        )}{" "}
        {r.url && (
          <a href={r.url} target="_blank" rel="noreferrer" className="external">
            APWeb bibliography ↗
          </a>
        )}
      </p>
      <h2>
        Cited for <span className="count">{r.cited_by.length}</span>
      </h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Taxon</th>
            <th>Topics</th>
          </tr>
        </thead>
        <tbody>
          {r.cited_by.map((c) => (
            <tr key={c.id}>
              <td>
                <ConceptLink c={c} max={60} /> <RankBadge rank={c.rank} />
              </td>
              <td className="small">{c.topics.map((t) => t.replace("/", " › ")).join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

export function ConflictsPage() {
  const state = useAsync(() => api.conflicts("apweb"), []);
  const rows = state.data ?? [];
  return (
    <div>
      <h1>Placement conflicts in APWeb</h1>
      <p className="lede small">
        Groups that different APWeb pages place differently, judged by the bracketed topologies on each page (e.g. one
        page giving [Fabales [Rosales …]] and another [Rosales + Cucurbitales]). Support counts how many pages, and by
        which evidence, back each placement. These usually mark parts of the tree under revision.
      </p>
      <Status state={state} what="conflicts" />
      <table className="grid">
        <thead>
          <tr>
            <th>Group</th>
            <th>Placed in (support)</th>
            <th>Also given as a member of (support)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.child_id}>
              <td>
                <Link to={`/taxon/${d.child_id}`} title={d.child}>{shortLabel(d.child, 50)}</Link>
              </td>
              <td className="small" title={d.parent}>
                {shortLabel(d.parent, 60)} ({d.score})
              </td>
              <td className="small">
                {d.alternatives.map((a) => (
                  <div key={a.parent} title={a.parent}>
                    {shortLabel(a.parent, 60)} ({a.score})
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function NotFoundPage() {
  return (
    <div>
      <h1>Not found</h1>
      <p>
        <Link to="/">Back to the start</Link>
      </p>
    </div>
  );
}
