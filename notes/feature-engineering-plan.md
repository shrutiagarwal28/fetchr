# Feature Engineering Plan

> Based on the 100-dog PetFinder dataset audited in `audit.ipynb` (May 2026).
> Field coverage numbers come from that notebook — not estimates.

---

## Key Audit Findings That Changed the Plan

These are the fields where the real data contradicted assumptions going in:

| Field | Assumption | Reality |
|---|---|---|
| `activity_level` | Tier 2 matching signal | **0% populated** — PetFinder never sends it. Drop entirely. |
| `requires_fenced_yard` | Tier 1 matching signal | **0% populated** — PetFinder never sends it. Drop entirely. |
| `good_with_other_animals` | Soft filter | **93% null** — too sparse to use. Drop. |
| `tags` | Multi-hot encode like `personality_traits` | **0% populated** — PetFinder sends `[]` always. Drop. |
| `age_years_approx` | Primary continuous age feature | **100% null** — not yet derived. Must be computed from `age_range_label` / `birth_date`. |
| `weight_min` / `weight_max` | Continuous weight features | **Fixed PetFinder bands per size** — every Medium dog gets exactly 26-60 lbs. Redundant with `size` as an ML feature. Use for SQL range filters only. |
| `age_category` | Reliable age signal | **38% mismatch rate vs DOB-calculated age** — shelters enter this manually and rarely update it. `age_range_label` → derived `age_years_approx` should be prioritized over it. |

---

## Confirmed Matching Signals (with Real Coverage Numbers)

| Field | Coverage | Notes |
|---|---|---|
| `status` | 100% | Hard SQL filter always — `WHERE status = 'available'` |
| `size` | 100% | 3 values in current data (no xlarge yet); keep all 4 in the encoder |
| `age_category` | 100% | Use as fallback; 38% mismatch vs actual DOB |
| `gender` | 100% | Clean 54% female / 46% male |
| `breed_primary` | 100% | Only 24 unique values in 100-dog sample; top-heavy (Mixed Breed 30%, Pit Bull 28%) |
| `weight_min` / `weight_max` | 100% | SQL range filter only — not as ML features alongside `size` |
| `is_mixed` | 100% | Already clean |
| `special_needs` | 100% | 98% false, 2% true |
| `vaccinated` | 97% | 96% true, 1% false, 3% null |
| `spayed_neutered` | 93% | 87% true, 6% false, 7% null |
| `house_trained` | 84% | 66% true, 18% false, 16% null |
| `color` | 94% | 11 distinct values; soft filter only |
| `good_with_dogs` | 72% | 58% true, 14% false, 28% null |
| `coat_length` | 72% | 3 values: Short (65%), Medium (6%), Curly (1%), 28% null |
| `good_with_kids` | 69% | 62% true, 7% false, 31% null |
| `personality_traits` | 85% | 51 unique traits; 15% of dogs have `[]` empty list |
| `description` | 100% | Median 980 chars — excellent for embeddings |
| `good_with_cats` | 27% | **Risky** — 73% null; soft filter only, never hard filter |

---

## Fields Removed From Matching Plan

| Field | Reason |
|---|---|
| `activity_level` | 0% populated by PetFinder |
| `requires_fenced_yard` | 0% populated by PetFinder |
| `good_with_other_animals` | 93% null — too sparse |
| `tags` | 0% populated — PetFinder always sends `[]` |
| `weight_range_label` | Exact duplicate of `size` in different format |
| `behavior_other_animals` | 100% null |
| `knows_basic_commands` | 100% null |

These are kept in the schema for future use but must not appear in any matching filter or feature vector.

---

## Where Features Should Live

**Recommendation: a separate `dog_features` table** with a 1:1 FK to `dog_profiles.id`.

Features are derived artifacts that may need to be recomputed as encoding strategy evolves. A separate table means you can drop and rebuild it without touching the ingestion pipeline. This is the **Feature Store pattern** — establish the boundary at the start.

---

## Phase 1 — Ordinal Encoding of Categorical Enums

Convert ordered string enums to integers so a model can reason about magnitude.

| Raw field | Output column | Encoding |
|---|---|---|
| `age_category` | `age_category_ord` | `puppy=0, young=1, adult=2, senior=3, unknown=-1` |
| `size` | `size_ord` | `small=0, medium=1, large=2, xlarge=3` |
| `coat_length` | `coat_length_ord` | `short=0, medium=1, long=2, wirehaired=3, curly=4, null=-1` |

> `activity_level` removed — 0% populated in actual data.

**Why ordinal, not one-hot?** These have a natural order that carries meaning. One-hot would discard the "large > medium > small" relationship and inflate column count for no gain.

---

## Phase 2 — Three-State Boolean Normalization

Convert nullable booleans to `1 = yes, 0 = no, -1 = unknown`.

**Why null ≠ false:** If a shelter didn't fill in `good_with_dogs`, that's not "bad with dogs" — it's an absence of information. A model treating null as false would penalize dogs with incomplete records, not actually dangerous ones.

| Raw field | Output column | Coverage |
|---|---|---|
| `good_with_kids` | `compat_kids` | 69% known |
| `good_with_dogs` | `compat_dogs` | 72% known |
| `good_with_cats` | `compat_cats` | 27% known — soft filter only |
| `house_trained` | `house_trained_flag` | 84% known |
| `vaccinated` | `vaccinated_flag` | 97% known |
| `spayed_neutered` | `spayed_neutered_flag` | 93% known |

> `requires_fenced_yard` removed — 0% populated.
> `good_with_other_animals` removed — 93% null.

---

## Phase 3 — Age as a Continuous Feature

Build a single, non-null `age_years_imputed` float.

