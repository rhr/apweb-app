"""SQLite access and the ``Store`` helper that source ingesters write through."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from importlib import resources
from pathlib import Path
from typing import Any

from . import config

PREDICATES = {
    # name: (object_type, description)
    "child_of": ("concept", "Subject is an immediate member/subclade of object."),
    "has_character": ("literal", "Character state in the subject's characterization. "
                      "Qualifiers: apomorphy, uncertain, position."),
    "family_count": ("literal", "Number of families."),
    "genus_count": ("literal", "Number of genera."),
    "species_count": ("literal", "Number of species."),
    "distribution": ("literal", "Geographic distribution summary."),
    "synonym": ("literal", "A name treated as a synonym of the subject."),
    "discussed_in": ("literal", "Literature discussing the subject on a topic "
                     "(value = topic, citations attached)."),
}


def norm(s: str) -> str:
    """Normalize a label for matching: strip diacritics, punctuation, case."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("†", "").replace("&", "+").lower()
    s = re.sub(r"[^\w+\[\] ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(resources.files("apw").joinpath("schema.sql").read_text())
    con.executemany(
        "INSERT OR REPLACE INTO predicate(name, object_type, description) VALUES (?,?,?)",
        [(k, t, d) for k, (t, d) in PREDICATES.items()],
    )
    con.commit()


class Store:
    """Write API used by ingesters. One Store per source being (re)ingested."""

    def __init__(self, con: sqlite3.Connection, source_id: int):
        self.con = con
        self.source_id = source_id

    @classmethod
    def for_source(cls, con: sqlite3.Connection, *, key: str, replace: bool = True,
                   **fields: Any) -> "Store":
        """Register a source. With replace=True, previous data for it is deleted."""
        row = con.execute("SELECT id FROM source WHERE key = ?", (key,)).fetchone()
        if row and replace:
            sid = row["id"]
            # FTS rows aren't covered by FK cascades.
            for kind, table in (("concept", "concept"), ("claim", "claim"),
                                ("reference", "reference")):
                con.execute(
                    f"DELETE FROM search WHERE kind = ? AND entity_id IN "
                    f"(SELECT id FROM {table} WHERE source_id = ?)", (kind, sid))
            con.execute("DELETE FROM source WHERE id = ?", (sid,))
            row = None
        if row:
            return cls(con, row["id"])
        cols = ["key", *fields]
        cur = con.execute(
            f"INSERT INTO source({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [key, *fields.values()],
        )
        return cls(con, cur.lastrowid)

    def document(self, *, url: str, path: str, sha256: str, fetched_at: str,
                 last_modified: str | None) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO document(source_id, url, path, sha256, fetched_at, last_modified)"
            " VALUES (?,?,?,?,?,?)",
            (self.source_id, url, path, sha256, fetched_at, last_modified))

    def concept(self, local_id: str, label: str, *, name: str | None = None,
                authorship: str | None = None, rank: str | None = None,
                informal: bool = False, url: str | None = None) -> int:
        """Get or create a concept; non-null fields update an existing one."""
        row = self.con.execute(
            "SELECT id FROM concept WHERE source_id = ? AND local_id = ?",
            (self.source_id, local_id)).fetchone()
        if row:
            cid = row["id"]
            self.con.execute(
                "UPDATE concept SET name = COALESCE(?, name), authorship = COALESCE(?, authorship),"
                " rank = COALESCE(?, rank), url = COALESCE(url, ?) WHERE id = ?",
                (name, authorship, rank, url, cid))
            return cid
        cur = self.con.execute(
            "INSERT INTO concept(source_id, local_id, label, name, norm, authorship, rank, informal, url)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.source_id, local_id, label, name, norm(label), authorship, rank,
             int(informal), url))
        self.con.execute("INSERT INTO search(kind, entity_id, text) VALUES ('concept', ?, ?)",
                         (cur.lastrowid, label))
        return cur.lastrowid

    def label(self, concept_id: int, label: str, kind: str = "alternative") -> None:
        cur = self.con.execute(
            "INSERT OR IGNORE INTO concept_label(concept_id, label, norm, kind) VALUES (?,?,?,?)",
            (concept_id, label, norm(label), kind))
        if cur.rowcount and kind != "anchor":
            self.con.execute("INSERT INTO search(kind, entity_id, text) VALUES ('concept', ?, ?)",
                             (concept_id, label))

    def claim(self, subject_id: int, predicate: str, *, object_id: int | None = None,
              value: str | None = None, qualifiers: dict | None = None,
              locator: str | None = None, index: bool = True) -> int:
        cur = self.con.execute(
            "INSERT INTO claim(source_id, subject_id, predicate, object_id, value, qualifiers, locator)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.source_id, subject_id, predicate, object_id, value,
             json.dumps(qualifiers, ensure_ascii=False) if qualifiers else None, locator))
        if index and value:
            self.con.execute("INSERT INTO search(kind, entity_id, text) VALUES ('claim', ?, ?)",
                             (cur.lastrowid, value))
        return cur.lastrowid

    def reference(self, local_id: str, raw: str, **fields: Any) -> int:
        cols = ["source_id", "local_id", "raw", *fields]
        cur = self.con.execute(
            f"INSERT INTO reference({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [self.source_id, local_id, raw, *fields.values()])
        self.con.execute("INSERT INTO search(kind, entity_id, text) VALUES ('reference', ?, ?)",
                         (cur.lastrowid, raw))
        return cur.lastrowid

    def citation(self, claim_id: int, text: str, reference_id: int | None,
                 confidence: float = 1.0, candidates: int = 1) -> None:
        self.con.execute(
            "INSERT INTO citation(claim_id, reference_id, text, confidence, candidates)"
            " VALUES (?,?,?,?,?)", (claim_id, reference_id, text, confidence, candidates))
