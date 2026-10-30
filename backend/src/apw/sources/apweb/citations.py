"""Find author-year citations in APWeb prose and resolve them to bibliography entries.

Handles e.g.  Wen et al. (2013) | (Magallón et al. 2018; Wheeler et al. 2017) |
Prakash & Dayal 1964 | Magallón and Castillo (2009) | Endress (2010c) | Smith (2001, 2003b) |
N. Adams et al. (2016) | Jaillon, Aury et al. (2007) | Moline et al. (2013: HPD)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ...db import norm

_PARTICLE = r"(?:(?:de|De|van|Van|von|Von|der|den|da|Da|dos|du|Du|la|La|le|Le|del|Del|ter|d'|D')\s?)"
_SURNAME = rf"{_PARTICLE}*[A-ZÀ-ÖØ-Þ][\w'’]*(?:-[A-ZÀ-ÖØ-Þ]?[\w'’]+)*"
_YEAR = r"(?:1[6-9]\d\d|20\d\d)[a-z]?"
_YEARS = rf"{_YEAR}(?:\s*,\s*(?:{_YEAR}|[a-z]\b))*"

CITE = re.compile(
    rf"(?:(?P<init>(?:[A-Z]\.[\s-]?)+)\s?|(?P<given>[A-Z][a-z]+)\s)?"
    rf"(?P<a1>{_SURNAME})"
    rf"(?:(?:,\s(?P<a1b>{_SURNAME}))?\s+et\s+al\.?(?P<etal>)|\s+(?:and|&)\s+(?P<a2>{_SURNAME}))?"
    rf"(?:\s*\((?P<yp>{_YEARS})(?=[\s):;,])|\s+(?P<yb>{_YEARS})\b)")

# Capitalized words that precede years but aren't authors.
STOP = {w.lower() for w in """In By Since From Until Before After About Ca The Up To Of Ma My Mya
    Figure Fig Table See Also And Or Version Online Accessed Consulted January February March
    April May June July August September October November December Spring Summer Autumn Winter
    Early Late Mid Middle Cretaceous Eocene Miocene Oligocene Paleocene Pliocene Jurassic Kew
    Note Here This These That Australia Africa America China Brazil Mexico Europe Asia""".split()}


@dataclass
class Mention:
    text: str
    first: str                  # normalized first-author surname
    years: list[str]
    given: str | None = None    # preceding capitalized word, maybe part of the surname ('Venkata Rao')
    etal: bool = False
    second: str | None = None   # normalized 2nd surname for 2-author works
    also: str | None = None     # 'Jaillon, Aury et al.' -> 'aury'
    initials: str | None = None
    span: tuple[int, int] = (0, 0)


def _expand_years(s: str) -> list[str]:
    out, last = [], None
    for tok in re.split(r"\s*,\s*", s):
        if re.fullmatch(_YEAR, tok):
            last = tok[:4]
            out.append(tok)
        elif re.fullmatch(r"[a-z]", tok) and last:
            out.append(last + tok)
    return out


def extract(text: str) -> list[Mention]:
    mentions = []
    for m in CITE.finditer(text):
        a1 = m["a1"]
        if a1.lower() in STOP:
            continue
        init = m["init"] or (m["given"][0] if m["given"] else None)
        mentions.append(Mention(
            text=m.group(0).strip() + (")" if m["yp"] and not m.group(0).endswith(")") else ""),
            first=norm(a1), given=m["given"], years=_expand_years(m["yp"] or m["yb"]),
            etal=m["etal"] is not None, second=norm(m["a2"]) if m["a2"] else None,
            also=norm(m["a1b"]) if m["a1b"] else None,
            initials=re.sub(r"[\s.\-]", "", init) if init else None, span=m.span()))
    return mentions


@dataclass
class RefKey:
    id: int
    first: str
    cite_year: str
    n_authors: int
    second: str | None
    initials: str | None
    authors_norm: str
    title_norm: str = ""


@dataclass
class Resolver:
    """In-memory index of bibliography entries for one source."""
    by_key: dict[tuple[str, str], list[RefKey]] = field(default_factory=lambda: defaultdict(list))
    surnames: set[str] = field(default_factory=set)

    def add(self, r: RefKey) -> None:
        self.by_key[(r.first, r.cite_year)].append(r)
        self.surnames.add(r.first)

    def known(self, m: Mention) -> bool:
        if m.first not in self.surnames and m.given:
            compound = norm(f"{m.given} {m.first}")       # 'Venkata Rao'
            if compound in self.surnames:
                m.first, m.given = compound, None
        return m.first in self.surnames

    def resolve(self, m: Mention, year: str,
                context: set[str] = frozenset()) -> tuple[int | None, float, int]:
        """Return (reference id or None, confidence, number of candidates).
        `context`: normalized taxon names around the citing claim, used to break ties."""
        cands = self.by_key.get((m.first, year), [])
        if not cands and not year[-1].isalpha():
            # 'Smith (2001)' where the bibliography only has 2001a/2001b, or vice versa
            cands = [r for s in "abcdefgh" for r in self.by_key.get((m.first, year + s), [])]
        if not cands:
            return None, 0.0, 0
        if m.etal:
            typed = [r for r in cands if r.n_authors >= 3]
        elif m.second:
            typed = ([r for r in cands if r.second == m.second] or
                     [r for r in cands if m.second in r.authors_norm])
        else:
            typed = [r for r in cands if r.n_authors == 1]
        conf = 1.0
        if not typed:          # citation style mismatch; fall back to any same-year work
            typed, conf = cands, 0.6
        if m.also:
            typed = [r for r in typed if m.also in r.authors_norm] or typed
        if len(typed) > 1 and m.initials:
            narrowed = [r for r in typed if r.initials and r.initials.startswith(m.initials)]
            typed = narrowed or typed
        if len(typed) > 1 and len({r.title_norm[:60] for r in typed}) == 1:
            return typed[0].id, conf * 0.9, len(typed)       # duplicate bibliography entries
        if len(typed) > 1 and context:
            scored = [(sum(1 for c in context if c in r.title_norm), r) for r in typed]
            best = max(s for s, _ in scored)
            top = [r for s, r in scored if s == best]
            if best > 0 and len(top) == 1:
                return top[0].id, conf * 0.7, len(typed)
        if len(typed) == 1:
            return typed[0].id, conf, 1
        return None, 0.0, len(typed)
