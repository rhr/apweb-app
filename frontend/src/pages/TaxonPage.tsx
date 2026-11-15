import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Character, type Citations, type Taxon } from "../api";
import { AgeChart } from "../components/AgeChart";
import { ConceptLink, RankBadge, SourceBadge, Status, fmtNum, shortLabel, useAsync } from "../lib";

export function TaxonPage() {
  const { id = "" } = useParams();
  const state = useAsync(() => api.taxon(id), [id]);
  const t = state.data;
  if (!t || String(t.id) !== id) return <Status state={state} what="taxon" />;
  return <TaxonView t={t} key={t.id} />;
}

function TaxonView({ t }: { t: Taxon }) {
  const isApweb = t.source?.startsWith("apweb");
  return (
    <article className="taxon">
      <Lineage t={t} />
      <header className="taxon-head">
        <h1 className={t.informal ? "informal" : undefined}>{t.label}</h1>
        <div className="taxon-meta">
          {t.authorship && <span className="authorship">{t.authorship}</span>}
          <RankBadge rank={t.rank} />
          <SourceBadge sourceKey={t.source} />
          {t.url && (
            <a href={t.url} target="_blank" rel="noreferrer" className="external">
              {isApweb ? "Read on APWeb" : "Source"} ↗
            </a>
          )}
          <Link to={`/tree/${t.id}`} className="button small">Open in tree viewer</Link>
        </div>
        {t.labels.length > 0 && (
          <p className="muted small">
            Also: {t.labels.map((l) => l.label + (l.kind === "synonym" ? " (syn.)" : "")).join(" · ")}
          </p>
        )}
      </header>

      <Facts t={t} />
      <PlacementNote t={t} />
      <OtherSources t={t} />

      <div className="two-col">
        <section>
          <h2>
            {t.informal || t.rank === "clade" ? "Members" : "Subgroups"} <span className="count">{t.children.length}</span>
          </h2>
          {t.children.length ? (
            <ul className="children">
              {t.children.map((c) => (
                <li key={c.id}>
                  <ConceptLink c={c} max={70} /> <RankBadge rank={c.rank} />
                  {c.n_descendants ? <span className="muted small"> {c.n_descendants.toLocaleString()} below</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No subgroups recorded.</p>
          )}
        </section>
        <Characters t={t} />
      </div>

      <Ages t={t} />
      <Literature t={t} />
    </article>
  );
}

function Lineage({ t }: { t: Taxon }) {
  const [full, setFull] = useState(false);
  const lin = [...t.lineage].reverse();
  // by default show named groups only; bracketed clades collapse into "…"
  const shown = full ? lin : lin.filter((c, i) => !c.informal || i === lin.length - 1);
  return (
    <nav className="lineage" aria-label="Lineage">
      {shown.map((c) => (
        <span key={c.id}>
          <ConceptLink c={c} max={full ? 40 : 28} />
          <span className="sep">›</span>
        </span>
      ))}
      {lin.length !== shown.length || full ? (
        <button className="link small" onClick={() => setFull(!full)}>
          {full ? "named groups only" : `show all ${lin.length} levels`}
        </button>
      ) : null}
    </nav>
  );
}

function Facts({ t }: { t: Taxon }) {
  const s = t.stats;
  const items: [string, string][] = [];
  if (s.family_count) items.push(["Families", fmtNum(s.family_count)]);
  if (s.genus_count) items.push(["Genera", fmtNum(s.genus_count)]);
  if (s.species_count) items.push(["Species", fmtNum(s.species_count)]);
  if (t.n_descendants) items.push(["Groups below", fmtNum(t.n_descendants)]);
  if (!items.length && !s.distribution && !t.synonyms.length) return null;
  return (
    <section className="facts">
      {items.length > 0 && (
        <dl className="stat-row">
          {items.map(([k, v]) => (
            <div key={k} className="stat">
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {s.distribution && (
        <p>
          <strong>Distribution.</strong> {s.distribution}
        </p>
      )}
      {t.synonyms.length > 0 && (
        <p>
          <strong>Synonymy.</strong> {t.synonyms.join("; ")}
        </p>
      )}
    </section>
  );
}

function PlacementNote({ t }: { t: Taxon }) {
  const p = t.placement;
  if (!p || !p.alternatives.length) return null;
  const strong = p.alternatives.filter((a) => p.score && a.score >= 0.5 * p.score && a.methods.bracket);
  if (!strong.length) return null;
  const parent = t.lineage[0];
  return (
    <aside className="note conflict">
      <strong>Placement differs between pages of this source.</strong> Placed in{" "}
      {parent ? <ConceptLink c={parent} max={60} /> : "?"} (support {p.score}); also given as a member of{" "}
      {strong.map((a, i) => (
        <span key={a.parent}>
          {i > 0 && "; "}
          <span title={a.parent}>{shortLabel(a.parent, 60)}</span> (support {a.score})
        </span>
      ))}
      .
    </aside>
  );
}

function OtherSources({ t }: { t: Taxon }) {
  if (!t.other_sources.length) return null;
  return (
    <section>
      <h2>Across sources</h2>
      <table className="grid">
        <thead>
          <tr>
            <th>Source</th>
            <th>Name</th>
            <th>Rank</th>
            <th>Order</th>
            <th>Parent</th>
            <th>Match</th>
          </tr>
        </thead>
        <tbody>
          <tr className="current">
            <td>
              <SourceBadge sourceKey={t.source} />
            </td>
            <td>{t.label}</td>
            <td>{t.rank}</td>
            <td>{t.lineage.find((c) => c.rank === "order")?.label ?? "—"}</td>
            <td>{t.lineage[0] ? shortLabel(t.lineage[0].label, 40) : "—"}</td>
            <td className="muted">this page</td>
          </tr>
          {t.other_sources.map((o) => (
            <tr key={o.id}>
              <td>
                <SourceBadge sourceKey={o.source} />
              </td>
              <td>
                <ConceptLink c={o} max={50} />
              </td>
              <td>{o.rank}</td>
              <td>{o.order ? <ConceptLink c={o.order} /> : "—"}</td>
              <td>{o.parent ? <ConceptLink c={o.parent} max={40} /> : "—"}</td>
              <td className="muted small">{o.relations.join(", ").replace("same_name", "same name")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function CharText({ c }: { c: Character }) {
  // bold the apomorphic runs within the statement
  const parts: (string | { b: string })[] = [];
  let rest = c.text;
  for (const a of c.apo) {
    const i = rest.indexOf(a);
    if (i < 0) continue;
    parts.push(rest.slice(0, i), { b: a });
    rest = rest.slice(i + a.length);
  }
  parts.push(rest);
  return (
    <>
      {parts.map((p, i) => (typeof p === "string" ? <span key={i}>{p}</span> : <strong key={i}>{p.b}</strong>))}
    </>
  );
}

function Characters({ t }: { t: Taxon }) {
  const [apoOnly, setApoOnly] = useState(false);
  if (!t.characters.length)
    return (
      <section>
        <h2>Characterization</h2>
        <p className="muted">None recorded for this group in this source.</p>
      </section>
    );
  const list = apoOnly ? t.characters.filter((c) => c.apomorphy) : t.characters;
  return (
    <section>
      <h2>
        Characterization <span className="count">{t.n_characters}</span>
      </h2>
      <p className="muted small">
        <strong>Bold</strong>: possible apomorphy · <span className="uncertain-key">dashed</span>: status uncertain ·
        [brackets]: explanatory. {t.n_apomorphies} statements include possible apomorphies.{" "}
        <label className="toggle">
          <input type="checkbox" checked={apoOnly} onChange={(e) => setApoOnly(e.target.checked)} /> apomorphies only
        </label>
      </p>
      <ul className="characters">
        {list.map((c, i) => (
          <li key={i} className={c.uncertain ? "uncertain" : undefined}>
            <CharText c={c} />
          </li>
        ))}
      </ul>
      {t.characters[0]?.locator && (
        <p className="small">
          <a href={t.characters[0].locator} target="_blank" rel="noreferrer" className="external">
            Characterization in context on APWeb ↗
          </a>
        </p>
      )}
    </section>
  );
}

function Ages({ t }: { t: Taxon }) {
  if (!t.ages?.length) return null;
  return (
    <section>
      <h2>
        Age <span className="count">{t.ages.length} estimates</span>
      </h2>
      <p className="muted small">
        Divergence-time estimates the source reports for this clade, read from its “Age” paragraphs. Hover a row for
        the age as written; ⚠ marks statements the parser could not read cleanly.
      </p>
      <AgeChart ages={t.ages} />
    </section>
  );
}

function Literature({ t }: { t: Taxon }) {
  const [open, setOpen] = useState<string | null>(null);
  const [data, setData] = useState<Record<string, Citations>>({});
  if (!t.topics.length) return null;
  const total = t.topics.reduce((n, x) => n + x.n_citations, 0);

  const show = async (topic: string) => {
    setOpen(open === topic ? null : topic);
    if (!data[topic]) {
      const r = await api.citations(t.id, topic);
      setData((d) => ({ ...d, [topic]: r }));
    }
  };

  return (
    <section>
      <h2>
        Literature <span className="count">{total.toLocaleString()} citations</span>
      </h2>
      <p className="muted small">Works the source cites when discussing this group, by topic.</p>
      <ul className="topics">
        {t.topics.map(({ topic, n_citations }) => {
          const cites = data[topic]?.topics.find((g) => g.topic === topic)?.citations;
          return (
            <li key={topic}>
              <button className="topic" aria-expanded={open === topic} onClick={() => show(topic)}>
                <span>{topic.replace("/", " › ")}</span> <span className="count">{n_citations}</span>
              </button>
              {open === topic && (
                <ol className="refs">
                  {!cites && <li className="loading">Loading…</li>}
                  {cites?.map((c, i) => (
                    <li key={i}>
                      {c.reference ? (
                        <>
                          <Link to={`/reference/${c.reference.id}`}>{c.reference.raw}</Link>
                          {c.reference.doi && (
                            <>
                              {" "}
                              <a href={`https://doi.org/${c.reference.doi}`} target="_blank" rel="noreferrer" className="doi">
                                doi
                              </a>
                            </>
                          )}
                          {c.confidence < 1 && (
                            <span className="muted small" title="Resolved by tie-breaking; check before relying on it">
                              {" "}
                              (probable match)
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="unresolved">
                          {c.text}{" "}
                          <span className="muted small">
                            {c.candidates
                              ? `(${c.candidates} possible works in the bibliography)`
                              : "(not found in the bibliography)"}
                          </span>
                        </span>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
