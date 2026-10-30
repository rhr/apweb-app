"""Polite crawler for APWeb. Raw pages go to a local, dated cache (never redistributed).

Layout: <data>/raw/apweb/<snapshot>/<path relative to BASE>, plus manifest.json.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urldefrag

import httpx
from rich.console import Console

from ... import config

BASE = "https://www.mobot.org/mobot/research/apweb/"
INDEX_PAGES = ["ordersside.html"]
REFERENCE_PAGES = [f"top/references{r}.html" for r in
                   ("a_d", "e_h", "i_l", "m_p", "q_s", "t_z")]

console = Console(stderr=True)


def snapshot_dir(snapshot: str) -> Path:
    return config.data_dir() / "raw" / "apweb" / snapshot


def latest_snapshot() -> str | None:
    root = config.data_dir() / "raw" / "apweb"
    snaps = sorted(p.name for p in root.glob("*") if (p / "manifest.json").exists())
    return snaps[-1] if snaps else None


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def order_pages(index_html: str) -> list[str]:
    """Order-page paths (relative to BASE) linked from the sidebar."""
    paths = {urldefrag(h)[0] for h in re.findall(r'href="(orders/[^"#]+)', index_html)}
    # The sidebar links both 'Cycadales.html' and 'cycadales.html'; the server is
    # case-sensitive, so keep both spellings and let 404s drop out.
    return sorted(paths)


def crawl(snapshot: str | None = None, delay: float = 2.0, force: bool = False) -> Path:
    snapshot = snapshot or dt.date.today().isoformat()
    out = snapshot_dir(snapshot)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    with httpx.Client(headers={"User-Agent": config.user_agent()}, timeout=60,
                      follow_redirects=True) as client:

        def fetch(rel: str) -> bytes | None:
            dest = out / rel
            if dest.exists() and rel in manifest and not force:
                return dest.read_bytes()
            url = urljoin(BASE, rel)
            r = client.get(url)
            time.sleep(delay)
            if r.status_code != 200:
                console.print(f"[yellow]{r.status_code}[/] {url}")
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            manifest[rel] = {
                "url": url,
                "sha256": hashlib.sha256(r.content).hexdigest(),
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "last_modified": r.headers.get("last-modified"),
            }
            manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
            console.print(f"[green]ok[/] {rel} ({len(r.content) // 1024} kB)")
            return r.content

        index = fetch(INDEX_PAGES[0])
        if index is None:
            raise RuntimeError("could not fetch APWeb index")
        # Follow links between order pages too: some (e.g. Dasypogonales) aren't in the sidebar.
        queue, seen = order_pages(decode(index)), set()
        while queue:
            rel = queue.pop(0)
            if rel in seen:
                continue
            seen.add(rel)
            page = fetch(rel)
            if page is not None:
                queue.extend(p for p in linked_order_pages(decode(page)) if p not in seen)
        for rel in REFERENCE_PAGES:
            fetch(rel)
    return out


def linked_order_pages(html: str) -> list[str]:
    """Order pages linked from an order page ('../orders/x.htm' or sibling 'x.htm')."""
    out = set()
    for href in re.findall(r'href="([^"#]+?\.html?)(?:#[^"]*)?"', html, re.I):
        if href.startswith("../orders/"):
            out.add("orders/" + href.removeprefix("../orders/"))
        elif "/" not in href and ":" not in href:
            out.add("orders/" + href)
    return sorted(out)
