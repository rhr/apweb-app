"""Extract clade age estimates from APWeb 'Age.' paragraphs.

A paragraph sits under a clade header, and 'the age of this node/clade' means the
crown of that clade. Typical prose:

    Wikström et al. (2001) suggested an age for crown Aristolochiaceae of
    (128-)122, 108(-102) Ma, Bell et al. (2010) ages of (126-)104, 91(-72) Ma, ...
    The age of this node is about 302-211 Ma (Forest et al. 2005: huge intervals), ...

An age expression is '(older-)points(-younger) Ma', where points are one estimate
('101'), several ('133, 122': e.g. two analyses), a range ('95-75') or alternatives
('143 or 88', '103/63'), optionally qualified ('ca', 'about', 'around').

Each estimate is bound to a citation: a parenthetical '(Author 2005)' or 'by/in
Author (2005)' right after it, else the nearest narrative 'Author (2005)' before it.
It is also tagged crown/stem and, when the prose names another clade ('crown-group
Pinaceae', 'for a clade [Lactoridaceae + Aristolochiaceae]'), with that subject.

Anything the parser cannot read cleanly is reported as a problem, for the errata.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from . import citations

_N = r"\d+(?:\.\d+)?"
_Q = (r"(?:(?:ca\.?|c\.|about|around|approximately|some|only|a\s+mere|perhaps|maybe|"
      r"at\s+least|at\s+most|as\s+little\s+as|as\s+much\s+as|up\s+to|over|somewhere\s+between|"
      r"between|roughly|nearly|almost|just\s+over|just\s+under|ca)\s+)*")
_SEP = r"\s*(?:-|–|—|,|/|\band\b|\bor\b|\bto\b)\s*"
_UNIT = r"(?:Ma|Mya|m\.\s?y\.\s?a\.?)"
AGE = re.compile(
    rf"(?:(?:\(\s*(?P<older>{_N})\s*[-–]\s*\)\s*)?"
    rf"(?P<q>{_Q})"
    rf"(?P<pts>{_N}(?:{_SEP}{_Q}{_N})*)"
    rf"(?:\s*\(\s*[-–]\s*(?P<younger>{_N})\s*\))?"
    rf"|(?P<q2>{_Q})(?P<pm>{_N})\s*±\s*(?P<err>{_N}))"      # '20±8.6 Ma'
    rf"\s*{_UNIT}(?![A-Za-z])")
# A number followed by a unit: anything not inside an AGE match is reported as unparsed.
BARE = re.compile(rf"{_N}\)?\s*(?:Ma|Mya|m\.\s?y\.\s?a\.?)(?![A-Za-z])")
# 'My' is used both for ages and for durations ('a phylogenetic fuse of ca 30 My').
AMBIGUOUS = re.compile(rf"{_N}\s*(?:My|my|m\.y\.|million years)(?![A-Za-z])")
APPROX = re.compile(r"\b(?:ca|c|about|around|approximately|some|roughly|nearly|almost|perhaps|maybe)\b")

_TAXON = (r"(?:\[[^\]]*(?:\][^\]]*)*?\]|[A-Z][a-z]+(?:aceae|ales|oideae|eae|inae|idae|anae|ae)\b"
          r"|[A-Z][a-z]+\s+clade\b)")
SUBJECT_BEFORE = re.compile(
    rf"\b(?P<kind>crown|stem)(?:[-\s]group)?(?:\s+age)?(?:\s+of)?(?:\s+the)?\s+(?P<name>{_TAXON})"
    rf"|\b(?:age|ages|origin|split|divergence|diversification)\s+(?:of|for|within)\s+(?:the\s+)?"
    rf"(?:(?P<kind2>crown|stem)(?:[-\s]group)?\s+)?(?:clade\s+)?(?P<name2>{_TAXON})")
SUBJECT_AFTER = re.compile(
    rf"^\s*(?:old\s+)?(?:for|of)\s+(?:a|the)?\s*(?:(?P<kind>crown|stem)(?:[-\s]group)?\s+)?"
    rf"(?:clade\s+)?(?P<name>{_TAXON})")
KIND = re.compile(r"\b(crown|stem)(?:[-\s]group|\s+age)?\b", re.I)
THIS_NODE = re.compile(r"\bthis\s+(?:node|clade|split|divergence)\b|\bthe\s+(?:node|clade)\b", re.I)
METHOD = re.compile(r"\b(BEAST|MCMCTree|MCMCtree|treePL|r8s|penalized likelihood|PL|"
                    r"PATHd8|RelTime|FBD|fossilized birth-death|BAMM|MultiDivTime|"
                    r"Bayesian|NPRS|relaxed clock|strict clock|uncorrelated)\b")

# Sentences about the fossil record give ages of fossils (minimum ages), not estimates.
FOSSIL = re.compile(r"\b(?:fossils?|macrofossils?|mesofossils?|pollen|fruits?|seeds?|leaves|"
                    r"leaf|flowers?|wood|amber|deposits?|sediments?|rocks?|Formation|"
                    r"strata|boundary)\b", re.I)
ESTIMATE = re.compile(r"\b(?:estimat\w*|crown|stem|this node|this clade|diverge\w*|"
                      r"divergence|molecular|dating|datings|clock|ages? of (?:this|the) (?:node|clade))\b",
                      re.I)

PERS_COMM = re.compile(r"\s*\(\s*(?P<who>[A-Z][^();]{1,60}?\s+pers\.\s*comm\.?)")

_ABBREV = {"al", "ca", "c", "fig", "figs", "e.g", "i.e", "cf", "c.f", "app", "tab", "vs", "sp",
           "spp", "var", "subsp", "n.b", "approx", "ed", "eds", "no", "vol", "pp", "suppl", "s.l",
           "s.str", "s.s", "incl", "excl"}


@dataclass
class Estimate:
    points: list[float]             # point estimates as written, in order
    form: str                       # point | points | range | alternatives
    older: float | None = None      # '(older-)': older end of the reported interval
    younger: float | None = None    # '(-younger)'
    approx: bool = False
    kind: str = "crown"             # crown | stem
    basis: str = "estimate"         # estimate (divergence time) | fossil (age of a fossil)
    kind_explicit: bool = False     # crown/stem stated, rather than 'this node' or default
    subject: str | None = None      # another clade this age is for, as written
    cite: citations.Mention | None = None
    pers_comm: str | None = None    # 'Tank & Olmstead pers. comm.': unpublished, no reference
    methods: list[str] = field(default_factory=list)
    text: str = ""                  # the age expression as written
    span: tuple[int, int] = (0, 0)
    sentence: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        """One representative age: the midpoint of a range, else the median point."""
        if self.form == "range":
            return round((max(self.points) + min(self.points)) / 2, 2)
        return round(statistics.median(self.points), 2)

    @property
    def low(self) -> float:
        return min([self.younger] if self.younger is not None else self.points)

    @property
    def high(self) -> float:
        return max([self.older] if self.older is not None else self.points)


@dataclass
class Problem:
    kind: str           # see PROBLEMS
    text: str           # the offending snippet
    span: tuple[int, int] = (0, 0)


PROBLEMS = {
    "no_citation": "age with no citation that can be attached to it",
    "interval_order": "interval does not contain its point estimates",
    "unparsed": "a number with 'Ma' the parser could not read",
    "ambiguous_unit": "'My' or 'million years': an age or a duration?",
    "implausible": "age older than 500 Ma",
    "unresolved_subject": "the age names a clade the site has no header for",
}


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans; periods after abbreviations and initials don't end a sentence."""
    out, start, depth = [], 0, 0
    for m in re.finditer(r"[()\[\]]|\.(?=\s+[A-Z\"(\[])|;\s", text):
        ch = m.group(0)
        if ch in "([":
            depth += 1
            continue
        if ch in ")]":
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        if ch == ".":
            word = re.search(r"([\w.]*)$", text[start:m.start()])[1].lower()
            if word in _ABBREV or re.fullmatch(r"[a-z]|[a-z]\.[-a-z.]*", word):
                continue
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def _subject(sent: str, m: re.Match, group: str) -> str:
    """The subject named by a match, with a bracket extended to its balancing ']'."""
    start = m.start(group)
    if sent[start] != "[":
        return m[group]
    depth = 0
    for i in range(start, len(sent)):
        depth += {"[": 1, "]": -1}.get(sent[i], 0)
        if depth == 0:
            return sent[start:i + 1]
    return m[group]


