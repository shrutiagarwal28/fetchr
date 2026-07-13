# Personal Notes — fetchr

      Notes saved during development. Use `/save-note` to add entries.

---

## Project Reference

### Call Chain

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

---

### dog_profiles — Complete Field List (as of 2026-05-27)

**Identity:** `id`, `source`, `source_id`, `source_url`, `name`, `animal_type`, `microchip_id`, `internal_notes`, `match_label`, `out_of_town`, `import_updates_enabled`, `import_deletes_enabled`

**Physical:** `breed_primary`, `breed_secondary`, `is_mixed`, `age_category`, `age_years_approx`, `age_label`, `age_range_label`, `size`, `weight_min`, `weight_max`, `weight_range_label`, `gender`, `color`, `color_secondary`, `color_tertiary`, `coat_length`, `declawed`, `species`, `spayed_neutered`, `vaccinated`, `special_needs`, `special_needs_notes`, `birth_date`

**Behavior:** `house_trained`, `activity_level`, `requires_fenced_yard`, `knows_basic_commands`, `behavior_other_animals`, `good_with_kids`, `good_with_dogs`, `good_with_cats`, `good_with_other_animals`, `personality_traits`

**Location:** `location_id`, `location_name`, `location_type`, `location_contact_name`, `location_email`, `location_phone`, `is_appt_only`, `is_map_hidden`, `is_public_location`, `private_address`, `location_street`, `location_street2`, `city`, `state`, `zip`, `country`, `lat`, `lng`

**Organization:** `shelter_name`, `org_id`, `org_type`, `org_custom_url_alias`, `org_website`, `org_social_urls`, `org_mission_statement`, `org_onsite_vet`, `org_supports_rehome`, `org_spay_neuter_policy`, `org_special_services`, `org_adoption_url`, `org_adoption_fee_min`, `org_adoption_fee_max`, `org_annual_adoptions`, `org_annual_intake`, `org_foster_count`, `org_employee_count`, `org_volunteer_count`, `org_display_id`

**Contact:** `contact_id`, `contact_email`, `contact_first_name`, `contact_last_name`, `contact_phone`

**Media:** `photos` (image URLs, quick access), `media_records` (full objects — `animal_id`, `media_id`, `mime_type`, `media_format`, `media_status`, `public_url`, `original_url`, `s3_url`, `s3_uri`, `original_filename`, `position`, `media_url`, `thumbnail_url`, `media_index`)

**Listing content:** `description`, `extended_description`, `petfinder_notes`, `tags`, `petfinder_url`, `sponsor_a_pet_url`

**Adoption / Status:** `status`, `adoption_fee`, `adoption_fee_waived`, `display_adoption_fee`, `adoption_date`, `adoption_status_change_date`, `intake_date`, `intake_type`, `transfer_date`, `transfer_from_org_id`, `listed_at`

**System:** `first_seen_at`, `last_updated_at`, `deleted_at`, `deletion_reason`

Total: 111 fields across 10 categories.

---

## Scraper Architecture

### Why __NEXT_DATA__ instead of CSS selectors

PetFinder is a Next.js app. Every page embeds all its data in a `<script id="__NEXT_DATA__">` JSON blob. We parse that JSON instead of using CSS selectors because PetFinder removes `data-test` attributes regularly — we hit this bug — and CSS selectors break on every frontend redeploy. The JSON data model is stable across deploys.

Data path: `props.pageProps.animal.physical`, `.behavior`, `._location.address`, `._organization`, `._media[]`, `.residency.adoptionStatus`

**Interview angle:** "We chose to read the embedded JSON payload rather than scrape the DOM because the JSON schema is stable across frontend deploys, while CSS selectors are an implementation detail that breaks without warning."

---

### Why separate DogProfile (Pydantic) and DogORM (SQLAlchemy)

The two classes have conflicting responsibilities:

| Concern | Pydantic | SQLAlchemy |
|---|---|---|
| Validate raw scraped strings | Yes | No |
| Generate SQL | No | Yes |
| Works without a DB | Yes | No |
| Serializable to JSON | Yes (`model_dump`) | No |

