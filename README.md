# fetchr

fetchr is the data ingestion layer of a dog-to-adopter matching platform. It scrapes adoption listings from PetFinder using a headless Chromium browser, normalizes them into a canonical schema, and stores them in Postgres. From there, a feature engineering pipeline derives ML-ready signals — age, breed group, compatibility flags, personality traits — into a `dog_features` table that the matching app reads from.

The project is split into two concerns: **fetchr** (this repo) owns everything from raw HTML to clean feature vectors. The **matching app** (separate repo) owns the adopter profile, ranking algorithm, and product layer. The boundary between them is `dog_features`.

```
[PetFinder]  →  [fetchr: scrape → normalize → featurize]  →  [dog_features]  →  [matching app]
```

## Setup

```bash
# 1. Create and activate the virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install the Chromium browser Playwright will drive
python3 -m playwright install chromium

# 4. Configure environment
cp .env.example .env
# Set DATABASE_URL in .env — see Environment Variables section below

# 5. Create the database and apply migrations (first time only)
createdb fetchr
alembic upgrade head
```

## Usage

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Scrape 10 dogs (smoke test)
python3 main.py scrape --source petfinder --max 10

# Use --no-headless if bot detection blocks the headless browser
python3 main.py scrape --source petfinder --max 10 --no-headless

# Inspect results
psql fetchr -c "SELECT name, breed_primary, city, status FROM dog_profiles LIMIT 10;"
psql fetchr -c "SELECT COUNT(*) FROM dog_profiles WHERE deleted_at IS NULL;"
```

## Database

Results are stored in Postgres. Connection string is set in `.env`:

```
DATABASE_URL=postgresql://shruti@localhost:5432/fetchr
```

### TablePlus (GUI)

Connect [TablePlus](https://tableplus.com) with:

| Field | Value |
|---|---|
| Host | 127.0.0.1 |
| Port | 5432 |
| Database | fetchr |
| User | shruti |
| Password | *(leave blank)* |

### Schema migrations

```bash
# Apply all pending migrations (run after pulling)
alembic upgrade head

# Create a new migration after editing models/dog.py
alembic revision --autogenerate -m "describe the change"
```

Re-running the scraper will **update** existing rows rather than creating duplicates. Deduplication key: `(source, source_id)`. Changed live fields are archived to `dog_profile_history` before the row is updated. Stable fields (`name`, `breed`, `age`, `gender`) are never overwritten on re-scrape.

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
│   ├── petfinder.py            ← live scraper (explore + detail, queue-based)
│   └── adoptapet.py            ← stub (not implemented yet)
├── features/                   ← pure, DB-free feature transforms (Feature Store)
│   ├── age.py                  ← age derivation: birth_date / age_range_label → float
│   ├── categorical.py          ← ordinal encodings: size / age_category / coat_type
│   ├── booleans.py             ← three-state encoder: True/False/None → 1/0/-1
│   ├── imputation.py           ← age imputation via per-category medians
│   └── breed.py                ← breed → breed_group (+ AUDIT-ONLY guardrail)
├── models/
│   ├── dog.py                  ← DogProfile (Pydantic) + DogORM (SQLAlchemy)
│   ├── features.py             ← DogFeaturesORM — the dog_features table
│   └── reference.py            ← petfinder_breeds + supply snapshot ORM models
├── data/
│   └── breed_groups.json       ← breed name → group mapping (Phase 4 lookup)
├── alembic/
│   └── versions/               ← migration history
├── scripts/
│   ├── seed_breeds.py          ← seeds petfinder_breeds from AllAnimalAttributes
│   ├── backfill_age_years_approx.py  ← Phase 0 backfill
│   └── backfill_phase{1,2,3,4}.py    ← compute & upsert dog_features rows
├── audit.ipynb                 ← data-quality / feature audit notebook
├── notes/                      ← design docs, expansion plan, feature engineering plan
└── tests/
    └── test_build_start_url.py ← smoke test
```

## Data Pipeline

```
python3 main.py scrape --source petfinder --max 100
         │
         ▼
  _run_scrape()  [main.py]
         │
         ▼
  PetFinderScraper.run()  [base.py]
    — launches Playwright browser
    — calls create_tables() before any write
         │
         ▼
  PetFinderScraper._scrape()  [petfinder.py]
    │
    ├── Explore phase (GraphQL interception)
    │     — intercepts SearchAnimal GraphQL responses in-browser
    │     — enqueues detail URLs into urls_to_visit
    │     — snapshots nationwide breed supply counts to breed_supply_snapshots
    │
    └── Detail phase (queue-based)
          — pops URLs from urls_to_visit one at a time
          — reads __NEXT_DATA__ JSON blob from each detail page
          — normalizes into DogProfile (Pydantic)
          — save_raw_scrape()   ← raw blob saved before normalization
          — upsert_dog()        ← dedup + diff + archive changed fields
                └─► _archive_snapshot()  ← writes dog_profile_history
         │
         ▼
  export_to_json()  [connection.py]
    — snapshots all dog_profiles rows to fetchr.json
```