def _form(pts_text: str, n: int, q: str = "") -> str:
    if n == 1:
        return "point"
    between = re.search(r"\bbetween\b", q or "")
    if re.search(r"\bor\b|/", pts_text) or (re.search(r"\band\b", pts_text) and not between):
        return "alternatives"                       # '175 and 119 Ma' for two analyses
    if n == 2 and re.search(r"[-–—]|\bto\b|\band\b", pts_text) and "," not in pts_text:
        return "range"                              # '95-75 Ma', 'between 52 and 41 Ma'
    return "points"


def _bind(text: str, s: int, e: int, ms: list[citations.Mention]) -> citations.Mention | None:
    """The citation an age at text[s:e] belongs to. In order of preference:
    1. one tight after it: '... Ma (Author 2005)', '... Ma in/by Author (2005)', '... Ma Author (2005)'
    2. a narrative one shortly before it, with no other age or comma between:
       'Author (2005) give an age of ... Ma' (not '..., while the youngest, at ... Ma, are those of')
    3. a looser one after it: '... Ma was suggested by Author (2005)', '... Ma (see Author 2005)'
    4. the nearest one before it, unless a parenthetical citation closed the previous estimate."""
    nxt = next((m for m in ms if m.span[0] >= e), None)
    ngap = text[e:nxt.span[0]] if nxt is not None else None
    if ngap is not None and re.fullmatch(r"\s*(?:\(\s*|(?:in|by)\s+)?", ngap):
        return nxt
    prev = [m for m in ms if m.span[1] <= s]
    p = prev[-1] if prev else None
    pgap = text[p.span[1]:s] if p is not None else None
    narrative_gap = pgap[1:] if pgap and pgap[0] == ")" else pgap   # spans stop before ')'
    if p is not None and p.text.rstrip().endswith(")") and len(pgap) <= 80 \
            and not re.search(r"[;,]|\bMa\b|\)", narrative_gap):
        return p
    if ngap is not None and not re.search(r"[;]|\bMa\b", ngap):
        narrative = nxt.text.rstrip().endswith(")")
        # '... Ma for a clade [A + B] was suggested by Author (2015)' can run long
        # ... but '... Ma (relaxed clock) and in Author (2023)' starts the next study
        if (len(ngap) <= 120 and re.search(r"\b(?:by|in)\s*$", ngap)
                and not re.search(r"\b(?:and|or|while|but|whereas)\s+(?:by|in)\s*$", ngap)) \
                or len(ngap) <= 60 and (
                re.search(r"\(\s*(?:e\.g\.,?\s*|see\s+(?:also\s+)?|cf\.\s*|c\.f\.\s*)?$", ngap)
                or re.search(r"\b(?:of|from|after|see)\s*$", ngap)
                or (not narrative and "(" in ngap and ")" not in ngap)):
            return nxt
    if p is not None:
        # a parenthetical citation closes the previous estimate; it doesn't open this one
        closed = not p.text.rstrip().endswith(")") and re.match(r"[^()]*\)", pgap)
        if not closed and len(pgap) <= 200:
            return p
    return None


