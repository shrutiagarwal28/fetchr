# GraphQL Findings — Implementation Plan

**Date:** 2026-06-02  
**Based on:** `notes/additional-findings.md`

---

## Overview

Four parts: a bug fix, new columns, new reference tables, and a two-scraper operating model. The biggest architectural change is splitting the current single scraper into two — a fast GraphQL-based search scraper that discovers dogs and queues them, and the existing detail-page scraper that enriches them on demand.

---

## Part 1 — Immediate bug fix: missing adoption statuses

**File:** `scrapers/petfinder.py` — `STATUS_MAP`

PetFinder has 6 canonical statuses; we only handle 3. Dogs on Hold or marked Found are silently falling into `available` right now.

```python
# Current (broken)
STATUS_MAP = {
    "adoptable": "available",
    "pending": "pending",
    "adopted": "adopted",
}

# Fixed
STATUS_MAP = {
    "adoptable": "available",
    "pending": "pending",
    "adopted": "adopted",
    "hold": "hold",
    "found": "found",
    "other": "other",
}
```

The `status` column in `dog_profiles` and `dog_profile_history` is `String(20)` — "hold", "found", "other" all fit. No schema change needed, just the map.

---

## Part 2 — New columns on `dog_profiles`

**Files:** `models/dog.py` (Pydantic + ORM), new Alembic migration

### 2a. Meta timestamps (Finding 1)

These come from the `meta` block on every `SearchAnimal` card response. More reliable than our own `last_updated_at` because they are PetFinder's own backend timestamps.

| Column | Type | Source |
|---|---|---|
| `petfinder_created_at` | `DateTime` nullable | `meta.create.time` |
| `petfinder_updated_at` | `DateTime` nullable | `meta.update.time` |
| `record_status` | `String(50)` nullable | `meta.recordStatus` e.g. "published" |

### 2b. Shelter's internal kennel ID (Finding 2) — `org_animal_id` only