## Tables

| Table | Purpose | Append-only? |
|---|---|---|
| `dog_profiles` | One canonical row per dog (dedup: `source` + `source_id`) | No — updated in place |
| `dog_features` | ML-ready feature vector, 1:1 with `dog_profiles`. Derived & recomputable — the handoff point to the matching app | No — recomputed by backfill scripts |
| `dog_profile_history` | Snapshot of live fields before each update | Yes |
| `raw_scrapes` | Raw `__NEXT_DATA__` JSON before any normalization | Yes |
| `urls_to_visit` | Scraper work queue — detail page URLs pending a visit | Consumed on read |
| `breed_supply_snapshots` | Nationwide breed supply counts from PetFinder facets, timestamped | Yes |
| `petfinder_breeds` | Canonical breed reference (309 breeds) seeded from PetFinder's vocabulary | Managed |

`dog_features` is populated by the `scripts/backfill_phase*.py` scripts, not by the scraper. The matching app reads `dog_features JOIN dog_profiles` — it never touches `raw_scrapes` or scraper internals.

### ER Diagram for db at june 18th 2026

```mermaid
erDiagram
    dog_profiles {
        varchar id PK
        varchar source
        varchar source_id
        varchar breed_primary
        varchar breed_secondary
        boolean is_mixed
        int breed_canonical_id FK
        varchar age_category
        float age_years_approx
        varchar age_label
        varchar age_range_label
        varchar size
        varchar gender
        varchar color
        varchar coat_length
        boolean spayed_neutered
        boolean vaccinated
        boolean house_trained
        boolean good_with_kids
        boolean good_with_dogs
        boolean good_with_cats
        boolean special_needs
        varchar activity_level
        boolean requires_fenced_yard
        jsonb personality_traits
        jsonb photos
        jsonb tags
        varchar status
        float adoption_fee
        varchar shelter_name
        varchar city
        varchar state
        varchar zip
        float lat
        float lng
        varchar org_id
        text description
        timestamptz birth_date
        timestamptz listed_at
        timestamptz first_seen_at
        timestamptz last_updated_at
        timestamptz deleted_at
        varchar record_status
    }

    dog_features {
        varchar id PK
        varchar dog_profile_id FK
        smallint size_enc
        smallint age_category_enc
        smallint coat_type_enc
        smallint good_with_kids_enc
        smallint good_with_dogs_enc
        smallint good_with_cats_enc
        smallint house_trained_enc
        smallint vaccinated_enc
        smallint spayed_neutered_enc
        float age_years_imputed
        boolean age_was_imputed
        varchar breed_group
        timestamptz computed_at
    }

    dog_profile_history {
        varchar id PK
        varchar dog_profile_id FK
        varchar source
        varchar source_id
        timestamptz archived_at
        varchar status
        jsonb photos
        jsonb tags
        jsonb personality_traits
        text description
        boolean good_with_kids
        boolean good_with_dogs
        boolean good_with_cats
        boolean house_trained
        boolean vaccinated
        varchar city
        varchar state
        float adoption_fee
        timestamptz listed_at
    }

    petfinder_breeds {
        int id PK
        varchar alternate_id
        varchar name
        timestamptz seeded_at
    }

    raw_scrapes {
        varchar id PK
        varchar source
        varchar source_id
        text source_url
        timestamptz scraped_at
        jsonb raw_json
    }

    urls_to_visit {
        varchar id PK
        varchar source
        varchar source_id
        text source_url
        timestamptz queued_at
        varchar reason
    }

    breed_supply_snapshots {
        varchar id PK
        timestamptz snapped_at
        varchar breed_name
        varchar breed_alt_id
        int count
    }

    dog_profiles ||--o{ dog_profile_history : "archived snapshots"
    dog_profiles ||--|| dog_features : "ML features"
    dog_profiles }o--o| petfinder_breeds : "breed_canonical_id"
    breed_supply_snapshots }o--o| petfinder_breeds : "breed_alt_id (soft ref)"
```

`raw_scrapes` and `urls_to_visit` are linked to `dog_profiles` logically by `(source, source_id)` — no enforced FK.

