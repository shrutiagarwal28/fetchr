# Plan: Natural-language dog search UI

## Context

fetchr currently ingests dogs into Postgres but has no way to *query* them in
plain language. The goal: a UI where you type something like
`"a calm small dog that's good with cats and already house trained"` and get
matching dogs back as cards.

The hard part is turning free text into a safe DB query. We are **not** letting
the LLM write SQL. Instead:

```
plain text ──► Claude (structured output) ──► DogQuery (Pydantic) ──► SQLAlchemy ORM query ──► cards
              parser.py                       schema.py               query.py                app.py
```

`DogQuery` is an **anti-corruption layer**: Claude can only fill a fixed,
validated set of fields. The query builder turns those fields into
parameterized ORM filters. This means a malicious/garbled query can at worst
produce *wrong filters* — never SQL injection, never a raw SQL string.

Decisions already made with the user: **Claude parses the text** (not keyword
rules), and the **UI is Streamlit**.

## Data reality (verified against the live DB, 225 live dogs)

- Clean enums: `size` {small, medium, large, xlarge}, `age_category`
  {puppy, young, adult, senior}, `gender` {male, female}, `status`
  {available, adopted}.
- **`activity_level` is entirely NULL** → temperament ("calm", "energetic")
  must map to `personality_traits` (JSONB list: "Couch Potato", "Athletic",
  "Playful", "Quiet", "Gentle", ...), **not** to `activity_level`.
- `good_with_kids/dogs/cats`, `house_trained` are **tristate** (true / false /
  NULL=unknown). NULL means untested, not "no" — see `models/features.py`
  Phase 2 notes. Coverage is partial (e.g. good_with_cats known for 81/225).
- All 225 dogs have photos → cards can always show an image.
- `coat_length` {Short, Medium, Long, Curly, Wire, NULL}; `state` is populated
  for most rows (NJ, NY, CA, ...).

## New files

### `search/__init__.py`
Empty package marker (matches `features/`, `scrapers/` convention).

