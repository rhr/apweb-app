"""APWeb parsing, on synthetic HTML that mimics the site's markup (no APWeb text)."""

from apw.sources.apweb import citations, pages, references
from apw.sources.apweb.ingest import Assembler

# --------------------------------------------------------------------- references


def test_reference_three_authors_with_cite_year():
    r = references.parse_entry(
        "Smith, A. B. [et al. 2021], Jones, C., & Brown, D. 2022 [= 2021]. "
        "A study of things. Some J. Bot. 12: 34-56.")
    assert (r.first_author, r.initials, r.n_authors) == ("Smith", "AB", 3)
    assert (r.year, r.cite_year) == ("2022", "2021")
    assert (r.title, r.container, r.volume, r.pages) == ("A study of things.", "Some J. Bot.", "12", "34-56")


def test_reference_two_authors_and_suffix():
    r = references.parse_entry("Doe, J., & Roe, R. 1999b. Another title. J. Things 3: 1-2.")
    assert (r.n_authors, r.second_norm, r.cite_year) == (2, "roe", "1999b")


def test_reference_rejects_non_entries():
    assert references.parse_entry("Back to Home") is None


# --------------------------------------------------------------------- citations


def test_extract_citation_forms():
    text = ("as noted by Hengcheng Wang et al. (2009), (Magallón & Castillo 2009; Endress 2010c) "
            "and Smith (2001, 2003b); see Jaillon, Aury et al. (2007: fig. 2). In 2009 nothing.")
    ms = {m.text: m for m in citations.extract(text)}
    assert ms["Hengcheng Wang et al. (2009)"].initials == "H"
    assert ms["Magallón & Castillo 2009"].second == "castillo"
    assert ms["Endress 2010c"].years == ["2010c"]
    assert ms["Smith (2001, 2003b)"].years == ["2001", "2003b"]
    assert ms["Jaillon, Aury et al. (2007)"].also == "aury"
    assert not any(m.first == "in" for m in ms.values())


def _resolver(*refs):
    res = citations.Resolver()
    for r in refs:
        res.add(r)
    return res


def test_resolver_uses_author_count_and_initials():
    res = _resolver(
        citations.RefKey(1, "wang", "2009", 3, None, "H", "wang h"),
        citations.RefKey(2, "wang", "2009", 3, None, "X", "wang x"),
        citations.RefKey(3, "wang", "2009", 1, None, "H", "wang h"))
    m = citations.extract("Hengcheng Wang et al. (2009)")[0]
    assert res.resolve(m, "2009")[0] == 1
    single = citations.extract("Wang (2009)")[0]
    assert res.resolve(single, "2009")[0] == 3


def test_resolver_breaks_ties_with_taxon_context():
    res = _resolver(
        citations.RefKey(1, "xi", "2014", 3, None, "H", "xi", " phylogeny of swertiinae gentianaceae "),
        citations.RefKey(2, "xi", "2014", 3, None, "Z", "xi", " placement of amborella "))
    m = citations.extract("Xi et al. (2014)")[0]
    assert res.resolve(m, "2014")[0] is None
    ref, conf, n = res.resolve(m, "2014", {" gentianaceae "})
    assert (ref, n) == (1, 2) and conf < 1


# --------------------------------------------------------------------- labels


def test_parse_brackets_nested_and_unbalanced():
    g = pages.parse_brackets("[CORNACEAE [NYSSACEAE [GRUBBIACEAE + CURTISIACEAE]]]")
    assert g.render() == "[Cornaceae + [Nyssaceae + [Grubbiaceae + Curtisiaceae]]]"
    poly = pages.parse_brackets("[SANTALALES, VITALES, [BERBERIDOPSIDALES + ASTERIDAE]")
    assert poly.atoms() == {"SANTALALES", "VITALES", "BERBERIDOPSIDALES", "ASTERIDAE"}
    assert pages.parse_brackets("[[A + B] + (if this clade exists)]").render() == "[A + B]"
    # a lost comma between two taxa; vernacular names stay whole
    assert pages.parse_brackets("[Jackieae, Ixorodae Airospermeae, Coffeodae]").atoms() == \
        {"Jackieae", "Ixorodae", "Airospermeae", "Coffeodae"}
    assert pages.parse_brackets("[Woody Myrsinoideae + Core Brassicales]").atoms() == \
        {"Woody Myrsinoideae", "Core Brassicales"}


