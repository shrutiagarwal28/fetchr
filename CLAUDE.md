# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

fetchr is a CLI scraper that pulls dog adoption listings from PetFinder into a local Postgres database. It uses a headless Chromium browser (Playwright) because PetFinder renders via JavaScript.

## Running the scraper

```bash
# Activate the virtual environment first
source .venv/bin/activate

# Scrape 10 dogs (smoke test)
python3 main.py scrape --source petfinder --max 10

# Use --no-headless if bot detection blocks the headless browser
python3 main.py scrape --source petfinder --max 10 --no-headless

# Inspect results
psql fetchr -c "SELECT name, breed_primary, city, status FROM dog_profiles LIMIT 10;"
```

## Setup (first time)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
cp .env.example .env
```

## Architecture

The data pipeline is **Extract → Validate → Upsert**:

1. `scrapers/petfinder.py` navigates Playwright to the listing page, scrolls to load all cards, collects detail page URLs, then visits each one.
2. On each detail page it reads the `<script id="__NEXT_DATA__">` JSON blob that Next.js embeds — **not CSS selectors** — to extract all dog fields. This is the critical architectural decision: PetFinder removes `data-test` attributes regularly, but the JSON structure is stable.
3. Raw data is normalized into a `DogProfile` Pydantic model (`models/dog.py`) which validates types and fills defaults.
4. `db/connection.py` has two write paths: `save_raw_scrape()` saves the unmodified `__NEXT_DATA__` animal blob to `raw_scrapes` before any normalization; `upsert_dog()` writes to `dog_profiles`. Dedup key is `(source, source_id)`. On re-run, if live fields (status, photos, behavior, location) changed, the old values are archived to `dog_profile_history` before the main row is updated. Stable fields (name, breed, age, gender) are never overwritten.

## Call chain

`connection.py` is the DB layer — the scraper calls into it, not the other way around.

```
python3 main.py scrape ...
    └─► main.py::_run_scrape()
            └─► PetFinderScraper.run()              # base.py — browser setup
                    └─► PetFinderScraper._scrape()        # petfinder.py — scraping loop
                            ├─► save_raw_scrape()          # connection.py — writes raw_scrapes
                            └─► upsert_dog()               # connection.py — writes dog_profiles
                                    └─► _archive_snapshot()    # connection.py — writes dog_profile_history
```

## Key data path in `__NEXT_DATA__`

```
props.pageProps.animal
  .physical.breed.{primary, secondary, mixed}
  .physical.age.value          # "Young" | "Adult" | "Baby" | "Senior"
  .physical.size.label         # "Small" | "Medium" | "Large" | "Extra Large"
  .physical.sex
  .physical.color.primary
  .behavior.houseTrained       # "Yes" | "No" | "Unknown"
  .behavior.interactions.{dogs, cats, childrenUnder8, children8AndUp}
  .behavior.personalityTraits  # list of tag strings
  ._location.address.{city, state, postalCode}
  ._organization.organizationName
  ._media[].{publicUrl, mimeType}
  .residency.adoptionStatus    # "Adoptable" | "Pending" | "Adopted"
```

## Adding a new scraper source

1. Create `scrapers/yoursite.py` inheriting `BaseScraper`
2. Implement `_scrape(page)` — follow `petfinder.py` as the reference
3. Set `SOURCE_NAME = "yoursite"` — this is the dedup key stored in the DB
4. Register it in `main.py::_run_scrape()` dispatch dict

## Bot detection notes

- `--no-headless` opens a visible browser window — use this to test if headless is being blocked
- `playwright-stealth` patches navigator flags that identify headless Chrome; install it if missing
- `BaseScraper._random_delay()` adds 2–5s between detail page visits — do not remove this
- All scraping runs sequentially (one page at a time) intentionally

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(required — no default)* | Postgres connection string, e.g. `postgresql://postgres:postgres@localhost:5432/fetchr` |
| `JSON_PATH` | `fetchr.json` | JSON export path (written after every scrape run) |
| `PETFINDER_LOCATION` | `nj/jersey-city` | Search location as `{state}/{city}` — overridden by `--location` CLI arg |
| `USER_AGENT` | Chrome 124 on macOS | Browser UA string |