## Feature Engineering

Feature engineering lives in the `features/` package. Features are derived artifacts — computed from `dog_profiles` and stored in `dog_features` (a separate table so they can be recomputed without touching the ingestion pipeline). This is the **Feature Store pattern**.

### Phase 0 — Age derivation (done)

`features/age.py` provides two pure functions:

- `parse_age_range_label(label)` — converts PetFinder's string ranges (`"(1-3 years)"`, `"(less than 1 year)"`) to a float midpoint
- `derive_age_years_approx(birth_date, age_range_label)` — priority: exact DOB calculation → range midpoint → None

Results are written to `dog_profiles.age_years_approx` (Phase 0 lives on `dog_profiles`; Phases 1+ live on `dog_features`). 29% of dogs have a birth date (exact); 71% use the range midpoint.

### Phase status

| Phase | Output (`dog_features` columns) | Status |
|---|---|---|
| 0 | `age_years_approx` on `dog_profiles` (birth_date / range midpoint → float) | ✓ Done |
| 1 | Ordinal encodings: `size_enc`, `age_category_enc`, `coat_type_enc` | ✓ Done |
| 2 | Three-state booleans: `good_with_kids_enc`, `good_with_dogs_enc`, `good_with_cats_enc`, `house_trained_enc`, `vaccinated_enc`, `spayed_neutered_enc` (1 / 0 / -1) | ✓ Done |
| 3 | Continuous age: `age_years_imputed` (float, no nulls) + `age_was_imputed` flag | ✓ Done |
| 4 | Breed group: `breed_group` (9 groups) — **AUDIT-ONLY**, see decision #9 | ✓ Done |
| 5 | Personality traits multi-hot: `trait_affectionate`, `trait_friendly`, etc. (top 15–20 by frequency) | Planned |
| 6 | Temporal: `days_listed`, `log_days_listed` (falls back to `first_seen_at`) | Planned |

The transforms in `features/` are **pure functions** — no DB access, fully unit-testable. The DB read/write is isolated in the `scripts/backfill_phase*.py` runners, which compute each phase's columns and `INSERT ... ON CONFLICT DO UPDATE` into `dog_features`. The scripts are **idempotent and re-runnable**:

```bash
source .venv/bin/activate
python scripts/backfill_phase1.py   # size_enc, age_category_enc, coat_type_enc
python scripts/backfill_phase2.py   # *_enc tristate compatibility/medical flags
python scripts/backfill_phase3.py   # age_years_imputed + age_was_imputed
python scripts/backfill_phase4.py   # breed_group
```

Notable encoding decisions:

- **`coat_type_enc`** reframes PetFinder's `coat_length` (which mixes length and texture) as a *grooming-effort* ordinal: Short=1, Medium/Wire=2, Long/Curly=3.
- **`age_category_enc`** maps `unknown` → `NULL` rather than forcing it onto the 1–4 scale — unknown age is not a life stage.
- **Phase 3** imputes missing ages with the **median age per `age_category`** (global median as fallback), and flags every imputed row via `age_was_imputed` so models can down-weight them.

See `notes/feature-engineering-plan.md` for full vocabulary, encoding decisions, and the audit findings (in `audit.ipynb`) that drove each choice.

## Key Architectural Decisions

**1. `__NEXT_DATA__` over CSS selectors.**
PetFinder embeds all page data in a `<script id="__NEXT_DATA__">` tag as JSON. The scraper reads that JSON directly instead of CSS selectors because PetFinder's class names change on every frontend deploy while the JSON schema is stable.

**2. Explore + detail two-phase scraping.**
The listing page `__NEXT_DATA__` contains almost nothing useful — real data loads dynamically via GraphQL after page load. The explore phase intercepts `SearchAnimal` GraphQL responses inside the browser to collect detail URLs and breed supply facets. The detail phase works off the `urls_to_visit` queue. This separates link discovery from data extraction and makes each phase independently restartable.

**3. Stable vs. live fields.**
`name`, `breed`, `age`, `gender` are never overwritten on re-scrape. Only `status`, `photos`, `description`, `tags`, `location`, and behavior flags are re-diffed each run. Manual corrections to the DB survive re-runs.

**4. Three-state booleans, not nullable booleans.**
Behavior fields (`good_with_dogs`, `house_trained`, etc.) use `1 / 0 / -1` instead of `True / False / None`. A shelter that didn't fill in `good_with_dogs` is not saying the dog is bad with dogs — it's an absence of information. Treating null as false would penalize dogs with incomplete records. The matching engine only hard-excludes dogs that are *known bad* (value = 0); unknowns stay in the candidate pool.

