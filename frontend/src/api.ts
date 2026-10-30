// Typed client for the backend API (backend/src/apw/api.py, shapes from service.py).

export interface Concept {
  id: number;
  label: string;
  rank: string | null;
  informal: boolean;
  authorship: string | null;
  url: string | null;
  source_id: number;
  source?: string;
  n_children?: number;
  n_descendants?: number;
}

export interface Source {
  id: number;
  key: string;
  family: string;
  version: string;
  title: string;
  kind: string;
  url: string | null;
  citation: string | null;
  license: string | null;
  retrieved_at: string | null;
  notes: string | null;
  n_concepts: number;
  n_claims: number;
  roots: Concept[];
}

export interface Character {
  text: string;
  apomorphy: boolean;
  apo: string[];
  uncertain: boolean;
  locator: string | null;
}

export interface Alternative {
  parent: string;
  score: number;
  methods: Record<string, number>;
}

export interface OtherSource extends Concept {
  relations: string[];
  parent: Concept | null;
  order: Concept | null;
}

export interface Taxon extends Concept {
  labels: { label: string; kind: string }[];
  lineage: Concept[];
  children: Concept[];
  stats: Partial<Record<"family_count" | "genus_count" | "species_count" | "distribution", string>>;
  synonyms: string[];
  n_characters: number;
  n_apomorphies: number;
  characters: Character[];
  topics: { topic: string; n_citations: number }[];
  placement: {
    parent_id: number;
    score: number | null;
    methods: Record<string, number> | null;
    basis: string | null;
    alternatives: Alternative[];
    locator: string | null;
  } | null;
  other_sources: OtherSource[];
}

export interface TreeNode extends Concept {
  n_children: number;
  children?: TreeNode[];
  truncated?: boolean;
  lineage?: Concept[];
}

export interface Reference {
  id: number;
  raw: string;
  authors: string | null;
  year: string | null;
  cite_year: string | null;
  title: string | null;
  container: string | null;
  volume: string | null;
  pages: string | null;
  doi: string | null;
  url: string | null;
  n_taxa?: number;
}

export interface Citation {
  text: string;
  confidence: number;
  candidates: number;
  reference: Reference | null;
}

export interface Citations {
  concept: Concept;
  topics: { topic: string; citations: Citation[] }[];
}

export interface ReferenceDetail extends Reference {
  cited_by: (Concept & { topics: string[] })[];
}

export interface SearchHit {
  kind: "concept" | "claim" | "reference";
  snippet: string;
  concept: Concept | null;
  reference_id?: number;
}

export interface Placement {
  name: string;
  a: string | null;
  b: string | null;
  a_id: number | null;
  b_id: number | null;
}

export interface Comparison {
  a: string;
  b: string;
  rank: string;
  parent_rank: string;
  counts: Record<"moved" | "only_a" | "only_b" | "same", number>;
  moved: Placement[];
  only_a: Placement[];
  only_b: Placement[];
  same: Placement[];
}

export interface CladeRow extends Concept {
  match: string | null;
  orders: string[];
}

export interface CladeAgreement {
  a: string;
  b: string;
  matched: CladeRow[];
  unmatched: CladeRow[];
}

export interface Conflict {
  child: string;
  child_id: number;
  parent: string;
  score: number;
  alternatives: Alternative[];
}

export interface Overview {
  sources: Source[];
  counts: Record<"concepts" | "claims" | "references" | "citations" | "citations_resolved", number>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function get<T>(path: string, params: Record<string, string | number | boolean | undefined | null> = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = `/api${path}${qs.size ? `?${qs}` : ""}`;
  const r = await fetch(url);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  overview: () => get<Overview>("/overview"),
  sources: () => get<Source[]>("/sources"),
  search: (q: string, source?: string, kind?: string) => get<SearchHit[]>("/search", { q, source, kind, limit: 40 }),
  suggest: (q: string, source?: string) => get<Concept[]>("/suggest", { q, source }),
  taxon: (id: number | string, source?: string) => get<Taxon>(`/taxa/${encodeURIComponent(id)}`, { source }),
  tree: (id: number | string, depth = 2, namedOnly = false, source?: string) =>
    get<TreeNode>(`/taxa/${encodeURIComponent(id)}/tree`, { depth, named_only: namedOnly, source }),
  citations: (id: number, topic?: string) => get<Citations>(`/taxa/${id}/citations`, { topic, exact: !!topic }),
  references: (q: string) => get<Reference[]>("/references", { q }),
  reference: (id: number) => get<ReferenceDetail>(`/references/${id}`),
  compare: (a: string, b: string, rank: string) => get<Comparison>("/compare", { a, b, rank }),
  clades: (a: string, b: string) => get<CladeAgreement>("/clades", { a, b }),
  conflicts: (source?: string) => get<Conflict[]>("/conflicts", { source }),
};
