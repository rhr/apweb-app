import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type CladeRow, type Placement } from "../api";
import { Status, sourceLabel, useAsync, useSources } from "../lib";

type Tab = "moved" | "only_a" | "only_b" | "same" | "clades";

/** Compare two classifications: where families (or orders) sit, and which clades agree. */
export function ComparePage() {
  const { sources } = useSources();
  const [params, setParams] = useSearchParams();
  const a = params.get("a") ?? "apg3";
  const b = params.get("b") ?? "apg4";
  const rank = params.get("rank") ?? "family";
  const tab = (params.get("tab") as Tab) ?? "moved";
  const [filter, setFilter] = useState("");

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    next.set(k, v);
    setParams(next, { replace: true });
  };

  const cmp = useAsync(() => api.compare(a, b, rank), [a, b, rank]);
  const cl = useAsync(() => (tab === "clades" ? api.clades(a, b) : Promise.resolve(null)), [a, b, tab]);

  const options = sources.map((s) => (
    <option key={s.key} value={s.family === "apweb" ? "apweb" : s.key}>
      {sourceLabel(s)}
    </option>
  ));
  const name = (k: string) => sourceLabel(sources.find((s) => s.key === k || (s.family === k && k === "apweb")), k);

  const c = cmp.data;
  const rows: Placement[] = c && tab !== "clades" ? c[tab] : [];
  const f = filter.trim().toLowerCase();
  const shown = useMemo(
    () => (f ? rows.filter((r) => [r.name, r.a, r.b].some((x) => x?.toLowerCase().includes(f))) : rows),
    [rows, f],
  );

  return (
    <div className="compare">
      <h1>Compare classifications</h1>
      <div className="compare-controls">
        <label>
          From <select value={a} onChange={(e) => set("a", e.target.value)}>{options}</select>
        </label>
        <button className="link" title="Swap" onClick={() => setParams({ a: b, b: a, rank, tab })}>
          ⇄
        </button>
        <label>
          to <select value={b} onChange={(e) => set("b", e.target.value)}>{options}</select>
        </label>
        <label>
          compare{" "}
          <select value={rank} onChange={(e) => set("rank", e.target.value)}>
            <option value="family">families (by order)</option>
            <option value="order">orders (by clade)</option>
          </select>
        </label>
      </div>
      <Status state={cmp} what="comparison" />
      {c && (
        <>
          <div className="tabs" role="tablist">
            {(
              [
                ["moved", `Placed differently`, c.counts.moved],
                ["only_a", `Only in ${name(a)}`, c.counts.only_a],
                ["only_b", `Only in ${name(b)}`, c.counts.only_b],
                ["same", "Same placement", c.counts.same],
                ["clades", "Clades", null],
              ] as [Tab, string, number | null][]
            ).map(([k, label, n]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => set("tab", k)}>
                {label} {n !== null && <span className="count">{n}</span>}
              </button>
            ))}
          </div>
          {tab !== "clades" ? (
            <>
              <input
                className="filter"
                type="search"
                placeholder={`Filter ${rows.length} rows…`}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
              <table className="grid">
                <thead>
                  <tr>
                    <th>{rank === "family" ? "Family" : "Order"}</th>
                    <th>{name(a)}</th>
                    <th>{name(b)}</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.name}>
                      <td>
                        <Link to={`/taxon/${r.b_id ?? r.a_id}`}>{r.name}</Link>
                      </td>
                      <td>{r.a_id ? <Link to={`/taxon/${r.a_id}`}>{r.a ?? "(unplaced)"}</Link> : <Missing v={r.a} />}</td>
                      <td>{r.b_id ? <Link to={`/taxon/${r.b_id}`}>{r.b ?? "(unplaced)"}</Link> : <Missing v={r.b} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {tab === "only_a" || tab === "only_b" ? (
                <p className="muted small">“= X” means the other source treats the name as a synonym of, or a group within, X.</p>
              ) : null}
            </>
          ) : (
            <Clades state={cl} a={name(a)} b={name(b)} />
          )}
        </>
      )}
    </div>
  );
}

function Missing({ v }: { v: string | null }) {
  return v ? <span>{v}</span> : <span className="muted">—</span>;
}

function Clades({ state, a, b }: { state: ReturnType<typeof useAsync<{ matched: CladeRow[]; unmatched: CladeRow[] } | null>>; a: string; b: string }) {
  const d = state.data;
  if (!d) return <Status state={state} what="clades" />;
  const total = d.matched.length + d.unmatched.length;
  return (
    <div>
      <p>
        {d.matched.length} of {total} clades in {b} contain exactly the same orders as a clade in {a}
        (orders recognized by only one of them are ignored).
      </p>
      <h3>No counterpart in {a}</h3>
      <table className="grid">
        <thead>
          <tr>
            <th>Clade in {b}</th>
            <th>Orders</th>
          </tr>
        </thead>
        <tbody>
          {d.unmatched.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={`/taxon/${r.id}`} title={r.label}>{r.label.length > 70 ? r.label.slice(0, 69) + "…" : r.label}</Link>
              </td>
              <td className="small">{r.orders.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Matched</h3>
      <table className="grid">
        <thead>
          <tr>
            <th>Clade in {b}</th>
            <th>Counterpart in {a}</th>
          </tr>
        </thead>
        <tbody>
          {d.matched.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={`/taxon/${r.id}`} title={r.label}>{r.label.length > 60 ? r.label.slice(0, 59) + "…" : r.label}</Link>
              </td>
              <td className="small">{r.match}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
