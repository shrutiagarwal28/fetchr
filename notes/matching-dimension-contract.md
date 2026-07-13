# Matching Dimension Contract

> Design doc — 2026-07-12. No code yet.
> This is the **shared vector space** the matcher operates in: the canonical list of
> matching dimensions, and the mapping from adopter inputs → those dimensions.
>
> **Why this doc exists:** `adopter-profile-matching-design.md` and `matching-app-schema.md`
> were written (June 2026) against an older `dog_features` schema — `*_ord`/`*_flag` naming and
> a **multi-hot `trait_*`** personality encoding. Phase 5 replaced multi-hot with **semantic
> dimension scores** (see the feature-engineering plan). This doc is the reconciliation point;
> the two older docs need updating to match (tracked in "Stale references to fix" below).

---

## The core idea: one shared space for dogs AND adopters

Both a dog and an adopter are represented as a vector over the **same set of dimensions**.

- A **dog's** vector is computed from its features (`dog_features`).
- An **adopter's** vector lives in the same space — mostly *learned* from which dog-vectors they
  right-swipe (per the behavioral model in `adopter-profile-matching-design.md`), and partly set
  by explicit pop-up answers.
- **Matching = similarity** between the two vectors, after hard safety filters.

This is why the dimension list is *the* contract: it's literally the coordinate system both sides
are expressed in. Get it right and the adopter side "just" learns weights in this space.

---

## Two tiers: hard filters vs. soft scoring

Not every field is a similarity axis. Split them:

### Tier 1 — Hard filters (safety / absolute constraints)
Applied as SQL `WHERE`, before any scoring. Never soft. Rule from the null-trap analysis:
**only exclude a dog that is *known bad* (value = 0), never one that is merely unknown.**

| Adopter fact | Filter | Feature source |
|---|---|---|
| `zip_code` + `max_distance_miles` | location radius (PostGIS) | `dog_profiles` city/lat/lng |
| `has_children` + `youngest_child_age < 8` | exclude `good_with_kids_enc = 0` | Phase 2 |
| `has_existing_dogs` | exclude `good_with_dogs_enc = 0` | Phase 2 |
| `has_existing_cats` | exclude `good_with_cats_enc = 0` (64% unknown — exclude only known-bad) | Phase 2 |
| `max_dog_size` (if set) | exclude `size_enc > max` | Phase 1 |
| (always) | `status = 'available'` | `dog_profiles` |

### Tier 2 — Soft scoring dimensions (the "categories")
The similarity space. Each dimension below is a coordinate; the adopter has a target value +
importance weight, the dog has a score, and the match adds `weight × alignment`.

| Dimension | Dog feature | Type | Phase |
|---|---|---|---|
| size | `size_enc` | ordinal 1–4 | 1 |
| age | `age_years_imputed` (+`age_was_imputed` confidence) | continuous | 1/3 |
| grooming effort | `coat_type_enc` | ordinal 1–3 | 1 |
| breed group | `breed_group` | nominal (audit-only for pit-type — never a filter) | 4 |
| **energy** | `energy_score` | bipolar −1..+1 | 5 |
| **affection** | `affection_score` | unipolar 0..+1 | 5 |
| **sociability** | `sociability_score` | unipolar 0..+1 | 5 |
| **playfulness** | `playfulness_score` | unipolar 0..+1 | 5 |
| **trainability** | `trainability_score` | bipolar −1..+1 | 5 |
| **confidence** | `confidence_score` | bipolar −1..+1 | 5 |
| **independence** | `independence_score` | bipolar −1..+1 | 5 |
| **special_needs** | `special_needs_score` | unipolar 0..+1 | 5 |
| **placement_restriction** | `placement_restriction_score` | unipolar 0..+1 | 5 |

---

## Adopter input → dimension mapping

This answers the original question: *"what categories can I put the dogs into?"* — and, read the
other way, *"what do I need to know about an adopter?"* Most of these are **learned from behavior**,
not asked upfront (see the product philosophy in `adopter-profile-matching-design.md`); the
"How obtained" column notes which.

