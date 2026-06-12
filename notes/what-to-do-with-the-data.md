# Matching Fields + What To Do Next

## Context

fetchr is a data ingestion layer for a dog-to-adopter matching platform. The scraper is
fully built and the DogProfile model captures all 109 fields from PetFinder. The DB has
100 dogs scraped from NJ/Jersey City. The audit notebook (audit.ipynb) has been fully
updated for the 109-field model — it classifies all 83 populated fields, documents null
rates, and establishes which fields are usable as matching signals.
No matching algorithm exists yet — this plan maps out which fields matter for matching and
what the logical sequence of next steps is.

---

## Part 1 — Fields needed for the matching algorithm

### Tier 1: Hard filters (boolean — must match exactly)
These are the fields adopters use to rule dogs in or out. Always applied first as a SQL
WHERE clause before any ML ranking.

| Field | Why |
|---|---|
| `status` | Only show `"available"` dogs — never pending/adopted |
| `good_with_kids` | Non-negotiable for families with children (69% populated) |
| `good_with_dogs` | Non-negotiable for households with dogs (72% populated) |
| `good_with_cats` | **73% null — treat as soft filter, not hard filter** (see audit §9) |
| `house_trained` | Non-negotiable for apartment adopters (84% populated) |
| `special_needs` | Some adopters specifically want / specifically cannot take special needs dogs |
| `spayed_neutered` | Many adopters filter on this (93% populated) |
| `size` | Common hard preference — people know if they want a small vs large dog (100%) |

### Tier 2: Soft filters (scored — influence ranking, not elimination)
These contribute to a match score but a null value doesn't disqualify the dog.

| Field | Coverage | Why |
|---|---|---|
| `age_category` | 100% | 38% mismatch rate vs actual DOB — treat as approximate; prefer `age_years_approx` |
| `age_years_approx` | **0% — not yet derived** | Must be computed from `birth_date` (29 dogs) or `age_range_label` midpoint (71 dogs) |
| `breed_primary` | 100% | Some adopters have breed preferences; top-heavy (Mixed 30%, Pit Bull 28%) |
| `gender` | 100% | Some adopters have preferences, others don't care; 54% F / 46% M |
| `coat_length` | 72% | Allergy-sensitive adopters care about this; 28% null |
| `color` | 94% | Low-weight soft filter; 11 distinct values |
| `vaccinated` | 97% | Health signal |
| `weight_min` / `weight_max` | 100% | SQL range filter only (`WHERE weight_max <= ?`) — **not** as ML features alongside `size` (fixed PetFinder bands per size tier, not actual weights) |

### Tier 3: Semantic signal (ML/NLP — fuzzy matching)
These fields are the richest signal but require embedding/NLP to use.

| Field | Coverage | Why |
|---|---|---|
| `description` | 100% | Median 980 chars — best signal for fuzzy matching ("I want a calm apartment dog") |
| `personality_traits` | 85% | 51 unique traits; top: Affectionate (57%), Friendly (56%), Playful (47%), Curious (43%) |

### Tier 4: Logistics (shown in results, not used for matching)
Not used for ranking but displayed to the adopter in search results.

| Field | Why |
|---|---|
| `photos` | Primary visual signal |
| `city` / `state` / `zip` | Proximity display |
| `adoption_fee` | Shown in results |
| `org_adoption_url` | "Apply to adopt" CTA |
| `shelter_name` | Shown in results |
| `petfinder_url` | Link back to source |

### Fields confirmed unpopulated — removed from matching plan
PetFinder never sends these. Stored in schema for future use but must not be used as filters or features:

| Field | Coverage | Was planned as |
|---|---|---|
| `activity_level` | 0% | Tier 2 soft filter |
| `requires_fenced_yard` | 0% | Tier 1 hard filter |
| `knows_basic_commands` | 0% | Tier 2 soft filter |
| `good_with_other_animals` | 7% | Soft filter |
| `tags` | 0% | Multi-hot encode (PetFinder always sends `[]`) |

### Fields NOT needed for matching
Everything else — org operational stats (`org_annual_adoptions`, `org_employee_count`),
PetFinder internal flags (`import_updates_enabled`, `exportApi`), contact details,
transfer history, media record metadata beyond `photos`, `microchip_id`, `internal_notes`.
Stored for completeness, not queried.

