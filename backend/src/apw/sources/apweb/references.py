"""Parse APWeb bibliography pages into structured references.

Entry shape (one <p> each):
    Alverson, W. S. [et al. 1999], Whitlock, B. A., ... & Baum, D. A. 1999. Title. <i>Journal</i> 86: 1474-1486.
    Benton, M. J. [et al. 2021], Wilf, P., & Sauquet, H. 2022 [= 2021]. Title ...
The bracketed '[et al. Y]' / '[= Y]' give the year as cited in APWeb's text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from ...db import norm

YEAR = r"(?:1[5-9]\d\d|20\d\d)"
ENTRY = re.compile(
    rf"^(?P<authors>.+?),?\s+(?P<year>{YEAR})(?P<suffix>[a-z]{{0,2}})"
    rf"(?:\s*\[\s*=\s*(?P<eq>{YEAR}[a-z]{{0,2}})\s*\])?\.\s+(?P<rest>.+)$")
ETAL = re.compile(rf"\[et al\.?,?\s*(?P<y>{YEAR}[a-z]{{0,2}})?\s*\]")
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)])")
CONTAINER = re.compile(
    r"^(?P<container>.+?),?\s+(?P<volume>\d[\w\-/]*(?:\s*\([\w\-/ ]+\))?):\s*(?P<pages>[\w\-–, ]+?)\.?$")
URL = re.compile(r"\s*(?:https?://|doi:)\S+")


@dataclass
class Reference:
    local_id: str
    raw: str
    first_author: str
    initials: str | None
    second_norm: str | None
    n_authors: int
    authors: str
    year: str
    cite_year: str
    title: str | None
    container: str | None
    volume: str | None
    pages: str | None
    doi: str | None
    anchor: str | None

    @property
    def first_norm(self) -> str:
        return norm(self.first_author)


def _split_title(rest: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Best-effort split of 'Title. Journal vol: pages.'; the raw string is kept regardless."""
    m = re.match(r"^(?P<title>.+?[.?!])\s+(?P<tail>[^.].*)$", rest)
    if not m:
        return rest.strip(), None, None, None
    title, tail = m["title"].strip(), URL.sub("", m["tail"]).strip()
    c = CONTAINER.match(tail)
    if c and c["pages"] and re.search(r"\d", c["pages"]):
        return title, c["container"].strip(" ,"), (c["volume"] or "").strip() or None, c["pages"].strip()
    return title, tail.rstrip(". ") or None, None, None


def parse_entry(text: str, anchor: str | None = None) -> Reference | None:
    text = re.sub(r"\s+", " ", text).strip()
    m = ENTRY.match(text)
    if not m:
        return None
    authors = m["authors"].strip()
    etal = ETAL.search(authors)
    cite_year = m["eq"] or (etal["y"] if etal and etal["y"] else None) or (m["year"] + m["suffix"])
    authors_clean = ETAL.sub("", authors).replace(" ,", ",").strip(" ,")

    first, _, after = authors_clean.partition(",")
    first = first.strip()
    if not first or len(first) > 60:
        return None
    im = re.match(r"\s*((?:[A-Z][a-z]?\.?[\s-]*)+)", after)
    initials = re.sub(r"[\s.]", "", im[1]) if im else None

    if etal:
        n_authors, second = 3, None
    elif "&" in authors_clean:
        second_part = authors_clean.split("&", 1)[1].strip()
        second = norm(second_part.split(",")[0])
        before = authors_clean.split("&", 1)[0]
        # 'Surname, I. I., & Second, J.' -> exactly one comma-pair before '&'
        n_authors = 2 if before.count(",") <= 2 else 3
        if n_authors == 3:
            second = None
    else:
        n_authors, second = 1, None

    title, container, volume, pages = _split_title(m["rest"])
    doi = DOI.search(text)
    digest = hashlib.sha1(text.encode()).hexdigest()[:8]
    return Reference(
        local_id=anchor or f"{norm(first).replace(' ', '_')}-{cite_year}-{digest}",
        raw=text, first_author=first, initials=initials, second_norm=second,
        n_authors=n_authors, authors=authors_clean, year=m["year"], cite_year=cite_year,
        title=title, container=container, volume=volume, pages=pages,
        doi=doi[1] if doi else None, anchor=anchor)


def parse_page(html: str) -> tuple[list[Reference], int]:
    """Return (references, number of skipped paragraphs)."""
    soup = BeautifulSoup(html, "lxml")
    refs, skipped, seen = [], 0, set()
    for p in soup.find_all("p"):
        text = p.get_text("", strip=False)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 15 or text.startswith("Back to"):
            continue
        a = p.find("a", attrs={"name": True})
        ref = parse_entry(text, a["name"] if a else None)
        if ref:
            if ref.local_id in seen:  # duplicated anchors/entries occur
                ref.local_id += f"~{len(refs)}"
            seen.add(ref.local_id)
            refs.append(ref)
        else:
            skipped += 1
    return refs, skipped
