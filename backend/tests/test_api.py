"""HTTP API and MCP tools against a small on-disk store."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from apw import db
from apw.db import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "t.sqlite"
    monkeypatch.setenv("APW_DB", str(path))
    con = db.connect(path)
    db.init(con)
    for key, order in (("old", "Alphales"), ("new", "Betales")):
        s = Store.for_source(con, key=key, family="test", version=key, title=key, kind="classification")
        root = s.concept("n:root", "Root", rank="clade")
        a = s.concept("n:alphales", "Alphales", name="Alphales", rank="order")
        b = s.concept("n:betales", "Betales", name="Betales", rank="order")
        ab = s.concept("s:ab", "[Alphales + Betales]", rank="clade", informal=True)
        f = s.concept("n:fooaceae", "Fooaceae", name="Fooaceae", rank="family")
        s.claim(ab, "child_of", object_id=root)
        s.claim(a, "child_of", object_id=ab)
        s.claim(b, "child_of", object_id=ab)
        s.claim(f, "child_of", object_id=a if order == "Alphales" else b)
        s.claim(f, "has_character", value="flowers blue", qualifiers={"apomorphy": True, "apo": ["flowers blue"]})
        s.claim(f, "species_count", value="12", index=False)
    con.commit()
    from apw import align
    align.align(con)
    con.close()
    from apw.api import app
    return TestClient(app)


def test_sources_and_taxon(client):
    srcs = client.get("/api/sources").json()
    assert {s["key"] for s in srcs} == {"old", "new"}
    assert srcs[0]["roots"][0]["label"] == "Root"
    t = client.get("/api/taxa/Fooaceae", params={"source": "new"}).json()
    assert [c["label"] for c in t["lineage"]] == ["Betales", "[Alphales + Betales]", "Root"]
    assert t["stats"] == {"species_count": "12"}
    assert t["characters"][0]["apomorphy"] is True
    other = {o["source"]: o for o in t["other_sources"]}
    assert other["old"]["order"]["label"] == "Alphales"


def test_tree_named_only_skips_informal(client):
    root = client.get("/api/taxa/Root/tree", params={"source": "new", "depth": 3}).json()
    assert [c["label"] for c in root["children"]] == ["[Alphales + Betales]"]
    named = client.get("/api/taxa/Root/tree", params={"source": "new", "depth": 3, "named_only": True}).json()
    assert [c["label"] for c in named["children"]] == ["Alphales", "Betales"]
    assert named["children"][1]["children"][0]["label"] == "Fooaceae"


def test_compare_and_errors(client):
    c = client.get("/api/compare", params={"a": "old", "b": "new"}).json()
    assert c["counts"]["moved"] == 1 and c["moved"][0]["a"] == "Alphales"
    assert c["moved"][0]["a_id"] and c["moved"][0]["b_id"]
    assert client.get("/api/taxa/Nosuchaceae", params={"source": "new"}).status_code == 404
    assert client.get("/api/compare", params={"a": "nope", "b": "new"}).status_code == 404


def test_search_and_suggest(client):
    hits = client.get("/api/search", params={"q": "flowers blue", "source": "old"}).json()
    assert hits[0]["concept"]["label"] == "Fooaceae"
    sug = client.get("/api/suggest", params={"q": "alph", "source": "new"}).json()
    assert [s["label"] for s in sug] == ["Alphales"]


def test_mcp_tools(client):
    from apw.mcp_server import server

    async def call():
        tools = {t.name for t in await server.list_tools()}
        r = await server.call_tool("compare_classifications", {"a": "old", "b": "new"})
        return tools, r

    tools, r = asyncio.run(call())
    assert {"get_taxon", "search", "compare_classifications", "get_literature"} <= tools
    data = r.structured_content or json.loads(r.content[0].text)
    data = data.get("result", data)
    assert data["counts"]["moved"] == 1