def test_parse_alt_names_and_authorities():
    assert pages.parse_alt("ROSANAE Takhtajan") == ("ROSANAE", "Takhtajan", False)
    assert pages.parse_alt("Core Lamiales")[0] == "Core Lamiales"
    assert pages.parse_alt("Cayratieae J. Wen & L. M. Lu")[:2] == ("Cayratieae", "J. Wen & L. M. Lu")
    assert pages.parse_alt("Rubiodae Robbrecht & Manen")[0] == "Rubiodae"
    assert pages.parse_alt("Boryaceae et al.")[0] == "Boryaceae et al"
    assert pages.parse_alt("TRACHEOPHYTA†") == ("TRACHEOPHYTA", None, True)
    # vernacular names have no authority to split off
    assert pages.parse_alt("Fooeae alliance") == ("Fooeae alliance", None, False)
    assert pages.parse_alt("Old World Clade") == ("Old World Clade", None, False)


def test_parse_head_ranks():
    names, _, auth, enum, _ = pages.parse_head("VII. ANGIOSPERMAE Lindley / MAGNOLIOPHYTA")
    assert names == ["Angiospermae", "Magnoliophyta"] and not enum
    for head, rank in [("3A. Bombaceae", "tribe"), ("1C. Melodineae", "tribe"),
                       ("2. Vitoideae Eaton", "subfamily"), ("1. Schismocarpus S. F. Blake", "genus")]:
        n, _, _, e, _ = pages.parse_head(head)
        assert pages.infer_rank(n[0], False, e) == rank, head
    assert pages.infer_rank("Aextoxicaceae", False, True, upper=True) == "family"


def test_split_characters_flags_apomorphies():
    cs = pages.split_characters([("stomata anomocytic; ", False), ("pollen tricolpate", True),
                                 (" [Fischer's rule; see]; (nodes 3:3)", False)])
    assert [c.text for c in cs] == ["stomata anomocytic", "pollen tricolpate [Fischer's rule; see]",
                                   "(nodes 3:3)"]
    assert cs[1].apomorphy and cs[1].apo == ["pollen tricolpate"]
    assert cs[2].uncertain and not cs[0].apomorphy


# --------------------------------------------------------------------- pages


PAGE_A = """<html><body>
<p><b><a name=Root>ROOTOPHYTA</a></b> Somebody</p>
<p>trait one; <b class="apo">trait two</b>.</p>
<p>[ALPHALES + BETALES]: <b class="apo">shared thing +</b>; other thing.</p>
<p><b><font><a name="Alphales" anc="AlpBet">ALPHALES</a></font></b> Author</p>
<p>Just the 2 families, 10 genera, 100 species.</p>
<p>Synonymy: <b>Alphanae</b> Reveal</p>
<p><a name="FooBar" anc="Alphales"><u>[Fooaceae + Baraceae]</u></a>: leaves opposite.</p>
<p><b><a name="Fooaceae" anc="FooBar">FOOACEAE</a></b> Smith, <i>nom. cons.</i> - <a href="#Alphales">Back to Alphales</a></p>
<p class=MsoNormal>Herbs; <b class="apo">flowers blue</b>; (fruit a berry).</p>
<p>5/50: Tropical Africa. Map: from Doe (2001).</p>
<p style="text-indent: 30px;"><b>Age.</b> Crown age ca 50 Ma (Doe &amp; Roe 1999b).</p>
<p><a name="Fooideae" anc="Fooaceae"><b>1. Fooideae</b></a> Jones &mdash;&mdash; Synonymy: <b>Fooidaceae</b> Lee</p>
<p>2/20. Madagascar.</p>
<p style="text-indent: 30px;"><b>Phylogeny.</b> See Smith et al. (2010).</p>
<p><b><a name="Baraceae" anc="FooBr">BARACEAE</a></b> Brown</p>
<p>3/30. Asia.</p>
</body></html>"""

PAGE_B = """<html><body>
<p><b><a name=Root>ROOTOPHYTA</a></b> Somebody</p>
<p>trait one; <b class="apo">trait two</b>.</p>
<p>[ALPHALES + BETALES]: <b class="apo">shared thing +</b>.</p>
<p><b><a name="Betales" anc="AlpBet">BETALES</a></b> Author</p>
<p><b><a name="Bazaceae" anc="Betales">BAZACEAE</a></b> Brown</p>
</body></html>"""


