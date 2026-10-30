import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type Concept, type Source } from "./api";

// ------------------------------------------------------------------ data loading

export type Async<T> = { data?: T; error?: Error; loading: boolean };

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<Async<T>>({ loading: true });
  useEffect(() => {
    let live = true;
    setState((s) => ({ data: s.data, loading: true }));
    fn().then(
      (data) => live && setState({ data, loading: false }),
      (error: Error) => live && setState({ error, loading: false }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function Status({ state, what }: { state: Async<unknown>; what?: string }) {
  if (state.error) return <div className="error">Couldn't load {what ?? "data"}: {state.error.message}</div>;
  if (state.loading && !state.data) return <div className="loading">Loading{what ? ` ${what}` : ""}…</div>;
  return null;
}

// ------------------------------------------------------------------ sources

interface SourceCtx {
  sources: Source[];
  current: string; // source key used for search / tree roots
  setCurrent: (key: string) => void;
  byId: (id: number) => Source | undefined;
}

const Ctx = createContext<SourceCtx>({ sources: [], current: "apweb", setCurrent: () => {}, byId: () => undefined });

function readStored(): string | null {
  try {
    return localStorage.getItem("apw.source");
  } catch {
    return null;
  }
}

export function SourceProvider({ children }: { children: ReactNode }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [current, setCurrentState] = useState<string>(readStored() ?? "apweb");
  useEffect(() => {
    api.sources().then(setSources, () => setSources([]));
  }, []);
  const setCurrent = (key: string) => {
    setCurrentState(key);
    try {
      localStorage.setItem("apw.source", key);
    } catch {
      /* storage unavailable */
    }
  };
  const byId = (id: number) => sources.find((s) => s.id === id);
  return <Ctx.Provider value={{ sources, current, setCurrent, byId }}>{children}</Ctx.Provider>;
}

export const useSources = () => useContext(Ctx);

/** Resolve 'apweb' (family) to the concrete latest key. */
export function sourceKey(sources: Source[], ref: string): string {
  if (sources.some((s) => s.key === ref)) return ref;
  const fam = sources.filter((s) => s.family === ref).sort((a, b) => (a.version < b.version ? 1 : -1));
  return fam[0]?.key ?? ref;
}

export function sourceLabel(s: Source | undefined, fallback = ""): string {
  if (!s) return fallback;
  return s.family === "apweb" ? `APWeb ${s.version}` : s.title;
}

// ------------------------------------------------------------------ small components

export function shortLabel(label: string, max = 48): string {
  return label.length > max ? label.slice(0, max - 1) + "…" : label;
}

export function ConceptLink({ c, max }: { c: Pick<Concept, "id" | "label" | "informal">; max?: number }) {
  return (
    <Link to={`/taxon/${c.id}`} className={c.informal ? "informal" : undefined} title={c.label}>
      {max ? shortLabel(c.label, max) : c.label}
    </Link>
  );
}

export function RankBadge({ rank }: { rank: string | null }) {
  if (!rank) return null;
  return <span className={`rank rank-${rank}`}>{rank}</span>;
}

export function SourceBadge({ sourceKey: key }: { sourceKey?: string }) {
  if (!key) return null;
  const fam = key.startsWith("apweb") ? "apweb" : "apg";
  return <span className={`source-badge src-${fam}`}>{key.replace("@", " ")}</span>;
}

export function fmtNum(v: string | number | undefined): string {
  if (v === undefined) return "";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString() : String(v);
}
