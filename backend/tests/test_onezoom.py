"""OneZoom adapter: the bracket string must parse, the way OneZoom's viewer parses it,
back into the source's tree."""

import os

import pytest
from fastapi.testclient import TestClient

from apw import config, db, onezoom, query
from apw.db import Store


def parse(s: str) -> list[tuple[str, int, list]]:
    """Re-implements OneZoom's Midnode child split (get_cuts) and OZid numbering.
    Returns the tree as nested (kind, ozid, children), kind '(' / '{' / 'leaf'."""
    opens_before = [0]
    for ch in s:
        opens_before.append(opens_before[-1] + (ch in "({"))

    def node(start: int, end: int, leaf_start: int) -> tuple[tuple, int]:
        count, cut = 0, None
        for i in range(end, start - 1, -1):         # as in midnode.js get_cuts
            count += 1 if s[i] in ")}" else -1
            if count == 1 and s[i] in "({":
                cut = i - 1
                break
        kids, leaf = [], leaf_start
        ranges = [(start + 1, cut), (cut + 1, end - 1)] if cut is not None else [None, None]
        for r in ranges:
            if r is None or r[1] < r[0]:
                kids.append(("leaf", -leaf, []))
                leaf += 1
            else:
                child, leaf = node(r[0], r[1], leaf)
                kids.append(child)
        return (s[start], opens_before[start] + 1, kids), leaf

    return node(0, len(s) - 1, 1)[0]


def edges_from_string(oz: onezoom.OzTree) -> set[tuple]:
    """(parent, child) pairs between drawn nodes, with `{` splits merged into their parent."""
    out = set()

    def ids(n):
        return tuple(oz.leaves[-n[1]] if n[0] == "leaf" else oz.nodes[n[1]])

    def walk(n, parent):
        me = parent if n[0] == "{" else ids(n)
        if n[0] != "{" and parent is not None:
            out.add((parent, me))
        for k in n[2]:
            walk(k, me)

    walk(parse(oz.raw), None)
    return out


def expected_edges(t: query.Tree, oz: onezoom.OzTree) -> set[tuple]:
    def drawn(c):
        i = oz.ozid[c]
        return tuple(oz.nodes[i] if i > 0 else oz.leaves[-i])

    out = set()
    for child, parent in t.parent.items():
        a, b = drawn(parent), drawn(child)
        if a != b:
            out.add((a, b))
    roots = [r for r in t.roots() if r in oz.ozid]
    if oz.nodes[1] == []:                       # several roots under an added one
        out |= {((), drawn(r)) for r in roots}
    return out


def roundtrip(con, source_id):
    t = query.tree_for(con, source_id)
    oz = onezoom.build(con, t)
    assert set(oz.raw) <= set("(){}")
    assert len(oz.leaves) - 1 == len(oz.raw) // 2 + 1  # strictly bifurcating
    assert {c for c in t.concepts if c in t.parent or t.children.get(c)} <= set(oz.ozid)
    assert edges_from_string(oz) == expected_edges(t, oz)
    return t, oz


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "t.sqlite"
    monkeypatch.setenv("APW_DB", str(path))
    con = db.connect(path)
    db.init(con)
    s = Store.for_source(con, key="t", family="test", version="1", title="t", kind="classification")

    def c(label, parent=None, spp=None):
        cid = s.concept(f"n:{label}", label, name=label, rank="clade")
        if parent:
            s.claim(cid, "child_of", object_id=parent)
        if spp:
            s.claim(cid, "species_count", value=str(spp), index=False)
        return cid

    root = c("Root")
    poly = c("Poly", root)                      # four children: two tips, two clades
    for tip in ("Tip1", "Tip2"):
        c(tip, poly, spp=3)
    for clade in ("CladeA", "CladeB"):
        x = c(clade, poly)
        c(clade + "1", x, spp=2)
        c(clade + "2", x, spp=5)
    order = c("Vitales", root)                  # single-child chain
    fam = c("Vitaceae", order)
    c("Leeoideae", fam, spp=34)
    c("Vitoideae", fam, spp=900)
    c("Lonely", root)
    con.commit()
    yield con, s.source_id
    con.close()


def test_roundtrip_and_details(store):
    con, sid = store
    t, oz = roundtrip(con, sid)
    by_label = {v["label"]: k for k, v in t.concepts.items()}
    assert oz.ozid[by_label["Root"]] == 1
    assert oz.ozid[by_label["Vitales"]] == oz.ozid[by_label["Vitaceae"]] > 0
    assert oz.species[by_label["Root"]] == 3 + 3 + 7 + 7 + 934

    d = onezoom.node_details(con, oz, [oz.ozid[by_label["Vitales"]]], [-oz.ozid[by_label["Tip1"]]])
    node, leaf = d["nodes"][0], d["leaves"][0]
    assert node[d["colnames_nodes"]["name"]] == "Vitales / Vitaceae"
    assert node[d["colnames_nodes"]["ott"]] == by_label["Vitaceae"]
    assert node[d["colnames_nodes"]["popularity"]] == 934
    assert leaf[d["colnames_leaves"]["name"]] == "Tip1"


def test_several_roots_get_a_shared_root(store):
    con, sid = store
    s = Store(con, sid)
    other = s.concept("n:Other", "Other", rank="clade")
    for k in ("x", "y"):
        s.claim(s.concept(f"n:{k}", k, rank="clade"), "child_of", object_id=other)
    con.commit()
    t, oz = roundtrip(con, sid)
    assert oz.nodes[1] == []
    assert len(t.roots()) == 2


def test_cut_maps_match_scan(store):
    con, sid = store
    _, oz = roundtrip(con, sid)
    binary = onezoom._binary_cut_map(oz.raw, 0)
    poly = onezoom._polytomy_cut_map(oz.raw, 0)
    assert binary[len(oz.raw) - 1] < len(oz.raw) - 1
    assert len(poly[len(oz.raw) - 1]) == 2 * 3     # root has three children


def test_api(store):
    from apw.api import app
    client = TestClient(app)
    js = client.get("/api/oz/t/completetree.js")
    assert js.headers["content-type"].startswith("text/javascript")
    assert js.text.startswith("var rawData = '(")
    assert "cut_threshold" in client.get("/api/oz/t/cut_position_map.js").text
    head = client.get("/api/oz/t/node_details").json()
    assert head["nodes"] == [] and "ott" in head["colnames_leaves"]
    root = client.get("/api/oz/t/node_details", params={"node_ids": "1,99999"}).json()
    assert [n[4] for n in root["nodes"]] == ["Root"]
    cid = client.get("/api/taxa/Vitaceae", params={"source": "t"}).json()["id"]
    assert client.get(f"/api/oz/t/locate/{cid}").json()["ozid"] > 0
    assert client.get("/api/oz/t/node_details", params={"node_ids": "a"}).status_code == 400


@pytest.mark.skipif(not config.db_path().exists() or "APW_DB" in os.environ,
                    reason="needs the built store")
def test_roundtrip_real_sources():
    con = db.connect_ro()
    for s in query.sources(con):
        roundtrip(con, s["id"])
