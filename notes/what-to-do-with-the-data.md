# Matching Fields + What To Do Next

## Context

fetchr is a data ingestion layer for a dog-to-adopter matching platform. The scraper is
fully built and the DogProfile model now captures all 109 fields from PetFinder. The DB
is empty — no scrape runs have been done since the schema was expanded. The audit notebook
(audit.ipynb) was run on a 222-dog sample from the *old* 31-field model and is now stale.
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
| `good_with_cats` | Non-negotiable for households with cats |
| `house_trained` | Non-negotiable for apartment adopters |
| `requires_fenced_yard` | Eliminates apartment adopters for high-energy dogs |
| `special_needs` | Some adopters specifically want / specifically cannot take special needs dogs |
| `spayed_neutered` | Many adopters filter on this |
| `size` | Common hard preference — people know if they want a small vs large dog |

### Tier 2: Soft filters (scored — influence ranking, not elimination)
These contribute to a match score but a null value doesn't disqualify the dog.

| Field | Why |
|---|---|
| `age_category` | Preferences exist but many adopters are flexible |
| `age_years_approx` | More precise than category for "under 3 years" type queries |
| `breed_primary` | Some adopters have breed preferences |
| `gender` | Some adopters have preferences, others don't care |
| `activity_level` | "Calm companion" vs "running partner" — high value when populated |
| `coat_length` | Allergy-sensitive adopters care about this |
| `weight_min` / `weight_max` | More precise than size label |
| `knows_basic_commands` | First-time owners prefer trained dogs |
| `vaccinated` | Health signal |

### Tier 3: Semantic signal (ML/NLP — fuzzy matching)
These fields are the richest signal but require embedding/NLP to use.

| Field | Why |
|---|---|
| `description` | 96% coverage in old audit, median 1023 chars — best signal for fuzzy matching ("I want a calm apartment dog") |
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

### Fields NOT needed for matching
Everything else — org operational stats (`org_annual_adoptions`, `org_employee_count`),
PetFinder internal flags (`import_updates_enabled`, `exportApi`), contact details,
transfer history, media record metadata beyond `photos`, `microchip_id`, `internal_notes`.
Stored for completeness, not queried.

---

## Part 2 — What to do next (in order)

### Step 1: Run the scraper and populate the DB
The DB is empty. Nothing downstream can be built without real data.

```bash
source .venv/bin/activate
python3 main.py scrape --source petfinder --max 100 --no-headless
```

Verify:
```bash
sqlite3 fetchr.db "SELECT COUNT(*) FROM dog_profiles;"
sqlite3 fetchr.db "SELECT name, breed_primary, size, good_with_kids, activity_level FROM dog_profiles LIMIT 10;"
```

### Step 2: Update the data audit notebook
The existing `audit.ipynb` was run on the old 31-field model. Re-run it on the new
109-field model to answer:
- Which of the new fields (activity_level, requires_fenced_yard, vaccinated, etc.) are
  actually populated vs null?
- What are the real null rates for good_with_kids, good_with_dogs, good_with_cats now?
- Are personality_traits consistent or freeform noise?
- What does the description length distribution look like?

This audit output directly determines which fields can be trusted as matching signals.

### Step 3: Migrate to Postgres
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