def parse(text: str) -> tuple[list[Estimate], list[Problem]]:
    """Estimates in one Age paragraph, and problems not tied to any one estimate."""
    ms = sorted(citations.extract(text), key=lambda m: m.span[0])
    cite_spans = [m.span for m in ms]
    estimates: list[Estimate] = []
    covered: list[tuple[int, int]] = []
    for ss, se in sentences(text):
        sent = text[ss:se]
        basis = "fossil" if FOSSIL.search(sent) and not ESTIMATE.search(sent) else "estimate"
        kind, kind_explicit, subject = "crown", False, None
        cue_end = 0
        for m in AGE.finditer(sent):
            s, e = ss + m.start(), ss + m.end()
            if any(a < e and s < b for a, b in cite_spans):
                continue
            covered.append((s, e))
            if m["pm"]:
                v, err = float(m["pm"]), float(m["err"])
                pts, pts_text, q = [v], m["pm"], m["q2"]
                older, younger = round(v + err, 3), round(max(0.0, v - err), 3)
            else:
                pts, pts_text, q = [float(x) for x in re.findall(_N, m["pts"])], m["pts"], m["q"]
                older = float(m["older"]) if m["older"] else None
                younger = float(m["younger"]) if m["younger"] else None
            # Context cues between the previous age and this one carry forward in the
            # sentence: 'crown-group Pinaceae ... A (x), B (y)'.
            before = sent[cue_end:m.start()]
            for c in SUBJECT_BEFORE.finditer(before):
                k = c["kind"] or c["kind2"]
                subject = _subject(before, c, "name" if c["name"] else "name2")
                if k:
                    kind, kind_explicit = k.lower(), True
            if THIS_NODE.search(before):
                subject = None
            km = list(KIND.finditer(before))
            if km:
                kind, kind_explicit = km[-1].group(1).lower(), True
            cue_end = m.end()
            est_kind, est_explicit, est_subject = kind, kind_explicit, subject
            after = SUBJECT_AFTER.match(sent[m.end():])
            if after:                                   # '... Ma for a clade [A + B]'
                est_subject = _subject(sent[m.end():], after, "name")
                if after["kind"]:
                    est_kind, est_explicit = after["kind"].lower(), True
            est = Estimate(
                points=pts, form=_form(pts_text, len(pts), q), older=older, younger=younger,
                approx=bool(APPROX.search(q or "")),
                kind=est_kind, kind_explicit=est_explicit, basis=basis,
                subject=est_subject.strip() if est_subject else None,
                cite=_bind(text, s, e, ms),
                methods=sorted(set(METHOD.findall(sent))),
                text=m.group(0).strip(), span=(s, e), sentence=sent.strip())
            pc = PERS_COMM.match(text, e)
            if pc and est.cite is None:
                est.pers_comm = pc["who"]
            _check(est)
            estimates.append(est)
    problems = []
    for m in BARE.finditer(text):
        if not any(a <= m.start() and m.end() <= b for a, b in covered) and \
                not any(a <= m.start() < b for a, b in cite_spans):
            problems.append(Problem("unparsed", _around(text, *m.span()), m.span()))
    for m in AMBIGUOUS.finditer(text):
        problems.append(Problem("ambiguous_unit", _around(text, *m.span()), m.span()))
    return estimates, problems


