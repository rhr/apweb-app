import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Concept } from "../api";
import { RankBadge, shortLabel, useSources } from "../lib";

/** Name autocomplete within the current source; Enter without a pick runs a full search.
 * With `onPick`, picking a name calls it instead of opening the taxon page, and Enter
 * picks the first suggestion. */
export function SearchBox({ onPick, placeholder }: { onPick?: (c: Concept) => void; placeholder?: string } = {}) {
  const { current } = useSources();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Concept[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (q.trim().length < 2) {
      setItems([]);
      return;
    }
    const t = setTimeout(() => {
      api.suggest(q, current).then((r) => {
        setItems(r);
        setActive(-1);
      }, () => setItems([]));
    }, 150);
    return () => clearTimeout(t);
  }, [q, current]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const go = (c: Concept) => {
    setOpen(false);
    setQ("");
    if (onPick) onPick(c);
    else nav(`/taxon/${c.id}`);
  };

  return (
    <div className="searchbox" ref={box}>
      <input
        type="search"
        placeholder={placeholder ?? "Search taxa, characters, literature…"}
        aria-label="Search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, items.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, -1));
          } else if (e.key === "Enter") {
            if (active >= 0 && items[active]) go(items[active]);
            else if (onPick && items[0]) go(items[0]);
            else if (q.trim()) {
              setOpen(false);
              nav(`/search?q=${encodeURIComponent(q.trim())}`);
            }
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && items.length > 0 && (
        <ul className="suggest" role="listbox">
          {items.map((c, i) => (
            <li
              key={c.id}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : undefined}
              onMouseDown={(e) => {
                e.preventDefault();
                go(c);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className={c.informal ? "informal" : undefined}>{shortLabel(c.label, 60)}</span>
              <RankBadge rank={c.rank} />
              {c.n_descendants ? <span className="muted small">{c.n_descendants.toLocaleString()} below</span> : null}
            </li>
          ))}
          {!onPick && <li
            className="suggest-all"
            onMouseDown={(e) => {
              e.preventDefault();
              setOpen(false);
              nav(`/search?q=${encodeURIComponent(q.trim())}`);
            }}
          >
            Search everything for “{q.trim()}” ↵
          </li>}
        </ul>
      )}
    </div>
  );
}
