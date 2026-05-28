# fetchr

Scrapes dog adoption listings from PetFinder into a local SQLite database.

## Setup

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the Chromium browser Playwright will drive
playwright install chromium

# 3. Configure environment
cp .env.example .env
# Edit .env if you want a different DB path or user agent
```

## Usage

```bash
# Scrape up to 100 dogs from PetFinder (default)
python main.py scrape --source petfinder

# Scrape a smaller batch for testing
python main.py scrape --source petfinder --max 10

# Avoid bot detection
python3 main.py scrape --source petfinder --max 1000 --no-headless

# Adopt-a-Pet (stub — logs a warning, not yet implemented)
python main.py scrape --source adoptapet

# Run all sources
python main.py scrape --source all --max 200
```

## Database

Results are stored in `fetchr.db` (SQLite). Inspect with:

```bash
sqlite3 fetchr.db "SELECT name, breed_primary, city, status FROM dog_profiles LIMIT 10;"
sqlite3 fetchr.db "SELECT COUNT(*) FROM dog_profiles;"
```

Re-running the scraper will **update** existing rows (`last_updated_at`) rather than creating duplicates. Deduplication key: `(source, source_id)`.

---

## Project Structure

```
fetchr/
├── config.py                   ← env vars (single config source of truth)
├── main.py                     ← CLI entrypoint + argparse
├── models/
│   └── dog.py                  ← Pydantic schema + SQLAlchemy ORM models
├── db/
│   └── connection.py           ← DB engine, upsert logic, JSON export
├── scrapers/
│   ├── base.py                 ← shared browser lifecycle (Playwright)
│   ├── petfinder.py            ← live scraper implementation
│   └── adoptapet.py            ← stub (not implemented yet)
└── tests/
    └── test_build_start_url.py ← manual smoke test (no framework)
```

## Dependency Graph

```
main.py
  ├── config.py
  ├── scrapers/petfinder.py
  │     ├── scrapers/base.py
  │     │     ├── config.py
  │     │     └── db/connection.py  ← create_tables()
  │     ├── db/connection.py        ← Session, save_raw_scrape, upsert_dog
  │     └── models/dog.py           ← DogProfile
  ├── scrapers/adoptapet.py
  │     └── scrapers/base.py
  └── db/connection.py              ← export_to_json()

db/connection.py
  ├── config.py
  └── models/dog.py                 ← Base, DogORM, DogProfile, DogProfileHistory, RawScrape
```

`config.py` and `models/dog.py` are the leaves — they import nothing from this project. Everything else depends on them.

## Data Pipeline

```
python3 main.py scrape --source petfinder --max 100
         │
         ▼
  _run_scrape()  [main.py]
         │
         ▼
  PetFinderScraper.run()  [base.py]
    │  — launches Playwright browser
    │  — calls create_tables() so DB exists before any write
         │
         ▼
  PetFinderScraper._scrape()  [petfinder.py]
    │  — builds listing URL from location slug
    │  — paginates through listing cards, collects detail URLs
    │  — for each URL:
    │       navigate → extract __NEXT_DATA__ JSON → normalize → DogProfile
    │       save_raw_scrape()   ← raw blob saved before normalization
    │       upsert_dog()        ← dedup, diff, archive if changed
         │
         ▼
  export_to_json()  [connection.py]
    — snapshots all dog_profiles rows to fetchr.json
```

## The Four Tables

| Table | Purpose | Append-only? |
|---|---|---|
| `dog_profiles` | One row per dog per source (dedup key: source + source_id) | No — updated in place |
| `dog_profile_history` | Snapshot of live fields before each update | Yes |
| `raw_scrapes` | Raw `__NEXT_DATA__` JSON before any normalization | Yes |

## Key Architectural Decisions

**1. `__NEXT_DATA__` over CSS selectors.**
PetFinder is a Next.js app that embeds all page data in a `<script id="__NEXT_DATA__">` tag as JSON. The scraper reads that JSON directly instead of using CSS selectors, because PetFinder's class names change on every frontend deploy while the JSON schema is stable.

**2. Stable vs. live fields.**
`name`, `breed`, `age`, `gender` are never overwritten on re-scrape — they're treated as stable. Only `status`, `photos`, `description`, `tags`, `location`, and behavior flags are re-diffed each run. Manual corrections to the DB survive re-runs.

**3. Sync Playwright, not async.**
Deliberate — this is a single-threaded CLI tool. Sequential page visits are intentional for anti-bot-detection (randomized 2–5s delays between requests). Async would add complexity with no throughput benefit.

**4. Deferred scraper imports in `main.py`.**
The scraper classes are imported inside `_run_scrape()`, not at the top of the file. This prevents Playwright from initializing at import time, which matters if you're importing `main.py` in tests or tools.

**5. Raw scrape failure is intentionally swallowed.**
If `save_raw_scrape()` fails, it logs the error and continues — it never blocks the upsert path. The raw table is insurance against scraper bugs, not a hard dependency.