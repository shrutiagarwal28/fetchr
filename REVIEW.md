# fetchr Code Review Checklist

A project-specific review guide. Built from the actual architecture, not generic best practices.

---

## Severity Key

| Symbol | Severity | Meaning |
|--------|----------|---------|
| 🔴 RED | Merge blocker | Breaks the pipeline, corrupts data, or violates a core architectural rule. Fix before merge. |
| 🟡 YELLOW | Worth noting | Won't break anything today but creates risk or debt. Okay to defer with a note. |
| 🟣 PURPLE | Skip | Hygiene that doesn't matter on a side project at this scale. Don't spend time on it. |

---

## Merge Blockers 🔴

These are derived from the three architectural rules the codebase was explicitly built around: `__NEXT_DATA__` over CSS selectors, all DB writes flow through Pydantic, and the upsert/archive contract is never violated.

### Data Pipeline Integrity

- [ ] **Pydantic is not bypassed.** Raw scraped data must pass through `DogProfile` before touching the DB. A `DogORM(**raw_dict)` call that skips Pydantic is a blocker — missing fields, wrong types, and no defaults.
- [ ] **`save_raw_scrape()` is still called before normalization.** This is the recovery insurance: if a scraper bug mis-maps a field, the raw blob lets you replay without re-scraping. Removing or moving it after normalization defeats the purpose.
- [ ] **Stable fields are not written in `upsert_dog()`.** `name`, `breed_primary`, `breed_secondary`, `age_category`, `gender` must never be overwritten on an existing row. Manual DB corrections need to survive re-runs. Check `_LIVE_FIELDS` hasn't grown to include these.
- [ ] **New live fields are added to `_LIVE_FIELDS`.** If a new field can change between scrape runs (status, behavior, location, photos, fee), it must be in `_LIVE_FIELDS` in `connection.py` or changes will silently skip archiving to `dog_profile_history`. Check both `_LIVE_FIELDS` (used by `upsert_dog`) and `_CARD_LIVE_FIELDS` (used by `upsert_card`).
- [ ] **New Pydantic fields have a matching Alembic migration.** A field in `DogProfile`/`DogORM` with no migration means the column doesn't exist in Postgres. The scraper will either silently drop data or crash on insert. Check `alembic/versions/` for a new file dated near the PR.

### Scraper Architecture

- [ ] **`__NEXT_DATA__` is used, not CSS selectors.** PetFinder removes `data-test` attributes regularly. Any `page.query_selector()` or `page.locator()` call that extracts dog *data fields* (not card URLs) is a reliability risk. Card URL collection via CSS is expected — data extraction is not.
- [ ] **`_random_delay()` is not removed or made conditional.** Sequential scraping with delays is an intentional bot-detection strategy. Parallelizing requests or skipping the delay between detail page visits will get the IP flagged. The comment in `base.py` exists for exactly this reason.
- [ ] **A new scraper sets `SOURCE_NAME` as a class attribute.** `SOURCE_NAME` is the dedup key stored in the DB as `source`. Two scrapers with the same `SOURCE_NAME` will silently overwrite each other's records. A missing `SOURCE_NAME` (`""`) will corrupt the constraint.
- [ ] **A new scraper is registered in `main.py`'s dispatch dict.** If it's not in `scrapers = {...}` in `_run_scrape()`, it can never be invoked from the CLI. Easy to forget.

### DB Safety

- [ ] **No raw string interpolation in queries.** All DB writes go through SQLAlchemy's ORM or parameterized `session.execute()`. `f"... WHERE source = '{source}'"` is a SQL injection vector even in an internal tool.
- [ ] **`DATABASE_URL` is not hardcoded.** Must come from the environment via `config.py`. A hardcoded connection string in any file is a blocker.
- [ ] **Sessions are closed before `drop_all()` in tests.** See `errors.md` (2026-05-29): an open session after `session.commit()` holds an `ACCESS SHARE` lock that blocks `DROP TABLE`. Tests must call `session.close()` before recreating tables or they'll hang.

---

## Worth Reviewing, Okay to Defer 🟡