def test_parse_page_structure():
    hs = {h.label: h for h in pages.parse_page("a.htm", PAGE_A)}
    assert set(hs) >= {"Rootophyta", "[Alphales + Betales]", "Alphales", "[Fooaceae + Baraceae]",
                       "Fooaceae", "Fooideae", "Baraceae"}
    foo = hs["Fooaceae"]
    assert foo.rank == "family" and foo.authorship == "Smith, nom. cons"
    assert [c.text for c in foo.characters] == ["Herbs", "flowers blue", "(fruit a berry)"]
    assert foo.counts == {"genus_count": 5, "species_count": 50}
    assert foo.distribution == "Tropical Africa"
    assert [m.text for m in foo.topics["Age"]] == ["Doe & Roe 1999b"]
    # family-level discussion after a subfamily belongs to the family
    assert "Phylogeny" in foo.topics and "Phylogeny" not in hs["Fooideae"].topics
    assert hs["Fooideae"].synonyms == [("Fooidaceae", "Lee")]
    assert hs["Alphales"].counts == {"family_count": 2, "genus_count": 10, "species_count": 100}
    assert hs["Alphales"].synonyms == [("Alphanae", "Reveal")]


def test_assembler_merges_pages_and_picks_parents():
    asm = Assembler({"a.htm": pages.parse_page("a.htm", PAGE_A),
                     "b.htm": pages.parse_page("b.htm", PAGE_B)})
    labels = {n.label: k for k, n in asm.nodes.items()}
    parents = {asm.nodes[k].label: asm.nodes[p].label for k, (p, _, _) in asm.parents().items()}
    # the repeated spine is one node per clade
    assert sum(1 for n in asm.nodes.values() if n.label == "Rootophyta") == 1
    assert parents["Alphales"] == "[Alphales + Betales]"
    assert parents["Betales"] == "[Alphales + Betales]"
    assert parents["[Alphales + Betales]"] == "Rootophyta"
    # bracket topology wins over a typo'd anc="FooBr"
    assert parents["Baraceae"] == "[Fooaceae + Baraceae]"
    assert parents["Fooideae"] == "Fooaceae"
    assert parents["Bazaceae"] == "Betales"
    assert "Rootophyta" in labels


def test_family_discussion_after_subgroups_belongs_to_family():
    # 'Saraceae' is a tribe (after Saraca) though it ends in -aceae; the clade of tribes
    # must not end the family's context, so the closing Evolution paragraph is Fooaceae's.
    html = """<html><body>
<p><b><a name="Alphales" anc="X">ALPHALES</a></b> Author</p>
<p><b><a name="Fooaceae" anc="Alphales">FOOACEAE</a></b> Smith</p>
<p><a name="SarBaz" anc="Fooaceae"><u>[Saraceae + Bazeae]</u></a>: pods.</p>
<p><a name="Saraceae" anc="SarBaz"><b>1A. Saraceae</b></a> Doe</p>
<p style="text-indent: 30px;"><b>Age.</b> Young (Doe 2001).</p>
<p><a name="Bazeae" anc="SarBaz"><b>1B. Bazeae</b></a> Doe</p>
<p style="text-indent: 30px;"><b>Evolution:</b> <u>Divergence &amp; Distribution.</u> Old (Roe 2002).</p>
</body></html>"""
    hs = {h.label: h for h in pages.parse_page("a.htm", html, families={"fooaceae"})}
    assert hs["Saraceae"].rank == "tribe"
    assert list(hs["Saraceae"].topics) == ["Age"]
    assert "Evolution/Divergence & Distribution" in hs["Fooaceae"].topics
    assert not hs["Bazeae"].topics


def _spine_page(order: str, spine: str) -> str:
    return f"""<html><body>
<p><b><a name=Root>ROOTOPHYTA</a></b> Somebody</p>
<p>trait one.</p>
<p>{spine}: shared thing.</p>
<p><b><a name="{order}">{order.upper()}</a></b> Author</p>
<p><b><a name="{order}F" anc="{order}">{order.upper()[:-4]}ACEAE</a></b> Brown</p>
</body></html>"""


