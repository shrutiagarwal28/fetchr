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

# Avoid bot detection. 
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

## Project Structure

```
fetchr/
├── scrapers/
│   ├── base.py         # Shared browser setup + stealth
│   ├── petfinder.py    # PetFinder scraper (full implementation)
│   └── adoptapet.py    # Adopt-a-Pet stub (TODO)
├── models/
│   └── dog.py          # Pydantic DogProfile + SQLAlchemy DogORM
├── db/
│   └── connection.py   # Engine, session, upsert_dog()
├── config.py           # Env var loading
├── main.py             # CLI entry point
├── requirements.txt
└── .env.example
```

## Notes

- CSS selectors for PetFinder are defined as constants at the top of
  `scrapers/petfinder.py`. If the site redesigns, that's the only place
  to update.
- The scraper runs sequentially (one page at a time) to avoid detection.
- Delays of 2–5 seconds are added between detail page visits.
