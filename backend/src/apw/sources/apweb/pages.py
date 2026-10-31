"""Pass 1: parse one APWeb order page into a sequence of headers and their content.

A page is a flat run of <p> elements:
  * headers  - taxon/clade lines, either anchored (<a name=X anc=Parent>) or
               unanchored 'spine' lines such as '[CERATOPHYLLALES + EUDICOTS]: chars...'
  * characterization paragraphs following a header ('; '-separated characters,
               <b class="apo"> marking possible apomorphies)
  * topic paragraphs (style text-indent, bold/underlined lead: 'Age.', 'Evolution:' ...)
  * stats ('17/975: ...', 'Just the 1 family, 17 genera, 1000 species.')
  * synonymy ('Synonymy: <b>Leeales</b> de Candolle - ...')
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, NavigableString, Tag

from ...db import norm
from . import citations

RANK_SUFFIXES = [("aceae", "family"), ("oideae", "subfamily"), ("ales", "order"),
                 ("anae", "superorder"), ("idae", "subclass"), ("inae", "subtribe"),
                 ("odae", "supertribe"), ("eae", "tribe")]   # "-ineae" is ambiguous (Selineae)
ENUM = re.compile(r"^\s*(?:(?:\d+|[IVX]+)[A-Z]?\d*\.\s+)?\*?")   # '6C3. *Cactinae': '*' flags changes
HEADING_STOPWORDS = {"AND", "OF", "THE", "FOR", "IN", "ESPECIALLY", "TO", "WITH"}
HEAD_STOP = re.compile(r"\s(?:-|–|—)\s|——|—|\s-\s*Back to|\bBack to\b|\bMain Tree\b|\bSee Main Tree\b")


# ---------------------------------------------------------------- bracket trees

@dataclass
class Group:
    """A bracketed clade expression, e.g. [Cornaceae [Nyssaceae + Curtisiaceae]]."""
    members: list["Group | str"]

    def atoms(self) -> set[str]:
        out: set[str] = set()
        for m in self.members:
            out |= m.atoms() if isinstance(m, Group) else {m}
        return out

    def groups(self):
        yield self
        for m in self.members:
            if isinstance(m, Group):
                yield from m.groups()

    def render(self) -> str:
        parts = [m.render() if isinstance(m, Group) else display_name(m) for m in self.members]
        sep = " + " if len(parts) == 2 else ", "
        return "[" + sep.join(parts) + "]"


_SUPRAGENERIC = r"[A-Z][a-z]+(?:oideae|aceae|odae|idae|inae|ales|eae)"
RUN_ON = re.compile(rf"{_SUPRAGENERIC}(?: {_SUPRAGENERIC})+")


def parse_brackets(s: str) -> Group | None:
    """Parse '[A [B + C]]', '[A, B, [C + D]' (unbalanced tolerated) into a Group tree."""
    s = re.sub(r"\([^()\[\]]*\)?", "", s)      # '(if this clade exists)' etc.
    tokens = re.findall(r"\[|\]|\+|,|[^\[\]+,]+", s)
    stack: list[list] = [[]]
    for tok in tokens:
        if tok == "[":
            stack.append([])
        elif tok == "]":
            if len(stack) > 1:
                members = stack.pop()
                if len(members) == 1:            # '[[A + B]]' or '[A]': unwrap
                    stack[-1].append(members[0])
                elif members:
                    stack[-1].append(Group(members))
        elif tok in "+,":
            continue
        else:
            name = clean_atom(tok)
            if name and RUN_ON.fullmatch(name):   # 'Ixorodae Airospermeae': a lost comma
                stack[-1].extend(name.split())
            elif name:
                stack[-1].append(name)
    while len(stack) > 1:                      # close unbalanced brackets
        members = stack.pop()
        stack[-1].append(Group(members))
    top = stack[0]
    if len(top) == 1 and isinstance(top[0], Group):
        g = top[0]
    elif top:
        g = Group(top)
    else:
        return None
    # collapse single-member groups: [[A + B]] -> [A + B]
    while len(g.members) == 1 and isinstance(g.members[0], Group):
        g = g.members[0]
    return g if len(g.atoms()) >= 2 else None


def clean_atom(tok: str) -> str:
    tok = tok.split("/")[0]          # '[lamiidae/asterid i + ...]': first alternative
    tok = re.sub(r"\bet al\.?", "", tok, flags=re.I)
    tok = tok.replace("×", "").replace("†", "").replace("?", "")
    tok = re.sub(r"\s+", " ", tok).strip(" .:;")
    return tok


def display_name(s: str) -> str:
    """'VITACEAE' -> 'Vitaceae'; informal 'CORE EUDICOTS' -> 'core eudicots'."""
    s = s.strip()
    if s != s.upper():
        return s
    words = s.split()
    if len(words) == 1 and not s.endswith(("IDS", "OTS")):
        return s.capitalize()
    return s.lower()


def infer_rank(name: str, informal: bool, enumerated: bool, upper: bool = False) -> str:
    if informal:
        return "clade"
    n = name.lower()
    if enumerated and not upper and n.endswith(("aceae", "ineae")) and " " not in n:
        # APWeb capitalizes families and doesn't number suborders: '3A. Bombaceae'
        # (Bombax) and '1C. Melodineae' (Melodinus) are tribes
        return "tribe"
    if " " not in n:
        for suf, rank in RANK_SUFFIXES:
            if n.endswith(suf):
                return rank
        if enumerated:
            return "genus"
    return "clade"


# ---------------------------------------------------------------- page model

@dataclass
class Character:
    text: str
    apomorphy: bool
    apo: list[str]
    uncertain: bool


@dataclass
class Header:
    page: str
    seq: int
    anchor: str | None
    anc: str | None
    names: list[str]                 # named alternatives, display form, e.g. ['Rosids', 'Rosidae']
    groups: list[Group]              # bracket alternatives
    authorship: str | None
    rank: str
    informal: bool
    dagger: bool
    characters: list[Character] = field(default_factory=list)
    topics: dict[str, list[citations.Mention]] = field(default_factory=dict)
    synonyms: list[tuple[str, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    distribution: str | None = None
    text: str = ""                   # the header line as written, up to the label colon

    @property
    def label(self) -> str:
        return self.names[0] if self.names else self.groups[0].render()

    @property
    def atoms(self) -> set[str]:
        out: set[str] = set()
        for g in self.groups:
            out |= g.atoms()
        return out


def text_of(el: Tag) -> str:
    return re.sub(r"\s+", " ", el.get_text("")).replace("\xa0", " ").strip()


def segments(el: Tag) -> list[tuple[str, bool]]:
    """Flatten an element to (text, is_apomorphy) runs, skipping comments/scripts."""
    out = []
    for s in el.descendants:
        if type(s) is not NavigableString or s.parent.name in ("script", "style"):
            continue
        apo = any(p.name == "b" and "apo" in (p.get("class") or []) for p in s.parents)
        out.append((str(s).replace("\xa0", " "), apo))
    return out


def split_characters(segs: list[tuple[str, bool]]) -> list[Character]:
    text = "".join(t for t, _ in segs)
    flags = [a for t, a in segs for _ in t]
    units, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            units.append((start, i))
            start = i + 1
    units.append((start, len(text)))
    out = []
    for a, b in units:
        raw = text[a:b]
        unit = re.sub(r"\s+", " ", raw).strip().rstrip(".").strip()
        if not re.search(r"[A-Za-z]", unit) or len(unit) > 600:
            continue
        apo_runs, cur = [], ""
        for ch, f in zip(raw, flags[a:b]):
            if f:
                cur += ch
            elif cur:
                apo_runs.append(cur)
                cur = ""
        if cur:
            apo_runs.append(cur)
        apo_runs = [re.sub(r"\s+", " ", r).strip(" ,;.") for r in apo_runs]
        apo_runs = [r for r in apo_runs if re.search(r"[A-Za-z0-9]", r)]
        uncertain = unit.startswith("(") and unit.endswith(")") and _balanced(unit[1:-1])
        out.append(Character(unit, bool(apo_runs), apo_runs, uncertain))
    return out


def _balanced(s: str) -> bool:
    d = 0
    for ch in s:
        d += ch == "("
        d -= ch == ")"
        if d < 0:
            return False
    return d == 0


def _depth0_index(s: str, ch: str) -> int:
    d = 0
    for i, c in enumerate(s):
        if c in "([":
            d += 1
        elif c in ")]":
            d -= 1
        elif c == ch and d <= 0:
            return i
    return -1


def _label_colon(s: str) -> int:
    """Index of the colon ending a header label, ignoring '—— Synonymy: ...' tails."""
    cut = min([i for i in (s.find("——"), s.lower().find("synonymy")) if i >= 0], default=len(s))
    i = _depth0_index(s[:cut], ":")
    if i < 0 and s[:cut].count("[") > s[:cut].count("]"):   # unbalanced brackets
        i = s[:cut].find(":")
    return i


def _split_alternatives(head: str) -> list[str]:
    parts, d, cur = [], 0, ""
    for c in head:
        if c == "[":
            d += 1
        elif c == "]":
            d -= 1
        if c == "/" and d <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _is_upper(tok: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", tok)
    return len(letters) >= 2 and letters.isupper() or tok in ("ET", "AL.")


def _is_authority_start(tok: str) -> bool:
    return bool(re.match(r"^(?:[A-Z]\.|&|\(|de$|ex$|van$|von$|d'|L\.?$)", tok))


def has_rank_suffix(word: str) -> bool:
    w = word.lower().strip(".,†")
    return any(w.endswith(suf) for suf, _ in RANK_SUFFIXES)


def parse_alt(alt: str) -> tuple[str, str | None, bool] | None:
    """Split one '/'-separated label alternative into (name, authority, dagger)."""
    alt = re.sub(r"\s*\[[^\]]*\]", "", alt).replace('"', "").strip(" ,.:")
    dagger = "†" in alt
    toks = alt.replace("†", " ").split()
    if not toks or not re.match(r"[A-Za-z×]", toks[0]):
        return None
    vernacular = len(toks) > 1 and not any(_is_authority_start(t) for t in toks[1:]) and (
        all(re.fullmatch(r"[a-z][a-z-]*", t) and t not in ("et", "al") for t in toks[1:])
        or (len(toks) <= 4 and toks[-1].lower() in ("clade", "group", "grade", "alliance")))
    if vernacular and not _is_upper(toks[0]):
        # 'Spermacoceae alliance', 'Old World Clade': no authority to split off
        i = len(toks)
    elif _is_upper(toks[0]):
        i = 1
        while i < len(toks) and (_is_upper(toks[i]) or re.fullmatch(r"[IVX]+", toks[i])):
            i += 1
        while i < len(toks) and re.fullmatch(r"(?:clade|group|grade|s\.\s?l\.|s\.\s?str\.)", toks[i]):
            i += 1
    elif len(toks) == 1 or has_rank_suffix(toks[0]) or _is_authority_start(toks[1]) or \
            (toks[0][0].isupper() and toks[1][0].isupper() and
             any(t == "&" or re.fullmatch(r"[A-Z]\.", t) for t in toks[2:])) or \
            (toks[0][0].isupper() and toks[1][0].isupper() and not has_rank_suffix(toks[1])
             and len(toks) <= 3 and toks[0].lower() not in ("core", "true", "the")):
        i = 1
    else:   # informal multi-word name: 'core Lamiales', 'The Carnivorous Clade'
        i = min(len(toks), 6)
    if re.match(r"(?i)et\s+al", " ".join(toks[i:])):
        i += 2                    # 'Boryaceae et al.' names a clade, not the family
    name = re.sub(r"\s+", " ", " ".join(toks[:i]).replace("×", "").replace("?", "")).strip(" .:;")
    rest = " ".join(toks[i:]).strip(" ,.:")
    if not name or len(name) > 60:
        return None
    auth = rest if rest and len(rest) < 80 and not rest.lower().startswith(("of ", "if ")) else None
    return name, auth, dagger


def parse_head(head: str) -> tuple[list[str], list[Group], str | None, bool, bool]:
    """Split a header label into (names, groups, authorship, enumerated, dagger)."""
    # Arabic numbering marks subgroups ('1. Schismocarpus'); Roman numbering marks
    # backbone clades ('II. TRACHEOPHYTA'), which aren't genera.
    enumerated = bool(ENUM.match(head)[0].strip(" *")) and head.lstrip()[:1].isdigit()
    head = ENUM.sub("", head)
    names, groups, auth, dagger = [], [], None, False
    for alt in _split_alternatives(head):
        if alt.startswith("["):
            g = parse_brackets(alt)
            if g:
                groups.append(g)
            continue
        parsed = parse_alt(alt)
        if not parsed:
            continue
        name, a, d = parsed
        names.append(display_name(name))
        dagger = dagger or d
        if auth is None and a:
            auth = a
    return names, groups, auth, enumerated, dagger


# ---------------------------------------------------------------- paragraph classes

def _is_indented(p: Tag) -> bool:
    return "text-indent" in (p.get("style") or "").replace(" ", "").lower()


def _lead(p: Tag) -> tuple[str | None, str | None]:
    """Return (bold lead, underlined lead) at the very start of a topic paragraph."""
    main = sub = None
    for child in p.children:
        if isinstance(child, NavigableString):
            if child.strip():
                break
            continue
        if child.name == "a" and not text_of(child):
            continue
        el = child
        while el.name in ("a", "font", "span") and len(el.find_all(recursive=False)) == 1 \
                and not "".join(s for s in el.strings if s.parent is el).strip():
            el = el.find(recursive=False)
        t = text_of(el)
        if el.name == "b" and main is None and sub is None:
            main = t
            continue
        if el.name == "u" and sub is None:
            sub = t
        break
    clean = lambda s: re.sub(r"[.:\s]+$", "", s).strip() if s else None
    return clean(main), clean(sub)


STATS_FAMILY = re.compile(r"^(?P<g>\d[\d,]*)\s*(?:\[[^\]]*\])?\s*/\s*(?P<s>ca\.?\s*)?(?P<n>\d[\d,]*)")
STATS_ORDER = re.compile(r"^(?:Just\s+the\s+)?\d[\d,]*\s+famil|^(?:Just\s+the\s+)?\d+\s+(?:genus|genera)\b",
                         re.I)


def _count(text: str, word: str) -> int | None:
    m = re.search(rf"(\d[\d,]*)\s+(?:{word})", text)
    return int(m[1].replace(",", "")) if m else None


def _distribution(text: str) -> str | None:
    t = STATS_FAMILY.sub("", text)
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"^[\s.:]*(?:-\s*[^.]*?below\.)?", "", t)
    t = re.sub(r"^\s*(?:[A-Z][a-z]+(?:\s+[a-z.]+)?\s*\(\d[\d,]*\)[,.]?\s*(?:and\s+)?)+", "", t)
    t = re.split(r"\b(?:Maps?|Photos?|Images?|Illustrations?)\s*:", t)[0]
    t = re.sub(r"\s+", " ", t).strip(" .:-")
    if not t or len(t) < 4:
        return None
    return t[:300]


def _synonyms(text: str) -> list[tuple[str, str]]:
    body = text.split(":", 1)[1] if ":" in text else text
    out = []
    for item in re.split(r"\s+-\s+|\s*—\s*", body):
        item = item.strip(" .;")
        m = re.match(r"^([A-Z][a-zë]+)\s*(.*)$", item)
        if m and len(item) < 120:
            out.append((m[1], m[2].strip(" ,")))
    return out


def _is_spine_line(p: Tag, text: str) -> bool:
    head = text.split(":", 1)[0] if ":" in text else text
    if len(head) > 400 or len(text) < 3:
        return False
    if head.startswith("[") and "]" in head:
        return True
    first = next((c for c in p.children if isinstance(c, Tag) or str(c).strip()), None)
    letters = re.sub(r"[^A-Za-z]", "", ENUM.sub("", head).split("/")[0])
    if len(letters) < 4 or not letters.isupper():
        return False
    if ":" in text:
        return isinstance(first, Tag) and first.name in ("b", "font")
    # a bare label paragraph, e.g. '<p><font>TRACHEOPHYTA&dagger;</font></p>'
    words = set(re.findall(r"[A-Z]+", text))
    return len(text) < 80 and not words & HEADING_STOPWORDS and p.find_parent("center") is None


def _anchor_header(p: Tag, text: str) -> Tag | None:
    a = p.find("a", attrs={"name": True})
    if a is None:
        return None
    before = ""
    for s in p.descendants:
        if s is a:
            break
        if isinstance(s, NavigableString):
            before += str(s)
    if before.strip(" \xa0"):
        return None
    label = ENUM.sub("", text_of(a) or text).lstrip('"')
    if label.startswith("["):
        return a
    if not (p.find("b") or p.find("u")) or p.find_parent("center") is not None:
        return None
    words = label.split()
    if not words or not re.match(r"[A-Za-z×]", words[0]):
        return None
    if _is_upper(words[0]):
        return None if set(re.findall(r"[A-Z]+", label)) & HEADING_STOPWORDS else a
    if has_rank_suffix(words[0]) or a.get("anc") is not None:
        return a
    if ENUM.match(text)[0].strip(" *"):
        # enumerated genus-level entries carry an authority: '1. Schismocarpus S. F. Blake'
        rest = text[len(ENUM.match(text)[0]):].split(None, 1)
        if len(rest) == 2 and _is_authority_start(rest[1].split()[0]):
            return a
    return None


def parse_page(page: str, html: str, families: set[str] | None = None) -> list[Header]:
    """`families`: normalized names of all family headers on the site, if known; used to
    tell clades of families from clades of tribes ('Saraceae' is a tribe, after Saraca)."""
    soup = BeautifulSoup(html, "lxml")
    headers: list[Header] = []
    cur: Header | None = None
    family: Header | None = None   # enclosing family, for discussion placed after its subgroups
    in_chars = False
    first_para = False
    main_topic = None

    for p in soup.find_all("p"):
        text = text_of(p)
        if not text:
            continue
        if p.find_parent("center") is not None and text.isupper():
            # a centered essay heading ('EVOLUTION AND DIVERSIFICATION OF THE SEED PLANTS')
            # ends the current taxon; the essay is about the angiosperms as a whole
            cur = next((h for h in headers if "Angiospermae" in h.names), None)
            family, in_chars, main_topic = None, False, None
            continue
        indented = _is_indented(p)
        a = None if indented else _anchor_header(p, text)
        if a is not None or (not indented and _is_spine_line(p, text)):
            h = _make_header(page, len(headers), p, a, text)
            if h:
                headers.append(h)
                cur, in_chars, first_para, main_topic = h, True, not h.characters, None
                # A family's own discussion follows its subgroups, so keep the family
                # context through subgroups, including named clades within it ('The BOP
                # Clade'). Only clades of families or higher (or backbone lines) end it.
                if h.rank in ("family", "order") or (h.atoms and _above_family(h, families)) \
                        or (h.rank == "clade" and not h.atoms and family is None):
                    family = h if h.rank == "family" else None
                continue
        if cur is None:
            continue
        if indented:
            in_chars = False
            main, sub = _lead(p)
            if main:
                main_topic = main
            topic = "/".join(t for t in (main_topic, sub) if t) or "General"
            if topic.startswith("Note"):
                continue
            mentions = citations.extract(text)
            if mentions:
                # APWeb discusses a family's evolution/phylogeny after listing its
                # subfamilies/tribes; only 'Age' stays with the subgroup.
                owner = family if (family is not None and cur is not family
                                   and not main_topic in (None, "Age")) else cur
                owner.topics.setdefault(topic, []).extend(mentions)
            continue
        if STATS_FAMILY.match(text):
            m = STATS_FAMILY.match(text)
            cur.counts.setdefault("genus_count", int(m["g"].replace(",", "")))
            cur.counts.setdefault("species_count", int(m["n"].replace(",", "")))
            cur.distribution = cur.distribution or _distribution(text)
            in_chars = False
            continue
        if STATS_ORDER.match(text):
            for key, word in (("family_count", "famil"), ("genus_count", "genus|genera"),
                              ("species_count", "species")):
                n = _count(text, word)
                if n is not None:
                    cur.counts.setdefault(key, n)
            continue
        if text.lower().startswith("synonymy"):
            cur.synonyms.extend(_synonyms(text))
            continue
        if in_chars and len(text) > 15 and "Back to" not in text[:20] and (
                first_para or ";" in text or p.find("b", class_="apo")
                or "MsoNormal" in (p.get("class") or [])):
            cur.characters.extend(split_characters(segments(p)))
        first_para = False
    return headers


def _above_family(h: Header, families: set[str] | None = None) -> bool:
    """Informal clade whose members are families or higher (e.g. [Cornaceae [Nyssaceae ...]])."""
    def high(a: str) -> bool:
        rank = infer_rank(a, False, False)
        if rank == "family" and families is not None:
            return norm(a) in families
        return rank in ("family", "order", "superorder")
    return any(high(a) for a in h.atoms)


def _make_header(page: str, seq: int, p: Tag, a: Tag | None, text: str) -> Header | None:
    colon = _label_colon(text)
    head = text[:colon] if colon >= 0 else text
    syn = re.search(r"synonymy", text, re.I)
    syn_text = text[syn.start():] if colon < 0 and syn else None
    stop = HEAD_STOP.search(head)
    if stop:
        head = head[:stop.start()]
    names, groups, auth, enumerated, dagger = parse_head(head.strip())
    if not names and not groups:
        return None
    informal = not names
    upper = bool(re.search(r"\b[A-Z]{4,}ACEAE\b", head))
    rank = infer_rank(names[0] if names else "", informal, enumerated, upper)
    h = Header(page=page, seq=seq, anchor=a.get("name") if a else None,
               anc=a.get("anc") if a else None, names=names, groups=groups,
               authorship=auth, rank=rank, informal=informal, dagger=dagger,
               text=(text[:colon] if colon >= 0 else text)[:600].strip())
    if syn_text:
        h.synonyms.extend(_synonyms(syn_text))
    if colon >= 0:
        segs = segments(p)
        colon = _label_colon("".join(t for t, _ in segs))
        # drop the label part: consume characters up to and including the colon
        consumed, out = 0, []
        for t, f in segs:
            if consumed + len(t) <= colon:
                consumed += len(t)
                continue
            cut = max(0, colon + 1 - consumed)
            out.append((t[cut:], f))
            consumed += len(t)
        h.characters = split_characters(out)
    return h