def test_assembler_leaves_out_clades_contradicted_by_other_pages():
    # two pages say [Alphales + [Betales + Gammales]]; a stale one says [[Alphales + Betales] + Gammales]
    current = "[ALPHALES + [BETALES + GAMMALES]]</p><p>[BETALES + GAMMALES]"
    stale = "[[ALPHALES + BETALES] + GAMMALES]</p><p>[ALPHALES + BETALES]"
    asm = Assembler({"a.htm": pages.parse_page("a.htm", _spine_page("Alphales", current)),
                     "b.htm": pages.parse_page("b.htm", _spine_page("Betales", current)),
                     "g.htm": pages.parse_page("g.htm", _spine_page("Gammales", stale))})
    label = lambda k: asm.nodes[k].label
    parents = {label(k): label(p) for k, (p, _, _) in asm.parents().items()}
    assert {label(k): label(v) for k, v in asm.conflicts.items()} == \
        {"[Alphales + Betales]": "[Betales + Gammales]"}
    assert "[Alphales + Betales]" not in parents
    assert parents["Betales"] == parents["Gammales"] == "[Betales + Gammales]"
    assert parents["Alphales"] == parents["[Betales + Gammales]"] == "[Alphales + [Betales + Gammales]]"


def _family_page(body: str) -> dict[str, list[pages.Header]]:
    html = f"""<html><body>
<p><b><a name="Alphales" anc="X">ALPHALES</a></b> Author</p>
<p><b><a name="Fooaceae" anc="Alphales">FOOACEAE</a></b> Smith</p>
{body}
</body></html>"""
    return {"a.htm": pages.parse_page("a.htm", html, families={"fooaceae"})}


def _parents(asm: Assembler) -> dict[str, str]:
    label = lambda k: asm.nodes[k].label
    return {label(k): label(p) for k, (p, _, _) in asm.parents().items()}


def test_assembler_merges_copies_of_a_clade():
    # the same three subfamilies nested two ways, and a misspelt copy with an 'etc'
    asm = Assembler(_family_page("""
<p><a name="AB" anc="Fooaceae"><u>[Aoideae + [Boideae + Coideae]]</u></a>: x.</p>
<p><a name="BA"><u>[Coideae + [Aoideae + Boideae]]</u></a>: x.</p>
<p><a name="Aoideae" anc="AB"><b>Aoideae</b></a> Doe</p>
<p><a name="Boideae" anc="AB"><b>Boideae</b></a> Doe</p>
<p><a name="Coideae" anc="AB"><b>Coideae</b></a> Doe</p>
<p><a name="DE"><u>[etc + [Doideae + Eoideae]]</u></a>: x.</p>
<p><a name="DE2"><u>[etc + [Doideae + Eiodeae]]</u></a>: x.</p>
<p><a name="Doideae" anc="DE"><b>Doideae</b></a> Doe</p>
<p><a name="Eoideae" anc="DE"><b>Eoideae</b></a> Doe</p>"""))
    parents = _parents(asm)
    outer = {"[Aoideae + [Boideae + Coideae]]", "[Coideae + [Aoideae + Boideae]]"}
    assert len(outer & set(parents)) == 1                  # one node for both
    assert len(asm.conflicts) == 1                         # one inner pair left out
    assert len({"[etc + [Doideae + Eoideae]]", "[etc + [Doideae + Eiodeae]]"} & set(parents)) == 1
    assert parents["Doideae"] == parents["Eoideae"]


def test_assembler_nests_a_grade_written_as_a_run_of_headers():
    # grades of one clade, written as headers anc-linked each to the last (APWeb's
    # Senegalia i-iv); their order here is not alphabetical
    asm = Assembler(_family_page("""
<p><a name="G1" anc="Fooaceae"><u>[Acacia z + [Calliandra + Inga]]</u></a>: x.</p>
<p><a name="G2" anc="G1"><u>[Acacia y + [Calliandra + Inga]]</u></a>: x.</p>
<p><a name="G3" anc="G2"><u>[Acacia x + [Calliandra + Inga]]</u></a>: x.</p>
<p><a name="Calliandra" anc="G3"><b>Calliandra</b></a> Doe</p>
<p><a name="Inga" anc="G3"><b>Inga</b></a> Doe</p>"""))
    parents = _parents(asm)
    assert parents["[Acacia x + [Calliandra + Inga]]"] == "[Acacia y + [Calliandra + Inga]]"
    assert parents["[Acacia y + [Calliandra + Inga]]"] == "[Acacia z + [Calliandra + Inga]]"
    assert parents["[Calliandra + Inga]"] == "[Acacia x + [Calliandra + Inga]]"


