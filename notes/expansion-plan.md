# Fetchr Expansion Plan

## Context

Fetchr is the data ingestion layer of a larger dog-to-adopter matching platform. It scrapes listings from PetFinder, AdoptaPet, and other sources, normalizes them into a canonical schema, and feeds downstream ML and matching algorithms.

```
[Scrapers]  →  [Ingestion Pipeline]  →  [Storage]  →  [ML / Matching]  →  [Product]
  fetchr            fetchr              Postgres        matching platform
```

Fetchr owns the first two boxes. Everything downstream depends on the quality and freshness of what fetchr produces.

---

## Architecture

### 1. Database: Switch to Postgres

SQLite breaks at commercial scale for three reasons specific to this use case:

- **Concurrent writes**: multiple scrapers running in parallel will deadlock on SQLite
- **ML feature queries**: Postgres has `JSONB` indexing, `pgvector` for embeddings, PostGIS for geospatial radius search (matching dogs within 50 miles of an adopter)
- **The matching platform needs a queryable API** — SQLite is a local file, not a service

### 2. Scraping: Async + Job Queue

The current model is sequential — one dog at a time, one source at a time. At thousands of listings across multiple sites, replace it with:

```
Scheduler (cron / Celery Beat)
    ↓
Job Queue (Celery + Redis  OR  RQ)
    ↓
Worker pool → [PetFinderScraper] [AdoptaPetScraper] [...]
    ↓
Ingestion pipeline (validate → upsert)
```

Each worker scrapes independently. If one site goes down or bot-detects, other workers keep running. Retries come for free.

### 3. Storage: Three-layer schema

Current schema is one layer (normalized dog profiles). At ML scale, use three:

```
raw_scrapes          # exact JSON blob from each site, unmodified
    ↓
dog_profiles         # canonical normalized record (keep what exists)
    ↓
dog_features         # ML-ready numeric/encoded features, derived from dog_profiles
```

**Why keep raw JSON**: scraper bugs will happen. When AdoptaPet changes their schema, raw blobs can be re-processed without re-scraping. This is the most important addition.

**Why a features table**: ML models need numeric vectors, not strings. `"Young"` becomes `0.5`, breed gets one-hot encoded, location becomes `(lat, lng)`. This transformation belongs in a pipeline step, not in the scraper.

### 4. Source normalization: extend what exists

`DogProfile` as the canonical schema is the right pattern. Each new scraper (`AdoptaPet`, `Rescue.ly`, etc.) maps to the same `DogProfile`. The matching platform never needs to know which site a dog came from — it queries `dog_profiles`.

Addition to consider: a **source confidence score** per field. Some sites have unreliable `age` data — the matching algorithm should know that.

### 5. Observability

At commercial scale, scrapers break silently. Needed from day one:
- Scrape job metrics: success rate, records created/updated/skipped per run per source
- Data freshness alerts: "PetFinder hasn't produced new records in 6 hours"
- Schema drift detection: flag when a source's JSON structure changes

---

## What to Keep from the Current Design

| Current decision | Keep? | Why |
|---|---|---|
| `BaseScraper` pattern | Yes | Right abstraction for multi-source |
| `DogProfile` canonical schema | Yes | Contract between fetchr and the ML platform |
| `upsert_dog()` as the only write path | Yes | Critical for dedup at scale |
| `dog_profile_history` audit table | Yes | State transitions (available → adopted) are ML training signals |
| `__NEXT_DATA__` JSON extraction | Yes | More stable than CSS selectors |
| Sequential scraping | No | Replace with async worker pool |
| SQLite | No | Replace with Postgres |

---

## Build Priority

1. ~~**Add raw JSON storage**~~ ✓ Done — `raw_scrapes` table exists in `db/connection.py`
2. ~~**Switch to Postgres**~~ ✓ Done — Alembic-managed schema, JSONB columns, TIMESTAMPTZ, soft deletes, FK constraint
3. ~~**Two-scraper architecture + verification**~~ ✓ Done — explore scraper (GraphQL), detail scraper (queue-based), `urls_to_visit`, `breed_supply_snapshots`, STATUS_MAP fix; 51 dogs in DB
4. **Feature engineering** ← next — `dog_features` table; ordinal encodings, three-state booleans, continuous age, breed groups, personality trait multi-hot, temporal features; wire recompute into scrape pipeline
5. **History-derived features** — `went_pending_count`, `returned_from_pending`, `days_to_adoption`, `is_known_history`; requires scrape history to accumulate before meaningful values
6. **Schema normalization** — extract `organizations` table; move all 20+ org fields out of `dog_profiles`; replace with `org_id` FK
7. **Observability** — scrape job metrics (created/updated/skipped per run per source); data freshness alerts; schema drift detection
8. **Adopter profile + matching algorithm** — design adopter schema; hard filter engine (SQL WHERE on Tier 1 fields); semantic re-ranking (`pgvector` embeddings on `description` + `personality_traits`)
9. **AdoptaPet scraper** — implement `scrapers/adoptapet.py`; prove `DogProfile` canonical schema absorbs a second source cleanly
10. **Celery + Redis job queue** — replace sequential scraping with async worker pool; only after multi-source pattern is solid

---

## Operational To-Dos

- [ ] **Re-run `scripts/seed_breeds.py` periodically** — PetFinder adds breeds occasionally (ID gaps in the current data show this has happened multiple times). When a dog with an unrecognized breed is scraped, `upsert_dog` auto-inserts it into `petfinder_breeds` with a synthetic negative ID so FK integrity is preserved. Re-seeding replaces synthetic rows with PetFinder's official IDs. Check for synthetic rows with: `SELECT * FROM petfinder_breeds WHERE id < 0;`

---

## Schema Normalization To-Dos

- [ ] **Extract `organizations` table** — `dog_profiles` currently has 20+ org fields (`shelter_name`, `org_type`, `org_website`, `org_mission_statement`, `org_onsite_vet`, `org_foster_count`, `org_employee_count`, etc.) that are functionally dependent on `org_id`, not on the dog. Two dogs from the same shelter duplicate all 20 values. Proper fix: create an `organizations` table with `org_id` as PK, move all org fields there, replace them in `dog_profiles` with a single `org_id` FK. `org_animal_id` (the shelter's internal kennel number for a specific dog) stays on `dog_profiles`. Do this as a single dedicated migration — do not split org fields across two tables as a stopgap.

---

## Feature Derivation To-Dos

These features cannot be scraped — they must be derived from data we already have. The `dog_profile_history` table makes all of them possible because it archives every status transition with a timestamp, allowing full timeline reconstruction per dog.

- [ ] **`days_to_adoption`** — for every dog where `status = 'adopted'`, compute `adoption_date - listed_at`. Gives a per-dog adoption speed metric. Aggregate by breed to get a demand signal: breeds that get adopted in 3 days are in high demand; breeds that sit for 90 days are oversupplied.

- [ ] **`went_pending_count`** — for each dog, count the number of `available → pending` transitions in `dog_profile_history`. Each transition means a real human submitted an application. A dog with 4 pending transitions is far more desirable than one with 0, even if both are currently available.

- [ ] **`returned_from_pending`** — Boolean flag: does the dog have a `pending → available` transition in history? This means an application was submitted but fell through. A soft negative signal — worth knowing when ranking.

- [ ] **`is_known_history`** — derived from `intake_type` or `status` at first scrape. Dogs whose first-seen status was `found` are strays with no behavioral history on record. Flag them so the matching model doesn't rely on behavior fields that were never filled in.