### `search/schema.py` — the LLM↔DB contract
A Pydantic `DogQuery` model, every field `Optional`/defaulted (absent = "user
didn't constrain this"). Use `Literal`/enums for constrained fields so Claude
cannot emit invalid values and Pydantic rejects anything off-vocab:

- `sizes: list[Literal["small","medium","large","xlarge"]] = []`
- `age_categories: list[Literal["puppy","young","adult","senior"]] = []`
- `gender: Optional[Literal["male","female"]] = None`
- `good_with_kids/good_with_dogs/good_with_cats: Optional[bool] = None`
- `house_trained: Optional[bool] = None`
- `special_needs: Optional[bool] = None`
- `breed_contains: Optional[str] = None`  (matched with ILIKE)
- `personality_keywords: list[str] = []`  (temperament → matched vs `personality_traits`)
- `state: Optional[str] = None`, `city: Optional[str] = None`
- `max_adoption_fee: Optional[int] = None`

Keep it a plain `BaseModel` so `client.messages.parse(output_format=DogQuery)`
validates the response automatically. (Note: structured-output JSON Schema
disallows `minLength`/numeric constraints; the SDK strips unsupported keywords,
so don't add `Field(min_length=...)` constraints expecting them server-side.)

### `search/parser.py` — text → `DogQuery`
`parse_query(text: str) -> DogQuery`, fully type-hinted, no `print` (uses
`logging`):
- **Validate input first** (security/cost): reject empty; cap length (e.g.
  500 chars) before sending to the API.
- `client = anthropic.Anthropic()` — resolves `ANTHROPIC_API_KEY` from env, no
  hardcoded key.
- `client.messages.parse(model=..., max_tokens=1024, system=SYSTEM_PROMPT,
  messages=[{"role":"user","content":text}], output_format=DogQuery)` →
  `response.parsed_output`.
- **Model:** default `claude-opus-4-8` (per Anthropic guidance; one-line config
  swap to `claude-haiku-4-5` if cost/latency matters — this is a cheap
  extraction task, so Haiku is a reasonable later switch). Make it a `config.py`
  value `SEARCH_MODEL` so it's not hardcoded.
- **System prompt** teaches Claude the real vocabulary discovered above:
  the exact size/age/gender values; that temperament words go into
  `personality_keywords` (because activity_level is unused); that it must only
  set a `good_with_*` flag when the user explicitly asks. Keep it factual, not
  prescriptive (Opus 4.8 follows literal instructions well).
- **Error handling** with typed exceptions (`anthropic.APIError`,
  `RateLimitError`, `AuthenticationError`), and check `response.stop_reason ==
  "refusal"` before reading output. Return a safe domain error the UI can show;
  log details server-side (never leak internals to the UI).

### `search/query.py` — `DogQuery` → results
`search_dogs(session, query: DogQuery, limit: int = 50) -> list[DogORM]`, a pure
function that composes ORM filters (parameterized — no string interpolation):
- Always: `deleted_at IS NULL` and `status == "available"` (default; could be
  relaxed later).
- List fields → `DogORM.size.in_(query.sizes)` etc.
- Tristate: only filter when the user set the flag, e.g. `good_with_cats is True`
  → `DogORM.good_with_cats.is_(True)` (this **excludes unknown/NULL** — correct
  semantics; surface the excluded-unknown count in the UI so it's transparent).
- `breed_contains` → `DogORM.breed_primary.ilike(f"%{value}%")` (bound param via
  ORM, safe).
- `personality_keywords` → for each keyword, case-insensitive match against the
  `personality_traits` JSONB array. Reuse the existing JSONB column on
  `DogORM.personality_traits` (`models/dog.py`). Match with an `EXISTS` over
  `jsonb_array_elements_text` ILIKE, OR-ed across the dog, AND-ed across
  keywords (a dog must hint at every requested trait). Keep this in one helper.
- `max_adoption_fee` → `DogORM.adoption_fee <= value` (and not NULL).
- Return ordered (e.g. most recently listed first).

### `app.py` — Streamlit UI (repo root)
- Text input + Search button.
- On submit: `parse_query()` → show the parsed filters in an expander
  (transparency + great for debugging/learning) → `with Session() as session:
  search_dogs(...)` → render cards (photo from `photos[0]`, name, breed, age,
  size, city/state, good-with badges, adoption fee, link to `source_url`).
- Reuse the existing `Session` factory from `db/connection.py` (do **not** open
  a second engine).
- Handle the parser's domain error and empty-result case with friendly messages.

## Files to modify

- **`requirements.txt`** — add `anthropic` and `streamlit`.
- **`config.py`** — add `SEARCH_MODEL` (default `"claude-opus-4-8"`); the SDK
  reads `ANTHROPIC_API_KEY` itself, so just document it.
- **`.env.example`** — add `ANTHROPIC_API_KEY=` and optional `SEARCH_MODEL=`.
- **`CLAUDE.md`** — short "Searching (natural language)" section: how to run
  `streamlit run app.py`, the parse→filter→query flow, and the architectural
  note that the LLM never emits SQL.

## Tests (`tests/`)

Follow the existing pytest style in `tests/` (e.g. `test_upsert_dog.py`).
- `test_search_query.py` — the query builder is pure logic and the highest-value
  test. Drive `search_dogs` against a session (in-memory SQLite or an injected
  fake) and assert: list filters, tristate excludes NULL, personality keyword
  matching, deleted/adopted dogs excluded. **No API calls here.**
- `test_search_schema.py` — `DogQuery` rejects off-vocab values (e.g.
  `size=["gigantic"]`) and accepts valid ones.
- `test_search_parser.py` — mock the Anthropic client; assert input validation
  (empty/too-long rejected) and that a mocked `parsed_output` flows through.
  The real LLM is never hit in unit tests.

## Security notes (called out explicitly)

- LLM output is constrained by the Pydantic schema + ORM; user text cannot reach
  SQL. Prompt injection can only mis-set filters, not execute SQL.
- Validate/cap input length before the API call (cost + abuse).
- `ANTHROPIC_API_KEY` via env only; never logged, never in code.
- API errors logged server-side; UI shows a generic safe message.

## Verification (end-to-end)

1. `pip install -r requirements.txt` (adds anthropic, streamlit).
2. Set `ANTHROPIC_API_KEY` in `.env`.
3. `pytest tests/test_search_query.py tests/test_search_schema.py
   tests/test_search_parser.py` — all green (no network).
4. `streamlit run app.py`, type `"small dog good with cats, house trained"` —
   confirm the parsed-filters expander shows `sizes:[small]`,
   `good_with_cats:true`, `house_trained:true`, and that returned cards actually
   satisfy those filters.
5. Spot-check a temperament query `"calm couch potato dog"` returns dogs whose
   `personality_traits` include "Couch Potato"/"Quiet"/"Gentle".
6. Cross-check one result against psql:
   `SELECT name,size,good_with_cats,house_trained FROM dog_profiles
   WHERE size='small' AND good_with_cats AND house_trained AND deleted_at IS NULL;`

## Out of scope (future / noted as evolutionary debt)

- Semantic ranking/embeddings (current v1 is strict filtering).
- Soft tristate ("prefer good-with-cats, rank unknowns lower") instead of hard
  exclude.
- GIN index on `personality_traits` for scale (fine at 225 rows; matters later).
- Pagination, auth, FastAPI/JSON API (the commercial-product path).