If merged, you'd either pollute validation logic with DB concerns or lose the ability to validate data before touching the DB. The data flow is: `scraper → DogProfile (validate) → DogORM (persist)` — the Repository Pattern with a validation gateway in front. The handoff happens at `connection.py` where `profile.model_dump()` converts the validated Pydantic object directly into the ORM row.

**Interview angle:** "I separate the validation contract from the persistence contract because they evolve independently — the scraper schema can change without touching the DB schema, and vice versa."

---

### Pagination: Iterator / Cursor pattern

Each loop iteration advances a cursor (`page_num`), fetches a page of results, and terminates on an empty page sentinel. Key decisions:

- `seen: set[str]` is global across pages — if PetFinder shows the same dog on two pages (promoted listings), it's collected once.
- `remaining` shrinks as cards are collected across pages — without this, page 2 would scroll until it found `max_results` cards all by itself.
- `MAX_PAGES = 20` safety cap — a "stop on empty" loop could run forever if there's a bug or PetFinder returns a redirect loop.

**Watch out for:** An error page that happens to contain some dog card markup (e.g. a "suggested dogs" section) won't trigger the empty-page sentinel. Add a `page.url` redirect check if you see unexpected dogs being collected.

**Interview angle:** "How do you handle rate limits across pages?" — `_random_delay()` between pages. "How do you avoid re-scraping the same dog across pages?" — the shared `seen` set at the collection layer, not just the storage layer.

---

## Database Design

### CDC / Temporal data modeling — the history table pattern

`upsert_dog()` implements Change Data Capture using a history table. The main table always holds the latest state; the history table is append-only.

```
Dog scraped → upsert_dog checks if (source, source_id) exists
  If exists, no _LIVE_FIELDS changed → bumps last_updated_at, returns "unchanged"
  If exists, a live field changed → snapshots old values to dog_profile_history, updates main row, returns "updated"
  If new → inserts row, returns "created"
```

To query the full adoption timeline for a dog:

```sql
SELECT archived_at, status FROM dog_profile_history WHERE source_id = 'abc-123'
UNION ALL
SELECT last_updated_at, status FROM dog_profiles WHERE source_id = 'abc-123'
ORDER BY archived_at;
```

**Watch out for:** `_has_live_changes` uses Python equality. For JSONB list columns (photos, tags), list equality is order-sensitive. If PetFinder returns photos in a different order on re-scrape, it triggers a false "changed" and writes a history row. Acceptable for now (extra rows, no data loss) but worth knowing.

---

### Soft deletes vs hard deletes

Never hard-delete rows that have downstream dependents or audit value. Add `deleted_at` (TIMESTAMPTZ) and `deletion_reason` (VARCHAR) to mark rows as retired. Active queries filter with `WHERE deleted_at IS NULL`; the data is never gone.

Benefits:
- Referential integrity stays intact (FK constraints stay valid)
- Full audit trail preserved for ML training and compliance
- Deletions are reversible — `SET deleted_at = NULL` undoes it
- Distinguishes business states: `"erroneous"` vs `"adopted"` vs `"delisted_by_source"` are different things

The `ON DELETE RESTRICT` FK becomes a safety net against accidental hard deletes, not a policy mechanism — the policy lives in `mark_deleted()` in the application layer.

**Interview angle:** "We use soft deletes to separate 'I don't want to show this' from 'this data never existed' — they're different business states and should be modelled differently."

---

### ON DELETE RESTRICT vs CASCADE

`CASCADE` silently deletes child rows when the parent is deleted — convenient but dangerous for audit tables. `RESTRICT` blocks the parent delete entirely if children exist, forcing an explicit decision.

For an append-only history table, always use `RESTRICT`. History rows are facts about the past and should never be auto-deleted. If the business rule is "a dog profile can be retired but its history must survive," `RESTRICT` enforces that at the DB level.

**Interview angle:** "I chose RESTRICT over CASCADE because the child table is an append-only audit trail — losing history silently would be a data integrity bug, not a feature."

---

### FK constraint in the migration layer, not the ORM model

When a constraint is Postgres-specific (e.g. a FK with `ON DELETE RESTRICT`), declare it in the Alembic migration via `op.create_foreign_key()`, not in the ORM model via `ForeignKey()`.

The ORM model describes the shape of the data. The migration layer describes constraints and indexes. Keeping them separate means changing a constraint is a new migration — you never touch the model. All schema evolution is reviewable and reversible through Alembic.

