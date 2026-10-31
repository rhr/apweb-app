import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { SearchBox } from "../components/SearchBox";
import { RankBadge, Status, sourceKey, useAsync, useSources } from "../lib";
import { SelectedSummary, SideSource } from "./TreePage";

const VIEWS = [
  ["polytomy", "Polytomy"],
  ["spiral", "Spiral"],
  ["balanced", "Balanced"],
  ["fern", "Fern"],
  ["natural", "Natural"],
] as const;

type FromViewer = { from: "onezoom"; type: "ready" } | { from: "onezoom"; type: "select"; conceptId: number }
  | { from: "onezoom"; type: "error"; message: string };

/** /onezoom → the current source drawn by OneZoom's tree viewer (public/oz-viewer, in an
 * iframe). Clicking a name in the tree shows it in the side panel. */
export function OneZoomPage() {
  const { sources, current } = useSources();
  const key = sources.length ? sourceKey(sources, current) : null;
  const frame = useRef<HTMLIFrameElement>(null);
  const [view, setView] = useState("polytomy");
  const viewRef = useRef(view);
  viewRef.current = view;
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // a new source reloads the viewer, keeping the current view
  const src = useMemo(
    () => key && `/oz-viewer/index.html?source=${encodeURIComponent(key)}&view=${viewRef.current}`,
    [key],
  );
  useEffect(() => {
    setReady(false);
    setSelected(null);
  }, [src]);

  useEffect(() => {
    const onMessage = (e: MessageEvent<FromViewer>) => {
      if (e.source !== frame.current?.contentWindow || e.data?.from !== "onezoom") return;
      if (e.data.type === "ready") setReady(true);
      else if (e.data.type === "select") setSelected(e.data.conceptId);
      else if (e.data.type === "error") setError(e.data.message);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const send = (msg: object) => frame.current?.contentWindow?.postMessage(msg, window.location.origin);

  return (
    <div className="tree-page">
      <div className="tree-toolbar">
        <div className="oz-fly">
          <SearchBox
            placeholder="Fly to a taxon…"
            onPick={(c) => {
              setError(null);
              setSelected(c.id);
              send({ type: "goto", conceptId: c.id });
            }}
          />
          {error && <span className="muted small">{error}</span>}
        </div>
        <label className="toggle">
          View{" "}
          <select
            value={view}
            disabled={!ready}
            onChange={(e) => {
              setView(e.target.value);
              send({ type: "view", view: e.target.value });
            }}
          >
            {VIEWS.map(([v, label]) => (
              <option key={v} value={v}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="tree-layout">
        {src ? (
          <iframe ref={frame} key={src} src={src} className="oz-frame" title="OneZoom tree viewer" />
        ) : (
          <div className="loading">Loading…</div>
        )}
        <aside className="tree-side">
          {selected ? (
            <Selected id={selected} />
          ) : (
            <p className="muted small">
              Drag and scroll to zoom through the tree. Click a group’s name to see it here. Prototype:
              groups that are their parent’s only child are drawn as one node (“Vitales / Vitaceae”);
              in the spiral views, groups with more than two subgroups show extra unnamed forks.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function Selected({ id }: { id: number }) {
  const state = useAsync(() => api.taxon(id), [id]);
  const t = state.data;
  if (!t || t.id !== id) return <Status state={state} />;
  return (
    <>
      <h2 className={t.informal ? "informal" : undefined}>{t.label}</h2>
      <p>
        <RankBadge rank={t.rank} /> {t.authorship && <span className="authorship">{t.authorship}</span>}
      </p>
      <SideSource id={t.source_id} />
      <p className="side-actions">
        <Link to={`/taxon/${t.id}`} className="button">Taxon page</Link>
        <Link to={`/tree/${t.id}`} className="button secondary">Cladogram</Link>
      </p>
      <SelectedSummary id={t.id} />
    </>
  );
}