`org_animal_id` is per-dog (the shelter's internal kennel number for this specific dog) and belongs on `dog_profiles`.

| Column | Type | Source |
|---|---|---|
| `org_animal_id` | `String(100)` nullable | `organization.organizationAnimalId` e.g. "SSRD-A-2483" |

The remaining org card fields (`org_contact_name`, `org_contact_id`, `org_location_name`, `org_city`, `org_state`) are org-level data, not dog-level data. Adding them to `dog_profiles` would deepen the existing 2NF violation — 20 org fields already duplicated across every dog from the same shelter.

**Deferred:** These fields, along with all 20 existing org columns in `dog_profiles`, will be moved to a dedicated `organizations` table in a future normalization refactor. That refactor replaces all org columns in `dog_profiles` with a single `org_id` FK. See `expansion-plan.md` — Schema Normalization To-Dos.

---

## Part 3 — Two new reference tables

### 3a. `petfinder_breeds` — canonical breed taxonomy

A static reference table seeded once from `petfinder_breeds.json`. Used for validation (flag any scraped breed not in this list — it signals a PetFinder schema change) and for matching UI dropdowns.

```
petfinder_breeds
  id            Integer PK  (PetFinder's own integer ID)
  alternate_id  String(100) (URL slug used in SearchAnimal filter variables)
  name          String(255) (display name)
  seeded_at     DateTime
```

One-time seed script: `scripts/seed_breeds.py` — reads `petfinder_breeds.json`, inserts all 309 rows.

### 3b. `breed_supply_snapshots` — real-time supply counts over time (Finding 4)

The `SearchAnimal` facets return live counts of available dogs per breed across all of PetFinder. Storing these periodically builds a time series — which breeds are oversupplied, which are rare — directly useful for the matching platform's ranking logic.

```
breed_supply_snapshots
  id              String(36) PK UUID
  snapped_at      DateTime   (when this snapshot was taken)
  breed_name      String(255)
  breed_alt_id    String(100)
  count           Integer    (available dogs of this breed on PetFinder at snapshot time)
```

Indexes:
- `snapped_at` — for time-range queries
- `(breed_alt_id, snapped_at)` — for per-breed trend queries

A single `SearchAnimal` call with `animalType: "Dog"` and `facets: {breeds: true, age: true}` returns counts for all 309 breeds in one response.

**This is not part of the regular scraper.** The scraper is dog-centric — it visits detail pages and writes to `dog_profiles`. The supply snapshot is a separate, independent operation:

- **Separate file:** `scrapers/petfinder_supply_snapshot.py` — inherits `BaseScraper` for the Playwright/Akamai browser lifecycle, but `_scrape()` fires a single `page.evaluate()` GraphQL call and batch-inserts the facet counts. No detail page visits, no pagination.
- **Separate CLI command:** `python3 main.py snapshot --source petfinder` — independent of `python3 main.py scrape`.
- **Separate schedule:** Run once daily on its own cron, not tied to scrape runs.

---

## Part 4 — Two-scraper operating model

### Roles

| Scraper | File | Frequency | Job |
|---|---|---|---|
| Explore scraper | `scrapers/petfinder_explore.py` | Hourly | Discovers dogs via GraphQL, writes card-level fields, populates `urls_to_visit`, captures breed supply facets |
| Detail scraper | `scrapers/petfinder.py` | Daily | Reads `urls_to_visit`, enriches dogs with deep fields — only visits pages the search scraper has queued |

### New table: `urls_to_visit`

The search scraper already has the full list of discovered/updated dog URLs in memory as it processes each `SearchAnimal` page. It writes them directly into this table. The detail scraper reads from it. They communicate through the DB, not through shared in-memory state.

```
urls_to_visit
  id          String(36) PK UUID
  source      String(50)        e.g. 'petfinder'
  source_id   String(255)       the dog's UUID
  source_url  Text              the detail page URL
  queued_at   DateTime          when the search scraper added this
  reason      String(50)        'new' | 'updated'
```

Search scraper writes a row when:
- It finds a dog not yet in `dog_profiles` — reason: `new`
- `petfinder_updated_at` has advanced for an existing dog — reason: `updated`

Detail scraper:
- Reads from `urls_to_visit` ordered by `queued_at`, limited by `--max`
- Visits each `source_url` via Playwright, parses `__NEXT_DATA__`
- On success: deletes the row from `urls_to_visit`, stamps `detail_scraped_at` on the `dog_profiles` row
- On failure: leaves the row — automatically retried on next run

The `reason` column provides observability: we can see at a glance whether the detail scraper is handling mostly new dogs or re-enriching updated ones.

### New column: `detail_scraped_at`

Add `detail_scraped_at` (`DateTime`, nullable) to `dog_profiles`. Set only by the detail scraper on a successful page visit. Never touched by the search scraper. Used to know whether a dog has ever had its deep fields populated.

### Search scraper responsibilities

- One browser session load to pass Akamai, then all GraphQL calls fire via `page.evaluate()` from inside it
- Paginated `SearchAnimal` calls — 12 dogs per call, up to `--max`
- Writes card-level fields (`org_animal_id`, `petfinder_created_at`, `petfinder_updated_at`, `record_status`, status, photos) to `dog_profiles` via `upsert_dog()`
- Populates `urls_to_visit` for new and updated dogs
- Writes breed + age facet counts to `breed_supply_snapshots` once per run (facets come back on every `SearchAnimal` call as a side effect)
- CLI: `python3 main.py explore --source petfinder --max 1000`

### Detail scraper responsibilities

- No listing page crawling — that is now the search scraper's job
- Reads URL queue from `urls_to_visit`
- Visits each detail page via Playwright, parses `__NEXT_DATA__`
- Writes deep fields to `dog_profiles` via `upsert_dog()`
- Stamps `detail_scraped_at` and deletes the `urls_to_visit` row on success
- CLI: `python3 main.py scrape --source petfinder --max 100` (interface unchanged)

### Graceful degradation

- If the search scraper has never run, `urls_to_visit` is empty and the detail scraper is a no-op — correct behaviour.
- If a detail page visit fails, the `urls_to_visit` row is not deleted and the dog is retried on the next run automatically.

---

## Execution order

| Step | What | Files touched |
|---|---|---|
| 1 | Fix `STATUS_MAP` — add hold/found/other | `scrapers/petfinder.py` |
| 2 | Add 5 new fields to `DogProfile` Pydantic model: `petfinder_created_at`, `petfinder_updated_at`, `record_status`, `org_animal_id`, `detail_scraped_at` | `models/dog.py` |
| 3 | Add same 5 fields to `DogORM` | `models/dog.py` |
| 4 | Write Alembic migration — 5 new columns on `dog_profiles`, create `petfinder_breeds`, `breed_supply_snapshots`, and `urls_to_visit` tables | `alembic/versions/` |
| 5 | Add `BreedSupplySnapshotORM`, `PetFinderBreedORM`, `UrlToVisitORM` models | new `models/reference.py` |
| 6 | Write `scripts/seed_breeds.py` — populate `petfinder_breeds` from `petfinder_breeds.json` | new `scripts/` dir |
| 7 | Build `scrapers/petfinder_explore.py` — paginated `SearchAnimal` GraphQL scraper, writes to `dog_profiles`, `urls_to_visit`, `breed_supply_snapshots` | new file |
| 8 | Update `scrapers/petfinder.py` — remove listing page crawling, replace with `urls_to_visit` queue read | `scrapers/petfinder.py` |
| 9 | Add `search` command to `main.py` CLI | `main.py` |
| — | *(deferred)* Extract `organizations` table, move all 20+ org fields out of `dog_profiles`, add `org_id` FK | `models/`, `alembic/`, `db/connection.py` |