**Interview angle:** "We manage all schema constraints through Alembic migrations, not ORM declarations. This gives us a single source of truth for schema evolution and makes constraint changes reviewable and reversible."

---

### Postgres idle-in-transaction blocks DDL

**Date:** 2026-05-29

A session that commits but then runs another query (e.g. a COUNT assertion in tests) starts a new implicit transaction. If that session is never closed, the connection sits in Postgres as "idle in transaction" — holding an ACCESS SHARE lock on the queried table. A subsequent `DROP TABLE` needs ACCESS EXCLUSIVE, which conflicts with ACCESS SHARE, causing an indefinite hang.

Fix: close the session before issuing DDL so the connection returns to the pool as idle with no locks held.

**Interview angle:** "We hit a classic idle-in-transaction deadlock in our test teardown. The fix was ensuring sessions are explicitly closed before schema operations — a pattern that matters in any long-lived connection pool environment." Mention `pg_stat_activity` and `idle_in_transaction_session_timeout` as defensive measures in production.

---

## Staff Engineer Mindset

### ML matching: frame the problem before touching the model

The first step when asked to "add ML" is not touching ML — it's problem framing and data audit, in that order.

**Frame the problem first — three questions:**
1. What is the input? (user preferences, lifestyle data, interaction history?)
2. What is the output? (ranked list, binary score, similarity percentage?)
3. What kind of ML problem is it?
   - Filtering + ranking — simplest, often best
   - Vector similarity search — good for fuzzy/semantic matching
   - Collaborative filtering — only relevant if you have user feedback data

**Then audit the data you actually have:**

| Field | Question |
|---|---|
| `breed_primary` | How many unique values? Is "Unknown" common? |
| `age_category`, `size`, `gender` | Are the enums consistent or dirty? |
| `good_with_kids`, `good_with_dogs`, `good_with_cats` | How many are null vs true/false? Nulls are not the same as false. |
| `tags` | Vocabulary size? Normalized or freeform strings? |
| `description` | How many dogs have one? Average length? This is the richest signal for semantic search. |

Run this in a notebook — pandas `value_counts()` + null counts. The output tells you which fields are usable as features and which are too sparse to trust.

**Why this order matters:** The most common junior mistake is jumping to model selection before knowing if the data supports it. If `good_with_kids` is null for 70% of dogs, it's noise, not a feature. The audit also tells you whether you need ML at all — a SQL query with boolean filters may return a ranked list in milliseconds with zero model complexity. ML earns its complexity only when the matching is fuzzy or semantic.

---

### Two-scraper model: why petfinder.py should not blindly revisit every dog

We run two scrapers against PetFinder:
- `petfinder_search.py` — hourly, GraphQL-based, high volume, gets card-level data and breed supply snapshots
- `petfinder.py` — daily, detail-page-based, gets deep fields (`extended_description`, full org bio, full media records)

Once the detail scraper has visited a dog and captured its deep fields, those fields rarely change. Blindly revisiting every dog every day wastes page loads, increases bot-detection risk, and produces no new data.

**The smart trigger:** only visit a detail page when PetFinder itself says the record changed. `petfinder_updated_at` (written by the search scraper from `meta.update.time`) is PetFinder's own backend last-modified timestamp. `detail_scraped_at` (written only by the detail scraper on a successful visit) records when we last did a full detail visit. The comparison is:

```python
should_visit = petfinder_updated_at > detail_scraped_at
```

If `petfinder_updated_at` has advanced past `detail_scraped_at`, PetFinder updated the record after our last detail visit — go fetch it. Otherwise skip.

**Why `detail_scraped_at` and not `last_updated_at`:** `last_updated_at` is a generic "last touched by anything" timestamp — both scrapers bump it. If we used it for this comparison, a search scraper upsert would reset the clock and prevent the detail scraper from knowing whether it had already visited. `detail_scraped_at` is owned exclusively by the detail scraper, making the comparison unambiguous.

**Graceful degradation:** if `petfinder_updated_at` is null (search scraper has never run) or `detail_scraped_at` is null (detail scraper has never visited), always visit. The smart skip only kicks in once both scrapers have run at least once for a given dog.

---

### Adoption status — pipeline state, not a category or Boolean