def _check(est: Estimate) -> None:
    if est.cite is None and est.pers_comm is None and est.basis == "estimate":
        est.problems.append("no_citation")
    if (est.older is not None and est.older < max(est.points)) or \
            (est.younger is not None and est.younger > min(est.points)):
        est.problems.append("interval_order")
    if max(est.points + [est.older or 0]) > 500:
        est.problems.append("implausible")


def _around(text: str, s: int, e: int, pad: int = 60) -> str:
    a, b = max(0, s - pad), min(len(text), e + pad)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- per-clade collection

@dataclass
class Record:
    node: str                       # key of the clade whose Age paragraph holds the estimate
    target: str                     # key of the clade it dates: node, or the subject named
    header: object                  # pages.Header the paragraph sits under
    est: Estimate
    subject_resolved: bool | None   # None when no other clade is named


@dataclass
class Issue:
    page: str
    anchor: str | None
    label: str
    kind: str                       # a PROBLEMS key
    text: str


def collect(asm) -> tuple[list[Record], list[Issue]]:
    """Estimates from every clade's Age paragraphs (each distinct paragraph once per
    clade; the spine repeats them across pages), with the problems found."""
    records, issues = [], []
    for key, node in asm.nodes.items():
        seen: set[str] = set()
        for h in node.headers:
            for t in h.age_texts:
                if t in seen:
                    continue
                seen.add(t)
                ests, probs = parse(t)
                issues += [Issue(h.page, h.anchor, h.label, p.kind, p.text) for p in probs]
                for e in ests:
                    target, resolved = key, None
                    if e.subject:
                        k = asm.lookup(e.subject)
                        if k is None:
                            resolved = False
                            e.problems.append("unresolved_subject")
                        elif k != key:
                            target, resolved = k, True
                    issues += [Issue(h.page, h.anchor, h.label, p, _around(t, *e.span))
                               for p in e.problems]
                    records.append(Record(key, target, h, e, resolved))
    return records, issues
