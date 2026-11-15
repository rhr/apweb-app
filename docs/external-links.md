# Linking to Open Tree of Life and TimeTree: ideas

Status: exploratory, 2026-11-14. Nothing is built yet.

This note is based on two papers:

- McTavish et al. 2021, *Syst. Biol.* 70:1295, on the `opentree` Python package.
- Kumar et al. 2022, *Mol. Biol. Evol.* 39:msac174, on TimeTree 5.

The current APIs, synthetic-tree version and data terms must be checked before any code is
written.

Related: `docs/timetree.md` (the timetree from APWeb ages).

## Why link

Both projects synthesise published trees, as APWeb does by expert curation. The app's approach is
to compare sources, not merge them, so each project would come in as a separate source with its
own claims. APWeb data is never overwritten.

| | Open Tree of Life | TimeTree 5 |
|---|---|---|
| Gives us | Identifiers (OTT ids, which link to NCBI and GBIF) and a second topology, with studies that support or conflict with each node | A second, independent set of clade ages (median, confidence interval and per-study times) |
| Keyed by | OTT ids, matched to names through its name-matching service (TNRS) | NCBI taxon ids |
| Licence | The `opentree` package is CC0. The data licence still needs confirming. | The paper is CC-BY. Data use is described as "research and teaching (see timetree.org)", and the full collection is available on request. **Check before storing.** |

## Open Tree of Life

### Identifiers

- APWeb's named clades (orders, families, genera, most tribes) can be mapped to OTT ids through
  Open Tree's name-matching service.
- Unnamed clades such as `[Lactoridaceae + Aristolochiaceae]` can be mapped to the node in Open
  Tree's synthetic tree that is the common ancestor of their members' OTT ids.
- OTT records include NCBI and GBIF ids. The NCBI ids are what TimeTree needs.
- OneZoom keys its lookups on OTT ids. The lack of OTT ids is the main gap noted in
  `backend/src/apw/onezoom.py`, so this mapping also unblocks the OneZoom view: images, common
  names and dates.

### Comparing the two trees

- For each node in its synthetic tree, Open Tree's API lists the input studies that support it and
  those that conflict with it. For each APWeb clade, the app could show:
  - whether the synthetic tree has the clade;
  - how many studies support it and how many conflict with it.

  This works like a per-clade version of the existing `conflicts_with` predicate.
- A report listing where APWeb and Open Tree disagree, similar to the errata report.
- The paper says Open Tree's conflict tools also accept a tree you supply. That would allow the
  whole APWeb tree to be checked against the synthesis.

### Literature

Open Tree's source studies carry DOIs, which can link to the store's `reference` table.

## TimeTree 5

### API (as described in the paper)

| Route | Returns |
|---|---|
| `/mrca/id/<NCBI ids joined by +>` | The age of the clade's crown (the common ancestor of the given taxa). Add `/age`, `/ci` or `/study_count` for one field. |
| `/pairwise/<id>/<id>` | The divergence time between two taxa |
| `/timeline/<id>` | Divergence times from the taxon back to the root |

- Add `csv` to get each study's times, or `summaryjson` to get the summary as JSON.
- The API is at `timetree.temple.edu/api`.

### Uses

1. **Validate our age parser.**
   - Many TimeTree studies are probably the same papers APWeb cites, such as Magallón, Bell and
     Wikström.
   - Matching TimeTree's per-study times to our `crown_age` claims, by study, would measure our
     citation binding and number parsing. That is a better check than hand samples.
2. **A second age source for timetree step 2.**
   - Store TimeTree ages as `crown_age` claims from a separate `timetree5` source.
   - Offer "TimeTree median" as a selection rule or a fallback.
   - TimeTree ages could also date nodes APWeb leaves undated, so fewer nodes need interpolating.
3. **A method to borrow.** TimeTree does what step 2 plans:
   - it takes the median across studies;
   - it applies "adjusted times" when a node is older than its parent. When a node b is older than
     its parent a, the ages of a, b and b's oldest child c are set to the average of t(a) and
     t(c), repeated until the ages are in order.

   Step 2b could offer this as an adjustment option and cite the paper.

## Things to be careful about

- **TimeTree's licence.**
  - Storing TimeTree ages in the public store needs explicit permission or clear terms.
  - If neither exists, TimeTree may only be linked to, or used for checking without storing.
  - Before building, decide whether per-study times may be stored, and how to attribute them.
- **Different trees.**
  - TimeTree builds on the NCBI taxonomy, refined by published timetrees. It only counts a study's
    time when the two clades are monophyletic in that study.
  - Its common ancestor of an APWeb clade's members may not be the same clade.
  - Each match must check that the member sets agree before an age is attached, and record the
    match quality.
- **Mapping errors.** These need a report of their own, like the errata report:
  - names shared between a plant genus and an animal genus;
  - fuzzy matches from the name-matching service;
  - taxa that are not monophyletic in the synthetic tree;
  - APWeb names that have no OTT record.
- **Snapshot or live calls.** Should the OTT taxonomy dump and the synthetic tree be downloaded and
  versioned, as APWeb snapshots are, or should the APIs be called with a cache? Downloads are
  easier to reproduce. About 2,000 calls are needed to cover the dated nodes, so either approach
  is feasible.
- **Molecular only.** TimeTree uses molecular timetrees. APWeb's `fossil_age` claims stay separate
  evidence for minimum ages.

## How it would fit the store

- **New sources.** `ott@<version>` and/or `opentree-synth@<version>`, and `timetree5`. Each keeps
  its own concepts and claims.
- **A new predicate,** along the lines of `same_as`. It links an APWeb concept to an external id.
  Qualifiers:
  - `method`: exact name, name-service fuzzy match, or common ancestor;
  - `confidence`;
  - `members_agree`: whether the clade's member sets agree.
- **Support from Open Tree.** Stored as claims on the Open Tree source's concepts, such as
  `supported_by` and `conflicts_with` linking to study references, then shown on the matching
  APWeb clade.
- **TimeTree ages.** Stored as `crown_age` claims with the TimeTree source, carrying `ci`,
  `study_count` and the per-study citations where the licence allows.

## Suggested order

1. **Map APWeb clades to OTT ids.**
   - Named clades go through the name-matching service. Unnamed ones go through the common
     ancestor of their members.
   - Add the `same_as` predicate and a mapping report.
   - This is the foundation for everything else, and it unblocks OneZoom.
2. **Compare with Open Tree.** Show support and conflict per clade on the taxon page, and add a
   disagreement report.
3. **Add TimeTree ages,** once the licence is clear.
   - First use them to validate the age parser.
   - Then use them as a second age source in timetree step 2's rules.

The timetree step 2 work in `docs/timetree.md` can go ahead on APWeb ages alone. Doing step 1
here first means step 2's selection rules are designed with more than one age source in mind.

## Open questions

- What do TimeTree's terms allow us to store and show publicly?
- Should Open Tree data be a versioned snapshot or live API calls with a cache?
- Should Open Tree support and conflict appear on taxon pages, or only in a comparison view or
  report?
- Should step 2 wait for the OTT mapping, or go ahead on APWeb ages alone?