**The problem:** `age_years_approx` is currently 100% null — it hasn't been derived yet. `birth_date` is only 29% populated. The derivation path must be:

1. **If `birth_date` is present** → calculate exact age: `(today - birth_date).days / 365.25` (most precise)
2. **Else if `age_range_label` is present** → parse midpoint from the range string:

| age_range_label | midpoint |
|---|---|
| `(less than 1 year)` | 0.5 |
| `(1-3 years)` | 2.0 |
| `(3-8 years)` | 5.5 |
| `(8+ years)` | 10.0 |

3. **Else fall back to `age_category` midpoint** (same values as above, as last resort)

Output:
- `age_years_imputed` — float, no nulls
- `age_was_imputed` — boolean, True if derived from label or category (not from actual DOB)

**Important from the audit:** 38% of DOB-checked dogs had `age_category` disagree with their actual age. Shelters enter it manually and don't update it. `age_years_imputed` derived from `birth_date` or `age_range_label` is more reliable than `age_category` alone.

**Why not normalize to [0,1] yet?** Normalization belongs at the model training step. The feature store stores interpretable values: `0.5` means 6 months old. A min-max-scaled `0.03` means nothing to a human debugging a pipeline.

---

## Phase 4 — Breed Group Derivation

Map `breed_primary` (24 unique values in current data) to a small controlled vocabulary.

**Current top breeds from the audit:**
- Mixed Breed: 30 dogs
- Pit Bull Terrier: 28 dogs
- American Staffordshire Terrier: 8 dogs
- Shepherd: 5 dogs
- Labrador Retriever: 3 dogs
- Hound: 3 dogs

Given the dataset is 58% pit bulls / mixed breeds, the grouping must handle these well.

**Proposed groups:**

| Group | Examples in data |
|---|---|
| Bully | Pit Bull Terrier, American Staffordshire Terrier, American Bulldog, Dogo Argentino |
| Mixed | Mixed Breed (any `is_mixed=True`) |
| Sporting / Retriever | Labrador Retriever, Black Labrador Retriever |
| Herding / Shepherd | Shepherd, German Shepherd Dog |
| Hound | Hound, Catahoula Leopard Dog |
| Terrier | Boston Terrier, Yorkshire Terrier, Jack Russell |
| Working / Nordic | Siberian Husky |
| Toy | Shih Tzu |
| Unknown | anything unmatched |

Output columns:
- `breed_group` — VARCHAR, ~9 values
- `is_purebred` — BOOLEAN = `NOT is_mixed AND breed_secondary IS NULL`

**Note:** This mapping is a lookup dict, not ML. It must be reviewed by a human. Unmatched breed names fall through to `"Unknown"` — never raise an error. As the dataset grows past 100 dogs, revisit the grouping.

---

## Phase 5 — Personality Traits Multi-Hot Encoding

Expand `personality_traits` JSONB list into individual boolean columns.

**Actual vocabulary from the audit** (51 unique traits, 85% of dogs have at least one):

Top traits by frequency:
| Trait | Count |
|---|---|
| Affectionate | 57 |
| Friendly | 56 |
| Playful | 47 |
| Curious | 43 |
| Funny | 39 |
| Athletic | 34 |
| Smart | 28 |
| Gentle | 23 |
| Quiet | 17 |
| Housetrained | 15 |
| Brave | 15 |
| Loyal | 14 |
| Good with Dogs | 14 |
| Loves Kisses | 13 |
| Crate Trained | 12 |
| Good with Kids | 10 |
| Dignified | 10 |
| Independent | 10 |
| Couch Potato | 8 |
| Good with Cats | 7 |

**Important:** Some traits (`Housetrained`, `Good with Dogs`, `Good with Kids`, `Good with Cats`, `Crate Trained`) overlap with the boolean fields. Don't double-count them in the feature vector — use the boolean fields (which are more reliable) and treat the trait as supplementary confirmation.

**Encoding:**
- Top 15-20 traits by frequency → individual `trait_<name>` BOOLEAN columns
- Everything outside the top-N → `other_traits_count` INTEGER
- 15% of dogs have `[]` (not null) — treat as all-zero, not missing

> `tags` removed entirely — 0% populated.

---

## Phase 6 — Temporal Features

| Output column | Formula | Notes |
|---|---|---|
| `days_listed` | `CURRENT_DATE - listed_at::date` | Fall back to `first_seen_at` if `listed_at` null |
| `log_days_listed` | `log(days_listed + 1)` | Compress outlier tail |

`listed_at` is 100% null in the current dataset (PetFinder didn't send it). Fall back entirely to `first_seen_at` for now.

---

## Recommended Build Order

```
Phase 0: Derivation — age_years_approx
  └─ Parse age_range_label midpoints for all 100 dogs
  └─ Override with birth_date calculation where available (29 dogs)
  └─ Store result in dog_profiles.age_years_approx

Phase 1: Ordinal encodings (size, age_category, coat_length)
Phase 2: Three-state boolean normalization (good_with_*, house_trained, etc.)
Phase 3: age_years_imputed + age_was_imputed
Phase 4: Breed group mapping (lookup dict, informed by audit)
Phase 5: Personality traits multi-hot (vocabulary confirmed above)
Phase 6: Temporal features (days_listed using first_seen_at)
```

---

## What This Does NOT Include (by Design)

- **Embeddings** — `description` is 100% populated with median 980 chars. Excellent candidate when you get there.
- **User-side features** — the matching algorithm requires both sides. Dog features are half the pair.
- **Collaborative filtering** — no swipe/like history yet. Skip until you have it.
- **Normalization/scaling to [0,1]** — belongs at the model training step, not the feature store.
