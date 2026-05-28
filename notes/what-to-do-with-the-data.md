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
| `good_with_kids` | Non-negotiable for families with children |
| `good_with_dogs` | Non-negotiable for households with dogs |
| `good_with_cats` | 73% null — treat as soft filter, not hard filter (see audit §9) |
| `house_trained` | Non-negotiable for apartment adopters |
| `special_needs` | Some adopters specifically want / specifically cannot take special needs dogs |
| `spayed_neutered` | Many adopters filter on this |
| `size` | Common hard preference — people know if they want a small vs large dog |

### Tier 2: Soft filters (scored — influence ranking, not elimination)
These contribute to a match score but a null value doesn't disqualify the dog.

| Field | Why |
|---|---|
| `age_category` | Preferences exist but many adopters are flexible; 38% mismatch rate vs DOB — treat as approximate |
| `age_years_approx` | Not yet derived — will be calculated from `birth_date` (29% coverage) or `age_range_label` midpoint |
| `breed_primary` | Some adopters have breed preferences |
| `gender` | Some adopters have preferences, others don't care |
| `coat_length` | Allergy-sensitive adopters care about this; 28% null |
| `color` | Low-weight soft filter; 94% populated, 11 distinct values |
| `vaccinated` | Health signal; 97% populated |
| `weight_min` / `weight_max` | SQL range filter only (`WHERE weight_max <= ?`) — not in ML feature vector alongside `size` (same size tier, different representation) |

### Tier 3: Semantic signal (ML/NLP — fuzzy matching)
These fields are the richest signal but require embedding/NLP to use.

| Field | Why |
|---|---|
| `description` | 100% coverage, median 980 chars — best signal for fuzzy matching ("I want a calm apartment dog") |
| `personality_traits` | Free-text tags from shelter — "Crate Trained", "Calm Companion", "Good with Cats" |

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
PetFinder never sends these. Stored in schema for future use but must not be used as filters:

| Field | Coverage | Was planned as |
|---|---|---|
| `activity_level` | 0% | Tier 2 soft filter |
| `requires_fenced_yard` | 0% | Tier 1 hard filter |
| `knows_basic_commands` | 0% | Tier 2 soft filter |
| `good_with_other_animals` | 7% | Soft filter |

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

### Step 3: Migrate to Postgres ← current sprint
SQLite cannot support what comes next:
- Concurrent writes when the scraper runs in parallel
- `pgvector` extension for semantic embedding similarity search
- PostGIS for geospatial proximity queries (within 50 miles of zip code)
- Proper JSONB indexing for personality_traits and media_records

This is the gating dependency for Steps 4 and 5. The expansion plan already documents
this migration. No ML work should start before Postgres is in place.

### Step 4: Design the adopter profile
This is the missing half of the matching equation. The dog side is built. The adopter
side doesn't exist yet. You need to define:

- What preferences does the adopter declare? (size, age, breed, behavior requirements)
- What lifestyle data do you collect? (apartment vs house, kids, other pets, activity level)
- Is this structured input (form fields) or natural language ("I want a calm, small dog")?

The answer determines whether you build a filter engine, a vector similarity search, or
both.

### Step 5: Implement the matching algorithm
Only after Steps 1–4:

1. **Phase 1 — Hard filter engine**: SQL WHERE clause on Tier 1 fields. Returns candidate
   set. Fast, transparent, explainable. Many platforms stop here.
2. **Phase 2 — Semantic re-ranking**: Embed `description` + `personality_traits` using
   `sentence-transformers` (all-MiniLM-L6-v2), store 384-dim vectors in pgvector, rank
   candidates by cosine similarity to adopter query embedding.
3. **Phase 3 — Collaborative feedback loop**: If adopters can "like/pass" on dogs, collect
   that signal and train a ranking model on top of the semantic scores.