| Adopter input | Drives dimension(s) | Direction | How obtained |
|---|---|---|---|
| Home type (apartment / house / yard) | energy (↓ for apartment), placement_restriction | apartment → prefers low-energy, low-restriction | pop-up |
| Activity level (sedentary…very active) | **energy** (primary) | sedentary → low energy; active → high | pop-up (hard to infer) |
| Daily schedule / hours away (e.g. 9–6 job) | energy (↓), independence (↑ tolerance), special_needs (↓ capacity) | long hours → low-energy, independent, low-care dog | pop-up |
| Experience (first-time…experienced) | trainability (↑ for novice), confidence (novice avoids very-shy/reactive), special_needs (↑ for experienced) | novice → easy, stable, low-needs | pop-up |
| Wants a cuddler vs. an independent dog | affection, independence | — | learned from swipes / pop-up |
| Wants a playful vs. calm dog | playfulness, energy | — | learned from swipes |
| Openness to special needs | special_needs | gate + soft | pop-up after context |
| Existing dogs / cats | good_with_dogs/cats_enc | **hard filter** | onboarding |
| Children (+ ages) | good_with_kids_enc (hard), placement_restriction (adult-only/older-kids) | — | onboarding |
| Size preference | size_enc | hard-ish (max) + soft | learned from swipes |
| Grooming tolerance | coat_type_enc | — | learned / late pop-up |

---

## Open questions (resolve before locking the contract)

1. **Do we need a `reactivity` / noise axis?** Apartment adopters care a lot about barking and
   reactivity, but we have no clean trait signal for it today ("Quiet" feeds energy/independence,
   not noise). Options: (a) add a `reactivity_score` dimension and hope future scrapers/traits
   feed it; (b) fold "Quiet" partially into a noise proxy; (c) defer until a source provides it.
   **Leaning (c)** — don't invent an axis with no data behind it (same discipline as not
   fabricating truncated traits).

2. **Hard vs. soft for size, age, special_needs.** Size and age read as preferences (soft), but
   some adopters mean them absolutely ("I physically cannot handle an XL dog"). Proposal: keep a
   single *hard* cap (`max_dog_size`) + *soft* preference within the allowed range. Confirm.

3. **Absence handling in the match, not just the feature.** A dog with `energy_score = 0` means
   "no energy signal," not "medium energy." The matcher must not treat 0 as a confident midpoint —
   e.g. down-weight the energy term's contribution when the dog has no energy signal, rather than
   scoring it as a perfect match for a "moderate energy" adopter. Decide the exact rule.

4. **Bipolar vs unipolar asymmetry in similarity.** Comparing an adopter's target to a bipolar
   dog score (−1..+1) vs a unipolar one (0..+1) needs a consistent distance metric. Normalize both
   sides to the same range per dimension before combining.

---

## Stale references to fix (follow-up, not now)

- `matching-app-schema.md` "Data Contract with fetchr" table: rename `*_ord`→`*_enc`,
  `compat_*`→`good_with_*_enc`, `*_flag`→`*_enc`; **remove `trait_*` multi-hot + `other_traits_count`**;
  add the nine Phase 5 `*_score` columns; drop `is_purebred`/`days_listed` until built.
- `adopter-profile-matching-design.md` Matching Function step 2: replace "personality trait overlap
  (multi-hot dot product)" with "dimension-vector similarity over the Phase 5 `*_score` columns."

---

## Relationship to other docs

- **Behavioral/product model + schema:** `adopter-profile-matching-design.md` (how preferences are
  learned, pop-up system, cold-start, repo boundary). Still valid *except* the feature-column
  references above.
- **Adopter-side tables:** `matching-app-schema.md` (needs the column reconciliation above).
- **Dog-side features:** feature-engineering plan (Phases 0–6) + `data/trait_lexicon.json` (the
  trait→dimension weights, built next).
- **The handoff:** `dog_features` remains the single interface between fetchr and the matching app.
