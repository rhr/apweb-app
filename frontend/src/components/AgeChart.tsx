import * as d3 from "d3";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { AgeEstimate } from "../api";

const KINDS = [
  { key: "crown", label: "Crown" },
  { key: "stem", label: "Stem" },
  { key: "fossil", label: "Fossils" },
] as const;

const PROBLEM_TEXT: Record<string, string> = {
  no_citation: "no citation found next to this age",
  interval_order: "the interval does not contain the estimate (a typo?)",
  implausible: "older than 500 Ma",
  unresolved_subject: "names a clade with no header on the site",
};

/**
 * Age estimates for one clade, one row per study: the interval as a thin line, point
 * estimates as dots, a range as a bar. Older is on the left, as on a timetree.
 */
export function AgeChart({ ages }: { ages: AgeEstimate[] }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of ages) c[a.kind] = (c[a.kind] ?? 0) + 1;
    return c;
  }, [ages]);
  const [kind, setKind] = useState<string>(() => KINDS.find((k) => counts[k.key])?.key ?? "crown");
  const rows = ages.filter((a) => a.kind === kind);

  const x = useMemo(() => {
    const hi = d3.max(rows, (a) => Math.max(a.older ?? 0, ...a.points)) ?? 1;
    const lo = d3.min(rows, (a) => Math.min(a.younger ?? Infinity, ...a.points)) ?? 0;
    const pad = (hi - lo) * 0.06 || hi * 0.1 || 1;
    return d3.scaleLinear().domain([hi + pad, Math.max(0, lo - pad)]).range([2, 98]).nice();
  }, [rows]);
  const median = d3.median(rows, (a) => a.value);
  const pct = (v: number) => `${x(v)}%`;

  return (
    <div className="ages">
      <div className="age-tabs" role="tablist">
        {KINDS.filter((k) => counts[k.key]).map((k) => (
          <button key={k.key} role="tab" aria-selected={kind === k.key} onClick={() => setKind(k.key)}>
            {k.label} <span className="count">{counts[k.key]}</span>
          </button>
        ))}
      </div>
      {median !== undefined && (
        <p className="muted small">
          {rows.length} {kind === "fossil" ? "fossil ages" : "estimates"}, median {fmt(median)} Ma, from{" "}
          {fmt(d3.min(rows, (a) => a.value)!)} to {fmt(d3.max(rows, (a) => a.value)!)} Ma.
          {kind === "fossil" && " Ages of fossils placed in or near this group: evidence for a minimum age."}
        </p>
      )}
      <div className="age-grid">
        <div />
        <svg className="age-axis" width="100%" height="22" aria-hidden>
          {x.ticks(6).map((t) => (
            <g key={t}>
              <line x1={pct(t)} x2={pct(t)} y1={16} y2={22} />
              <text x={pct(t)} y={12} textAnchor="middle">
                {t}
              </text>
            </g>
          ))}
        </svg>
        {rows.map((a) => (
          <AgeRow key={a.id} a={a} pct={pct} ticks={x.ticks(6)} />
        ))}
        <div />
        <div className="muted small age-unit">Ma (millions of years ago)</div>
      </div>
    </div>
  );
}

function AgeRow({ a, pct, ticks }: { a: AgeEstimate; pct: (v: number) => string; ticks: number[] }) {
  const cite = a.citations[0];
  const ref = a.citations.find((c) => c.reference)?.reference;
  const label = cite ? cite.text : "uncited";
  const lo = a.younger ?? Math.min(...a.points);
  const hi = a.older ?? Math.max(...a.points);
  const tip = [
    a.text,
    a.methods.length ? `Methods mentioned: ${a.methods.join(", ")}` : null,
    a.subject ? `Stated for: ${a.subject}${a.subject_resolved === false ? " (no such clade on the site)" : ""}` : null,
    a.stated_under ? `Under: ${a.stated_under}` : null,
    ...a.problems.map((p) => `⚠ ${PROBLEM_TEXT[p] ?? p}`),
    ref?.raw,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <>
      <div className="age-label" title={tip}>
        {ref ? <Link to={`/reference/${ref.id}`}>{label}</Link> : <span className="unresolved">{label}</span>}
        {a.citations.length > 1 && <span className="muted small"> +{a.citations.length - 1}</span>}
        {a.problems.length > 0 && <span className="age-warn" aria-label="parse problem"> ⚠</span>}
        {a.locator && (
          <a href={a.locator} target="_blank" rel="noreferrer" className="age-src" aria-label="Statement on APWeb">
            {" "}↗
          </a>
        )}
      </div>
      <svg className={`age-row ${a.kind}`} width="100%" height="18" role="img" aria-label={`${a.text}${cite ? `, ${cite.text}` : ""}`}>
        <title>{tip}</title>
        {ticks.map((t) => (
          <line key={t} className="grid" x1={pct(t)} x2={pct(t)} y1={0} y2={18} />
        ))}
        {hi !== lo && <line className="ci" x1={pct(hi)} x2={pct(lo)} y1={9} y2={9} />}
        {a.form === "range" ? (
          <line className="range" x1={pct(Math.max(...a.points))} x2={pct(Math.min(...a.points))} y1={9} y2={9} />
        ) : (
          a.points.map((p, i) => <circle key={i} className="pt" cx={pct(p)} cy={9} r={a.approx ? 3.5 : 4} />)
        )}
      </svg>
    </>
  );
}

function fmt(v: number) {
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}
