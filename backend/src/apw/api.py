"""HTTP API over the store (`apw serve`). Interactive docs at /api/docs.

If the frontend has been built (frontend/dist), it is served at / as well.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, service

app = FastAPI(title="Angiosperm phylogeny knowledge store", version="0.2.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@contextmanager
def _con() -> Iterator:
    con = db.connect_ro()
    try:
        yield con
    except (service.NotFound, LookupError) as e:
        raise HTTPException(404, str(e).strip("'\""))
    finally:
        con.close()


SourceQ = Query(None, description="Source key (e.g. 'apg4') or family ('apweb' = latest snapshot)")


@app.get("/api/overview")
def overview():
    with _con() as con:
        return service.overview(con)


@app.get("/api/sources")
def sources():
    with _con() as con:
        return service.sources(con)


@app.get("/api/search")
def search(q: str, kind: Literal["concept", "claim", "reference"] | None = None,
           source: str | None = SourceQ, limit: int = Query(20, le=100)):
    with _con() as con:
        return service.search(con, q, kind, source, limit)


@app.get("/api/suggest")
def suggest(q: str, source: str | None = SourceQ, limit: int = Query(12, le=50)):
    with _con() as con:
        return service.suggest(con, q, source, limit)


@app.get("/api/taxa/{ref}")
def taxon(ref: str, source: str | None = SourceQ, max_characters: int | None = None):
    """A taxon by concept id, or by name within `source`."""
    with _con() as con:
        return service.taxon(con, ref, source, max_characters)


@app.get("/api/taxa/{ref}/tree")
def tree(ref: str, source: str | None = SourceQ, depth: int = Query(2, ge=0, le=6),
         named_only: bool = False):
    with _con() as con:
        return service.subtree(con, ref, source, depth, named_only)


@app.get("/api/taxa/{ref}/citations")
def citations(ref: str, source: str | None = SourceQ, topic: str | None = None,
              exact: bool = Query(False, description="Match the topic exactly")):
    with _con() as con:
        return service.citations(con, ref, source, topic, exact)


@app.get("/api/references")
def references(q: str, limit: int = Query(20, le=100)):
    with _con() as con:
        return service.find_references(con, q, limit)


@app.get("/api/references/{reference_id}")
def reference(reference_id: int):
    with _con() as con:
        return service.reference(con, reference_id)


@app.get("/api/compare")
def compare(a: str, b: str, rank: Literal["family", "order"] = "family"):
    with _con() as con:
        return service.compare(con, a, b, rank)


@app.get("/api/clades")
def clades(a: str, b: str):
    with _con() as con:
        return service.clades(con, a, b)


@app.get("/api/conflicts")
def conflicts(source: str | None = SourceQ, limit: int = Query(200, le=1000)):
    with _con() as con:
        return service.conflicts(con, source, limit)


class Question(BaseModel):
    question: str
    conversation_id: str | None = None


@app.get("/api/ask/status")
def ask_status():
    from . import ask
    return ask.status()


@app.post("/api/ask")
async def ask_question(q: Question):
    """Answer a question with Claude and the store's tools, streamed as Server-Sent Events
    (one JSON object per `data:` line; see apw.ask for the event types)."""
    from . import ask

    async def events():
        async for e in ask.ask(q.question, q.conversation_id):
            yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _mount_frontend() -> None:
    dist = Path(config.REPO_ROOT) / "frontend" / "dist"
    if not (dist / "index.html").exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = dist / path
        if path and f.is_file() and dist in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(dist / "index.html")      # client-side routes


_mount_frontend()
