import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { SearchBox } from "./components/SearchBox";
import { SourceProvider, sourceLabel, useSources } from "./lib";
import { ComparePage } from "./pages/ComparePage";
import { ConflictsPage, HomePage, NotFoundPage, ReferencePage, SearchPage, TaxonByName } from "./pages/OtherPages";
import { TaxonPage } from "./pages/TaxonPage";
import { TreePage } from "./pages/TreePage";
import "./styles.css";

function SourcePicker() {
  const { sources, current, setCurrent } = useSources();
  if (!sources.length) return null;
  // one entry per APWeb family (latest snapshot) plus each APG version
  const opts = sources.filter((s) => s.family !== "apweb").map((s) => ({ value: s.key, label: sourceLabel(s) }));
  const apweb = sources.filter((s) => s.family === "apweb").sort((a, b) => (a.version < b.version ? 1 : -1))[0];
  if (apweb) opts.unshift({ value: "apweb", label: sourceLabel(apweb) });
  return (
    <label className="source-picker" title="Source used for search and the tree viewer">
      <span className="muted small">Source</span>
      <select value={current} onChange={(e) => setCurrent(e.target.value)}>
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function App() {
  return (
    <>
      <header className="topbar">
        <NavLink to="/" className="brand">
          Angiosperm Phylogeny <span>Explorer</span>
        </NavLink>
        <nav className="mainnav">
          <NavLink to="/tree">Tree</NavLink>
          <NavLink to="/compare">Compare</NavLink>
          <NavLink to="/conflicts">Conflicts</NavLink>
        </nav>
        <SearchBox />
        <SourcePicker />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/taxon/name/:name" element={<TaxonByName />} />
          <Route path="/taxon/:id" element={<TaxonPage />} />
          <Route path="/tree" element={<TreePage />} />
          <Route path="/tree/:id" element={<TreePage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/conflicts" element={<ConflictsPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/reference/:id" element={<ReferencePage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
      <footer className="muted small">
        Facts extracted from the{" "}
        <a href="https://www.mobot.org/mobot/research/apweb/" target="_blank" rel="noreferrer">
          Angiosperm Phylogeny Website
        </a>{" "}
        (© P. F. Stevens) and the APG classifications; follow the links for the original discussion.
      </footer>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SourceProvider>
        <App />
      </SourceProvider>
    </BrowserRouter>
  </StrictMode>,
);