`status` has 6 canonical values from PetFinder (confirmed via `AllAnimalAttributes` GraphQL response, 2026-06-02). It is a **dropdown field** in the shelter management dashboard — not free text — so these 6 values are exhaustive. The integer IDs being non-sequential (1, 2, 3, 4, 12, 14) indicate some were retired or added over time.

The field represents a **lifecycle stage**, not a simple category:

```
Found/Intake → Adoptable → Pending → Adopted
                   ↕
                 Hold
```

Do not use the raw `status` value as a model feature. Derive from it:

| Derived feature | Definition | Use |
|---|---|---|
| `is_matchable` | `status == 'available'` | Boolean gate — should this dog appear in recommendations at all |
| `is_in_pipeline` | `status in ('available', 'pending', 'hold')` | Dog is active but not yet placed |
| `days_to_adoption` | `adoption_date - listed_at` for adopted dogs | How fast this breed/profile type gets adopted — demand signal per breed |
| `went_pending_count` | count of `available → pending` transitions in `dog_profile_history` | How many people expressed interest — popularity signal |
| `returned_from_pending` | `pending → available` transition exists in history | Application fell through — soft negative signal |
| `is_known_history` | `status != 'found'` at intake | Found/stray dogs have no behavioral history — flag for the model |

**Adopted dogs are ground truth.** Every dog with `status = 'adopted'` represents a real human choosing that dog. That is your supervised training signal for a future ranking model — the only label you have that means "this was a successful match."

**Hold dogs may carry a signal.** A dog that frequently transitions into `hold` may have behavioral or medical issues that are temporarily being managed. Worth tracking as a feature once enough history accumulates.

--------------------------------

### GraphQL exploration artifacts vs seed data

**Category:** Architecture

**Date:** 2026-06-10

`graphql_breed_list.json` and `petfinder_breeds.json` came from two different GraphQL queries and serve completely different purposes. The intercept script fired a `__schema` introspection query to discover what operations PetFinder's API exposes — the result was `graphql_breed_list.json`, a throwaway map of available query field names. Once `allAnimalAttributes` was discovered from that map, it was queried separately to get the actual 309-breed taxonomy, which was saved as `petfinder_breeds.json`. That file is intentional seed data committed to the repo; `scripts/seed_breeds.py` reads it and does a one-time `INSERT ... ON CONFLICT DO NOTHING` into the `petfinder_breeds` table. The exploration artifact (`graphql_breed_list.json`) is ignored in `.gitignore` — it's regenerated every time the intercept script runs and carries no stable value. The distinction to remember: if a file is *input to a script that populates the DB*, it's seed data and belongs in version control; if it's *output of an exploration run*, it's a debug artifact and belongs in `.gitignore`.

--------------------------------

### Three exploration scripts, three angles

**Category:** Architecture

**Date:** 2026-06-10

Before writing the real scrapers, three one-shot investigation scripts were used to understand where PetFinder's data lives:

```
explore_listing_next_data.py   → "What does the search page embed?"
explore_detail_page_props.py   → "What else is on a detail page besides animal?"
explore_graphql_intercept.py   → "What GraphQL calls does the browser make?"
```

All three read `__NEXT_DATA__` or intercept network traffic — none are production code. They're run manually when you need to understand data shape before writing a scraper. `explore_listing_next_data.py` dumps the full `__NEXT_DATA__` blob from the search grid page. `explore_detail_page_props.py` navigates to a single dog's detail page and dumps only `props.pageProps` to find what else lives alongside `props.pageProps.animal`. `explore_graphql_intercept.py` attaches Playwright request/response listeners to capture every GraphQL call the browser makes on page load, then fires a schema introspection query to discover available operations. The outputs of all three are gitignored — they're debug snapshots, not source code.

--------------------------------

### Matching platform — what I described maps onto 3 existing docs

**Category:** Design

**Date:** 2026-07-12

What I described isn't a design-from-scratch problem — it maps onto three docs that already exist:

- **Signup + lifestyle questions + location matches** → `adopter-profile-matching-design.md`
- **The scoring space those questions feed** → `matching-dimension-contract.md`
- **The chatbot / search bar** → `natural-language-search-plan.md`

So I'm not starting from zero. But three docs, three Claude sessions = three chances to drift. That drift is the real risk to watch.
