-- Angiosperm phylogeny knowledge store.
--
-- Core idea: every fact is a CLAIM made by a SOURCE about a taxon CONCEPT
-- (a name *sensu* that source), optionally backed by CITATIONS to literature.
-- Sources never overwrite each other; synthesis happens by comparing claims
-- and aligning concepts across sources.

PRAGMA foreign_keys = ON;

-- A provider of claims: a website snapshot, a classification, a paper, a database.
CREATE TABLE IF NOT EXISTS source (
    id           INTEGER PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,      -- e.g. 'apweb@2026-10-29', 'apg4'
    family       TEXT NOT NULL,             -- groups versions: 'apweb', 'apg'
    version      TEXT,                      -- '2026-10-29', 'IV'
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,             -- website | classification | paper | database | tree
    url          TEXT,
    citation     TEXT,                      -- how to cite the source itself
    license      TEXT,
    retrieved_at TEXT,
    notes        TEXT
);

-- Raw documents fetched for a source (cached locally, never redistributed).
CREATE TABLE IF NOT EXISTS document (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,
    path          TEXT NOT NULL,            -- local cache path, relative to data dir
    sha256        TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    last_modified TEXT,                     -- HTTP Last-Modified, if given
    UNIQUE (source_id, url)
);

-- A taxon concept: a name (or informal clade) as circumscribed by one source.
CREATE TABLE IF NOT EXISTS concept (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    local_id    TEXT NOT NULL,              -- stable key within the source
    label       TEXT NOT NULL,              -- display label
    name        TEXT,                       -- scientific name, if the concept has one
    norm        TEXT NOT NULL,              -- normalized label for matching
    authorship  TEXT,
    rank        TEXT,                       -- order | family | subfamily | tribe | clade | ...
    informal    INTEGER NOT NULL DEFAULT 0, -- 1 for e.g. '[Cisseae + Cayratieae]'
    url         TEXT,                       -- deep link into the source
    UNIQUE (source_id, local_id)
);
CREATE INDEX IF NOT EXISTS concept_norm ON concept(norm);
CREATE INDEX IF NOT EXISTS concept_name ON concept(name);

-- Additional labels for a concept (e.g. 'CORE EUDICOTS' / 'GUNNERIDAE').
CREATE TABLE IF NOT EXISTS concept_label (
    concept_id  INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,
    norm        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'alternative',  -- alternative | synonym | anchor
    PRIMARY KEY (concept_id, norm, kind)
);
CREATE INDEX IF NOT EXISTS concept_label_norm ON concept_label(norm);

-- Controlled vocabulary of predicates.
CREATE TABLE IF NOT EXISTS predicate (
    name         TEXT PRIMARY KEY,
    object_type  TEXT NOT NULL,             -- concept | literal
    description  TEXT NOT NULL
);

-- An atomic statement by a source.
CREATE TABLE IF NOT EXISTS claim (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    subject_id  INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    predicate   TEXT NOT NULL REFERENCES predicate(name),
    object_id   INTEGER REFERENCES concept(id) ON DELETE CASCADE,
    value       TEXT,                       -- literal object
    qualifiers  TEXT,                       -- JSON object
    locator     TEXT,                       -- URL (with #anchor) where the claim is made
    CHECK (object_id IS NOT NULL OR value IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS claim_subject ON claim(subject_id, predicate);
CREATE INDEX IF NOT EXISTS claim_object ON claim(object_id, predicate);
CREATE INDEX IF NOT EXISTS claim_source ON claim(source_id, predicate);

-- A bibliographic work.
CREATE TABLE IF NOT EXISTS reference (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    local_id    TEXT NOT NULL,
    first_author TEXT NOT NULL,             -- surname, as printed
    first_norm  TEXT NOT NULL,              -- normalized surname
    initials    TEXT,
    second_norm TEXT,                       -- normalized 2nd author surname (2-author works)
    n_authors   INTEGER,
    authors     TEXT,                       -- author string, as printed
    year        TEXT,                       -- publication year as printed, '2022'
    cite_year   TEXT,                       -- year+suffix used in in-text citations, '2021b'
    title       TEXT,
    container   TEXT,                       -- journal / book
    volume      TEXT,
    pages       TEXT,
    doi         TEXT,
    raw         TEXT NOT NULL,              -- full citation string
    url         TEXT,
    UNIQUE (source_id, local_id)
);
CREATE INDEX IF NOT EXISTS reference_key ON reference(first_norm, cite_year);
CREATE INDEX IF NOT EXISTS reference_doi ON reference(doi);

-- A literature citation supporting a claim. reference_id is NULL when the
-- in-text citation could not be resolved unambiguously.
CREATE TABLE IF NOT EXISTS citation (
    id           INTEGER PRIMARY KEY,
    claim_id     INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    reference_id INTEGER REFERENCES reference(id) ON DELETE SET NULL,
    text         TEXT NOT NULL,             -- the in-text citation, e.g. 'Wen et al. (2013)'
    confidence   REAL NOT NULL DEFAULT 1.0,
    candidates   INTEGER NOT NULL DEFAULT 1 -- number of matching references found
);
CREATE INDEX IF NOT EXISTS citation_claim ON citation(claim_id);
CREATE INDEX IF NOT EXISTS citation_reference ON citation(reference_id);

-- Relationships between concepts from different sources (RCC-5 style).
CREATE TABLE IF NOT EXISTS alignment (
    a_id        INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    b_id        INTEGER NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    relation    TEXT NOT NULL,              -- same_name | congruent | includes | included_in | overlaps | disjoint
    method      TEXT NOT NULL,              -- name | circumscription | manual
    confidence  REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (a_id, b_id, relation)
);

-- Identifiers in external systems (WFO, IPNI, OTT, GBIF, NCBI, DOI ...).
CREATE TABLE IF NOT EXISTS external_id (
    entity_type TEXT NOT NULL,              -- concept | reference
    entity_id   INTEGER NOT NULL,
    scheme      TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, scheme)
);

-- Full-text index over labels, claim values and references.
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
    kind UNINDEXED,                         -- concept | claim | reference
    entity_id UNINDEXED,
    text,
    tokenize = 'porter unicode61 remove_diacritics 2'
);
