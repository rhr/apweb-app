import * as d3 from "d3";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type TreeNode } from "../api";
import { shortLabel } from "../lib";

const ROW = 22; // vertical spacing between tips
const COL = 190; // horizontal spacing between levels
const LABEL_CHARS = 30;

interface Props {
  rootId: number;
  namedOnly: boolean;
  selectedId?: number;
  onSelect: (node: TreeNode) => void;
}

/**
 * Collapsible, lazily loaded cladogram. Click a node's marker to expand/collapse it;
 * click its label to select it. Drag to pan, scroll to zoom.
 */
export function PhyloTree({ rootId, namedOnly, selectedId, onSelect }: Props) {
  const [root, setRoot] = useState<TreeNode | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0); // bump after mutating loaded children
  const svgRef = useRef<SVGSVGElement>(null);
  const [transform, setTransform] = useState<d3.ZoomTransform>(d3.zoomIdentity);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);

  // initial load: two levels below the root, all of them expanded
  useEffect(() => {
    let live = true;
    setRoot(null);
    setError(null);
    api.tree(rootId, 2, namedOnly).then(
      (t) => {
        if (!live) return;
        const open = new Set<number>([t.id]);
        t.children?.forEach((c) => c.children && c.children.length <= 12 && open.add(c.id));
        setExpanded(open);
        setRoot(t);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [rootId, namedOnly]);

  // layout of the currently visible part of the tree
  const layout = useMemo(() => {
    if (!root) return null;
    const h = d3.hierarchy<TreeNode>(root, (d) => (expanded.has(d.id) ? d.children : undefined));
    return d3.tree<TreeNode>().nodeSize([ROW, COL]).separation((a, b) => (a.parent === b.parent ? 1 : 1.3))(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root, expanded, version]);

  // pan & zoom
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.15, 3])
      .on("zoom", (e: d3.D3ZoomEvent<SVGSVGElement, unknown>) => setTransform(e.transform));
    zoomRef.current = zoom;
    svg.call(zoom).on("dblclick.zoom", null);
    return () => {
      svg.on(".zoom", null);
    };
  }, []);

  // recentre on the root when it changes
  useEffect(() => {
    if (!root || !svgRef.current || !zoomRef.current) return;
    const { height } = svgRef.current.getBoundingClientRect();
    // leave room for the root's label, which sits to the left of its node
    const labelRoom = Math.min(shortLabel(root.label, LABEL_CHARS).length * 7, 220);
    d3.select(svgRef.current).call(zoomRef.current.transform, d3.zoomIdentity.translate(20 + labelRoom, height / 2));
  }, [root?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggle(node: TreeNode) {
    if (expanded.has(node.id)) {
      const next = new Set(expanded);
      next.delete(node.id);
      setExpanded(next);
      return;
    }
    if (!node.n_children) return;
    if (!node.children) {
      setLoading((s) => new Set(s).add(node.id));
      try {
        const sub = await api.tree(node.id, 1, namedOnly);
        node.children = sub.children ?? [];
        node.truncated = sub.truncated;
        setVersion((v) => v + 1);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setLoading((s) => {
          const n = new Set(s);
          n.delete(node.id);
          return n;
        });
      }
    }
    setExpanded((s) => new Set(s).add(node.id));
  }

  function expandAll(node: TreeNode, levels: number) {
    // expand already-loaded descendants up to `levels` deep
    const next = new Set(expanded);
    const walk = (n: TreeNode, d: number) => {
      if (d === 0 || !n.children) return;
      next.add(n.id);
      n.children.forEach((c) => walk(c, d - 1));
    };
    walk(node, levels);
    setExpanded(next);
  }

  if (error) return <div className="error">Couldn't load the tree: {error}</div>;

  const nodes = layout ? layout.descendants() : [];
  const links = layout ? layout.links() : [];

  return (
    <div className="phylotree">
      <svg ref={svgRef} role="img" aria-label="Phylogeny">
        <g transform={transform.toString()}>
          {links.map((l) => (
            <path
              key={`${l.source.data.id}-${l.target.data.id}`}
              className="branch"
              d={`M${l.source.y},${l.source.x}V${l.target.x}H${l.target.y}`}
            />
          ))}
          {nodes.map((n) => {
            const d = n.data;
            const isOpen = expanded.has(d.id) && !!d.children;
            const collapsed = d.n_children > 0 && !isOpen;
            const tip = !n.children;
            const cls = [
              "node",
              `rank-${d.rank ?? "none"}`,
              d.informal ? "informal" : "",
              d.id === selectedId ? "selected" : "",
              collapsed ? "collapsed" : "",
            ].join(" ");
            return (
              <g key={d.id} className={cls} transform={`translate(${n.y},${n.x})`}>
                <circle
                  r={collapsed ? 5 : 3.5}
                  onClick={() => toggle(d)}
                  onDoubleClick={() => expandAll(d, 3)}
                >
                  <title>
                    {collapsed ? `Expand (${d.n_children} below)` : d.n_children ? "Collapse" : d.label}
                  </title>
                </circle>
                {loading.has(d.id) && <circle className="spinner" r={8} />}
                <text
                  x={tip ? 8 : -6}
                  y={tip ? 4 : -6}
                  textAnchor={tip ? "start" : "end"}
                  onClick={() => onSelect(d)}
                >
                  <title>{d.label}</title>
                  {shortLabel(d.label, tip ? 60 : LABEL_CHARS)}
                  {tip && collapsed ? ` (${d.n_descendants?.toLocaleString() ?? d.n_children})` : ""}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      {!root && <div className="loading overlay">Loading tree…</div>}
      <div className="tree-help muted small">
        Click a <span className="dot" /> to expand or collapse · double-click to open 3 levels · click a name to select · drag to pan, scroll to zoom
      </div>
    </div>
  );
}