**5. Feature Store pattern: `dog_features` separate from `dog_profiles`.**
Features are derived, recomputable artifacts. Keeping them in a separate table means encoding strategy can change and be recomputed without modifying the ingestion pipeline. The matching app reads `dog_features JOIN dog_profiles` — it never touches `raw_scrapes` or scraper internals.

**6. Breed canonical IDs.**
`dog_profiles.breed_canonical_id` is a FK into `petfinder_breeds` (309 breeds from PetFinder's official vocabulary). When a dog arrives with an unrecognized breed, a synthetic negative ID is auto-inserted to preserve FK integrity. Re-running `scripts/seed_breeds.py` replaces synthetic rows with PetFinder's official IDs.

**7. Sync Playwright, not async.**
Deliberate — this is a single-threaded CLI tool. Sequential page visits are intentional for anti-bot-detection (randomized 2–5s delays between requests). Async would add complexity with no throughput benefit at current scale.

**8. Raw scrape failure is intentionally swallowed.**
If `save_raw_scrape()` fails, it logs the error and continues — it never blocks the upsert path. The raw table is insurance against scraper bugs, not a hard dependency.

**9. `breed_group` is AUDIT-ONLY.**
`dog_features.breed_group` (and the `Pit Bull Type` value in particular) exists only for internal monitoring and disparate-impact audits — e.g. measuring whether pit-type dogs suffer longer time-to-adoption so we can *correct* for that bias. It must never feed an adopter-facing exclusion filter or the matcher's ranking. Breed-based exclusion is the discriminatory pattern that harms these dogs in the real world; replicating it behind an algorithm would launder that bias. Adopter constraints are expressed through *trait* filters (`good_with_cats`, energy level, novice-friendly) that describe the individual dog, not its breed label. `features/breed.py` exposes `AUDIT_ONLY_FEATURES` and `assert_not_audit_only()` as the machine-checkable enforcement point — any future filter/ranking code must call it on its feature inputs so a leak fails loudly.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(required — no default)* | Postgres connection string, e.g. `postgresql://postgres:postgres@localhost:5432/fetchr` |
| `JSON_PATH` | `fetchr.json` | JSON export path (written after every scrape run) |
| `PETFINDER_LOCATION` | `nj/jersey-city` | Search location as `{state}/{city}` — overridden by `--location` CLI arg |
| `USER_AGENT` | Chrome 124 on macOS | Browser UA string |

## Build Progress

| Step | Status |
|---|---|
| Raw JSON storage (`raw_scrapes`) | ✓ Done |
| Migrate to Postgres (Alembic, JSONB, TIMESTAMPTZ, soft deletes, FK) | ✓ Done |
| Two-scraper architecture (explore + detail, queue-based) | ✓ Done |
| STATUS_MAP fix (hold / found / other statuses) | ✓ Done |
| Breed canonical ID FK + `petfinder_breeds` reference table | ✓ Done |
| Feature engineering Phase 0 — `age_years_approx` derivation | ✓ Done |
| Feature engineering Phases 1–4 — `dog_features` (encodings, tristate, age imputation, breed group) | ✓ Done |
| Feature engineering Phases 5–6 — personality multi-hot, temporal features | In progress |
| History-derived features (`went_pending_count`, `days_to_adoption`) | Planned |
| Schema normalization — extract `organizations` table | Planned |
| Observability (scrape job metrics, freshness alerts, schema drift) | Planned |
| Adopter profile + matching algorithm (separate repo) | Planned |
| AdoptaPet scraper | Planned |
| Celery + Redis job queue (async worker pool) | Planned |

## Bot Detection Notes

- `--no-headless` opens a visible browser window — use this if headless is being blocked
- `playwright-stealth` patches navigator flags that identify headless Chrome; install it if missing
- `BaseScraper._random_delay()` adds 2–5s between detail page visits — do not remove this
- All scraping runs sequentially (one page at a time) intentionally

## Operational Notes

- **Re-seed breeds periodically** — PetFinder adds breeds occasionally. Check for synthetic rows with `SELECT * FROM petfinder_breeds WHERE id < 0;` then re-run `scripts/seed_breeds.py` to replace them with official IDs.
- **`good_with_cats` is 73% null** — never use as a hard filter. Only hard-exclude dogs where the value is explicitly 0 (known bad). Surface a UI note to adopters for the rest.
- **`activity_level`, `requires_fenced_yard`, `tags`** — 0% populated by PetFinder. Stored in schema for future use but must not appear in any matching filter or feature vector.