def test_assembler_places_orders_under_subclasses_on_multi_order_pages():
    # like Sporing.html: one page treating several orders, grouped by subclass
    html = """<html><body>
<p><b><a name=Root>ROOTOPHYTA</a></b> Somebody</p>
<p><b><a name=Liv>LIVEROPHYTA</a></b> Somebody</p>
<p><b><a name="Aales">AALES</a></b> Author</p>
<p><b><a name="Ferns">FERNOPSIDA</a></b> Somebody</p>
<p><b><a name="Ophidae">OPHIDAE</a></b> Somebody</p>
<p><b><a name="Bales">BALES</a></b> Author</p>
<p><b><a name="Baceae">BACEAE</a></b> Brown</p>
<p><b><a name="Polyidae">POLYIDAE</a></b> Somebody</p>
<p><b><a name="Cales">CALES</a></b> Author</p>
</body></html>"""
    parents = _parents(Assembler({"s.htm": pages.parse_page("s.htm", html)}))
    assert parents["Bales"] == "Ophidae"
    assert parents["Cales"] == "Polyidae"
    assert parents["Baceae"] == "Bales"


def test_errata_lists_conflicts_typos_and_broken_links():
    from apw.sources.apweb import errata
    current = "[ALPHALES + [BETALES + GAMMALES]]</p><p>[BETALES + GAMMALES]"
    stale = "[[ALPHALES + BETALES] + GAMMALES]</p><p>[ALPHALES + BETALES]"
    htmls = {"a.htm": _spine_page("Alphales", current), "b.htm": _spine_page("Betales", current),
             "g.htm": _spine_page("Gammales", stale).replace("</body>", """
<p><a name="FooBar" anc="Gamales"><u>[Fooideae + [Barideae + Bazideae]</u></a>: x.</p>
<p><u>[Fooideae + Barideae] - if this clade exists</u>: y.</p>
<p><a name="Fooideae"><b>Fooideae</b></a> Doe</p>
<p><a name="Barideae"><b>Barideae</b></a> Doe</p>
<p><a name="Bazideae"><b>Bazideae</b></a> Doe <a href="zetalesweb.htm">Zetales</a></p>
<p><u>[Barideae + Bazidieae]</u>: z.</p>
<p><u>[Fooideae, Barideae Bazideae]</u>: w.</p>
</body>""")}
    asm = Assembler({p: pages.parse_page(p, h) for p, h in htmls.items()})
    manifest = {f"orders/{p}": {"sha256": p} for p in htmls}
    er = errata.build("2026-01-01", manifest, htmls, asm)
    assert [(c.rejected, c.accepted, c.pages) for c in er.backbone] == \
        [("[Alphales + Betales]", "[Betales + Gammales]", ["g.htm"])]
    assert any("if this clade exists" in q.text for q in er.alternatives)
    assert ("g.htm", ("Bazideae", "Bazidieae")) in {
        (p, tuple(sorted((w, o)))) for p, _, w, _, o, _, _ in er.spellings}
    assert [t for _, _, t in er.brackets] == ["[Fooideae + [Barideae + Bazideae]"]
    assert [n for _, n in er.run_on] == ["Barideae Bazideae"]          # a lost comma
    assert [(p, t) for p, _, _, t, _ in er.anc] == [("g.htm", "Gamales")]
    assert er.dead_links == [("g.htm", "orders/zetalesweb.htm")]
    assert er.dead_link_targets == {}
    assert "APWeb errata" in errata.render(er)


def test_crawl_alias_of_broken_order_links():
    from apw.sources.apweb import crawl
    known = {"orders/piperalesweb2.htm", "orders/dillenialesweb.html", "orders/sapindalesweb.htm",
             "orders/lauralesweb.htm", "orders/Sporing.html", "orders/sporing.html"}
    assert crawl.alias_of("orders/piperalesweb.htm", known) == "orders/piperalesweb2.htm"
    assert crawl.alias_of("orders/dillenialesweb.htm", known) == "orders/dillenialesweb.html"
    assert crawl.alias_of("orders/sapindalesweb2.htm", known) == "orders/sapindalesweb.htm"
    assert crawl.alias_of("orders/lauralesweb\\.htm", known) == "orders/lauralesweb.htm"
    assert crawl.alias_of("orders/Sporing.html.htm", known) == "orders/Sporing.html"
    assert crawl.alias_of("orders/dasypogonalesweb.html", known) is None