---

## Part 2 — What to do next (in order)

### ~~Step 1: Run the scraper and populate the DB~~ ✓ Done
100 dogs scraped from NJ/Jersey City. `dog_profiles` and `raw_scrapes` tables populated.

### ~~Step 2: Update the data audit notebook~~ ✓ Done
`audit.ipynb` fully rewritten for the 109-field model. Key findings documented in the
notebook and reflected in the field tiers above. See audit §9 Summary for the complete
matching signal table.

### ~~Step 3: Migrate to Postgres~~ ✓ Done
Schema live in Postgres. SQLAlchemy + Alembic managing migrations. Both PetFinder and
AdoptAPet scrapers verified against the Postgres DB. Scraper queue wired and working.

### Step 4: Feature Engineering ← current sprint
Build a `dog_features` table (Feature Store pattern — separate from `dog_profiles` so
features can be recomputed without touching the ingestion pipeline).

Full plan in `notes/feature-engineering-plan.md`. Phases in order:

**Phase 0 — Derive `age_years_approx` (prerequisite for everything else)**
`age_years_approx` is currently 100% null. Must be populated before any encoding work.
- 29 dogs have `birth_date` → exact age: `(today - birth_date).days / 365.25`
- 71 dogs have `age_range_label` → parse midpoint from the range string:

| age_range_label | midpoint |
|---|---|
| less than 1 year | 0.5 |
| 1–3 years | 2.0 |
| 3–8 years | 5.5 |
| 8+ years | 10.0 |

Write result back to `dog_profiles.age_years_approx` (column exists, just null).

**Phase 1 — Ordinal encoding** (`size`, `age_category`, `coat_length` → integers).
Ordinal (not one-hot) because these have a natural order that carries meaning.

**Phase 2 — Three-state boolean normalization** (`good_with_*`, `house_trained`,
`vaccinated`, `spayed_neutered` → `1 = yes, 0 = no, -1 = unknown`).
Null ≠ false. A shelter that didn't fill in `good_with_dogs` is not saying the dog is bad
with dogs — it's an absence of information. Treating null as false penalizes dogs with
incomplete records.

**Phase 3 — Continuous age** (`age_years_imputed` float, no nulls + `age_was_imputed`
boolean flag). Combines Phase 0 derivation with a fallback to `age_category` midpoint as
last resort.

**Phase 4 — Breed group mapping** (`breed_primary` → ~9 AKC-style groups via lookup dict).
Dataset is 58% pit bulls / mixed breeds — grouping must handle these well. Unmatched
breeds fall to `"Unknown"`, never raise an error.

**Phase 5 — Personality traits multi-hot** (top 15–20 traits by frequency → individual
`trait_<name>` BOOLEAN columns). Vocabulary confirmed from audit: 51 unique traits, 85%
of dogs have at least one. Traits that overlap with boolean fields (`Housetrained`,
`Good with Dogs`, etc.) are kept as supplementary confirmation only.

**Phase 6 — Temporal features** (`days_listed`, `log_days_listed`). `listed_at` is 100%
null from PetFinder — fall back entirely to `first_seen_at`.

### Step 5: Design the adopter profile
This is the missing half of the matching equation. The dog side is built. The adopter
side doesn't exist yet. You need to define:

- What preferences does the adopter declare? (size, age, breed, behavior requirements)
- What lifestyle data do you collect? (apartment vs house, kids, other pets, activity level)
- Is this structured input (form fields) or natural language ("I want a calm, small dog")?

The answer determines whether you build a filter engine, a vector similarity search, or both.

### Step 6: Implement the matching algorithm
Only after Steps 1–5:

1. **Phase 1 — Hard filter engine**: SQL WHERE clause on Tier 1 fields. Returns candidate
   set. Fast, transparent, explainable. Many platforms stop here.
2. **Phase 2 — Semantic re-ranking**: Embed `description` + `personality_traits` using
   `sentence-transformers` (all-MiniLM-L6-v2), store 384-dim vectors in pgvector, rank
   candidates by cosine similarity to adopter query embedding.
3. **Phase 3 — Collaborative feedback loop**: If adopters can "like/pass" on dogs, collect
   that signal and train a ranking model on top of the semantic scores.
