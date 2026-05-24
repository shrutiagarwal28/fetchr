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

1. **Add raw JSON storage** — cheapest insurance against scraper bugs
2. **Switch to Postgres** — unblocks everything downstream
3. **Add a second scraper source** — proves the multi-source abstraction works before investing in the queue
4. **Add Celery + Redis** — only after the multi-source pattern is solid
5. **Add the features table** — when the ML side defines what features it needs