"""APG wikitext parsing and the store/query layer, on synthetic data."""

import sqlite3

from apw import align, db, query
from apw.db import Store
from apw.sources.apg import ingest as apg

CLADOGRAM = """==Phylogeny==
{{clade
|label1=[[angiosperms]]
|1={{clade
   |1=[[Amborellales]]
   |2={{clade
      |1=[[Nymphaeales]]
      |label2=[[eudicots]]
      |2={{clade |1=[[Ranunculales]] |2=[[Proteales]] }}
      }}
   }}
}}
==References=="""

FAMILIES = """== Detailed version ==
=== [[Eudicots]] ===
* [[Ranunculales]] <small>Juss.</small>
** [[Papaveraceae]] <small>Juss., nom. cons.</small>
** [[Berberidaceae]] <small>Juss.</small> (including [[Nandinaceae]] <small>Horan.</small>)
** [[Sabiaceae]] <small>Blume</small>
=== Taxa of uncertain position ===
* [[Apodanthaceae]] <small>Takht.</small>
* ''[[Gumillea]]''
== Phylogeny =="""

OUTLINE = """==Groups==
*'''angiosperms :'''
*: [[paraphyletic]] grade '''[[basal angiosperms]]'''
*::: family [[Amborellaceae]]
*:: order [[Ranunculales]]
*:::: family [[Papaveraceae]] [+ family [[Fumariaceae]]]
*: clade '''[[eudicots]]'''
"""


def test_parse_cladogram():
    t = apg.find_cladogram(CLADOGRAM)
    assert t.label == "angiosperms"
    assert t.leaves() == ["Amborellales", "Nymphaeales", "Ranunculales", "Proteales"]
    eud = t.children[1].children[1]
    assert eud.label == "eudicots" and eud.leaves() == ["Ranunculales", "Proteales"]


def test_parse_family_lists():
    rows = apg.parse_family_lists(FAMILIES)
    by = {e.name: (h, o, e) for h, o, e in rows}
    assert by["Papaveraceae"][1] == "Ranunculales"
    assert by["Berberidaceae"][2].includes == ["Nandinaceae"]
    assert by["Apodanthaceae"][1] is None            # unplaced family, not an order
    assert "Gumillea" not in by


def test_parse_outline():
    es = apg.parse_outline(OUTLINE)
    kinds = [(e.kind, e.name) for e in es]
    assert ("family", "Amborellaceae") in kinds and ("order", "Ranunculales") in kinds
    assert ("container", "paraphyletic grade basal angiosperms") in kinds
    pap = next(e for e in es if e.name == "Papaveraceae")
    assert pap.optional == ["Fumariaceae"]


def _tiny_store():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    db.init(con)
    for key, fam_parent in (("old", "Alphales"), ("new", "Betales")):
        s = Store.for_source(con, key=key, family="test", version=key, title=key,
                             kind="classification")
        root = s.concept("n:root", "Root", rank="clade")
        a = s.concept("n:alphales", "Alphales", name="Alphales", rank="order")
        b = s.concept("n:betales", "Betales", name="Betales", rank="order")
        g = s.concept("n:gammales", "Gammales", name="Gammales", rank="order")
        ab = s.concept("s:ab", "[Alphales + Betales]", rank="clade", informal=True)
        f = s.concept("n:fooaceae", "Fooaceae", name="Fooaceae", rank="family")
        s.claim(ab, "child_of", object_id=root)
        s.claim(g, "child_of", object_id=root)
        s.claim(a, "child_of", object_id=ab)
        s.claim(b, "child_of", object_id=ab)
        s.claim(f, "child_of", object_id={"Alphales": a, "Betales": b}[fam_parent])
        s.claim(f, "has_character", value="flowers blue", qualifiers={"apomorphy": True})
    con.commit()
    return con


def test_compare_and_align():
    con = _tiny_store()
    res = query.compare_placements(con, "old", "new")
    assert [(p.name, p.a, p.b) for p in res["moved"]] == [("Fooaceae", "Alphales", "Betales")]
    stats = align.align(con)
    assert stats["congruent"] == 4      # Root and [Alphales + Betales], in both directions
    agree = query.clade_agreement(con, "old", "new")
    assert {r["label"] for r in agree["matched"]} >= {"[Alphales + Betales]"}


def test_find_and_search():
    con = _tiny_store()
    (row,) = query.find(con, "FOOACEAE", "new")
    assert row["label"] == "Fooaceae"
    hits = query.search(con, "flowers blue", kind="claim", source="old")
    assert hits and hits[0]["concept"]["label"] == "Fooaceae"


def test_store_replace_source_clears_search_index():
    con = _tiny_store()
    before = con.execute("SELECT count(*) FROM search").fetchone()[0]
    Store.for_source(con, key="old", family="test", title="old", kind="classification")
    after = con.execute("SELECT count(*) FROM search").fetchone()[0]
    assert after < before and query.find(con, "Fooaceae", "old") == []