- [ ] **Type hints on new functions.** Especially return types on scraper helpers and connection functions. The codebase uses type hints consistently — gaps show up fast at call sites.
- [ ] **Exceptions are logged, not swallowed silently.** `except Exception: pass` is never okay here. At minimum `logger.exception(...)` + rollback. Check new `try/except` blocks.
- [ ] **`__NEXT_DATA__` path documented in CLAUDE.md if a new field is extracted.** The key data path section is a living reference. If a new field comes from a nested path that wasn't there before, add it.
- [ ] **New DB columns have indexes if they'll appear in `filter_by()` calls.** `source` + `source_id` already have `uq_source_source_id`. A new column used in a `WHERE` clause without an index won't hurt today but will at 100k rows.
- [ ] **`upsert_card()` and `upsert_dog()` stay in sync conceptually.** `_CARD_LIVE_FIELDS` is intentionally a subset of `_LIVE_FIELDS`. If a field is added to one, think about whether it belongs in both or just one.

---

## Skip Entirely on This Project 🟣

These are legitimate engineering concerns that simply don't pay off at this scale and maturity.

- **100% test coverage.** The two existing test files cover the critical upsert contract. Don't block a PR because a new helper has no unit test.
- **Docstrings on every function.** The codebase already has strong naming + inline reasoning comments where it matters. Multi-paragraph docstrings on obvious functions are noise.
- **`export_to_json()` performance.** It loads all rows into memory and serializes. That's fine until ~50k records. Don't optimize it yet.
- **Logging verbosity debates.** `INFO` on the scrape loop, `DEBUG` for scroll internals. The split is already sensible. Don't bikeshed log levels.
- **Formatting/linting nits.** Line length, trailing whitespace, import order. Not worth review time.
- **Dead code in `scrapers/`.** `petfinder_explore.py`, `explore_*.py` are exploratory scripts. Ignore unless they're actively breaking something.

---

## 5-Minute Review Process

Run through these in order. Each step has a clear stop condition so you don't overread.

**Step 1 — Orient (30s)**
```
git diff main...HEAD --stat
```
Which files changed? The risk profile is completely different depending on the answer:
- Only `scrapers/` changed → focus on `__NEXT_DATA__` usage and delay logic
- Only `models/` changed → focus on migration + `_LIVE_FIELDS` sync
- Only `db/connection.py` changed → focus on upsert contract and field lists
- Only `tests/` changed → focus on session cleanup (the lock bug)

**Step 2 — Migration check (30s, only if `models/dog.py` changed)**

Does every new field in `DogORM` have a corresponding `add_column` in a new file under `alembic/versions/`? If no migration exists, stop — it's a blocker.

**Step 3 — Scraper check (60s, only if `scrapers/` changed)**

Search the diff for `page.query_selector` or `page.locator`. If those appear inside `_scrape_detail_page` (not in `_collect_cards_on_page`), ask why `__NEXT_DATA__` wasn't used. Then search for `_random_delay` to confirm it's still called in the detail page loop.

**Step 4 — Upsert contract (60s, only if `connection.py` changed)**

Read `_LIVE_FIELDS` and `_CARD_LIVE_FIELDS`. Ask two questions:
1. Is any stable field (`name`, `breed_primary`, `age_category`, `gender`) in either list? If yes, blocker.
2. Is any new "live" field (one that can change) missing from both lists? If yes, yellow flag.

**Step 5 — New scraper checklist (30s, only if a new file appeared in `scrapers/`)**

- `SOURCE_NAME` is set and unique
- Inherits `BaseScraper`
- `_random_delay()` called in the detail page loop
- Registered in `main.py` dispatch dict

**Step 6 — Security spot-check (30s, always)**

```
git diff main...HEAD | grep -E "(DATABASE_URL|password|secret|f\"|%s)" | grep -v "\.env"
```
Flag any hardcoded credential strings or f-string / `%s` usage inside `session.execute()` calls.

**Step 7 — Done.** If none of the blockers triggered, merge. Note any yellow flags in the PR comment.

---

## Quick Reference: Key Files and Their Rules

| File | Rule |
|------|------|
| `models/dog.py` | Every new field needs an Alembic migration |
| `db/connection.py` | Stable fields never in `_LIVE_FIELDS`; new live fields must be added to it |
| `scrapers/petfinder.py` | `__NEXT_DATA__` for data, CSS only for card URL collection |
| `scrapers/base.py` | `_random_delay()` is sacred; sync Playwright is intentional |
| `scrapers/*.py` (new) | `SOURCE_NAME` required; must be in `main.py` dispatch |
| `tests/test_upsert_dog.py` | `session.close()` before `drop_all()` — see errors.md |
| `alembic/versions/` | Date-slug filenames (`YYYYMMDD_description.py`) |
