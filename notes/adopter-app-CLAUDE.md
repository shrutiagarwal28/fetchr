# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

> **This is the draft CLAUDE.md for the new adopter-app repo.** It lives in fetchr's
> `notes/` until that repo exists — copy it to the new repo's root as `CLAUDE.md`,
> along with these four notes files into the new repo's `notes/`:
> `matching-dimension-contract.md`, `adopter-profile-matching-design.md`,
> `matching-app-schema.md`, `matching-app-api.md`. Then delete this banner.

## What this project does

The adopter side of a dog-to-adopter matching platform. Adopters sign up, answer a
short lifestyle questionnaire, and get a ranked feed of adoptable dogs near them.
A chatbot/search bar lets them describe the dog they want in plain language.
Dog data is ingested by a separate repository (**fetchr** — scraping + feature
pipeline); this repo consumes it and owns everything adopter-facing.

## Repository boundary (the most important rule)

Both repos share **one Postgres database**, but the ownership line is strict:

- **fetchr owns:** `dog_profiles`, `dog_features`, `dog_profile_history`,
  `raw_scrapes`, and its reference tables. **This repo treats all of them as
  READ-ONLY.** Never write to them, never create migrations that touch them,
  never add indexes/columns to them from here.
- **This repo owns:** `adopter_profiles`, `adopter_interactions`,
  `adopter_preferences`, `question_trigger_config`, `adopter_question_events`.
- **The interface is `dog_features JOIN dog_profiles`** — query nothing else from
  fetchr's schema. `raw_scrapes` and scraper internals never cross the boundary.
- `adopter_interactions.dog_profile_id` is a **soft reference** (plain column, no
  DB-level foreign key across the boundary). Validate existence in app code.

## Architecture

One repo, two apps:

```
/web    Next.js  — signup/onboarding, swipe feed, chatbot UI
/api    FastAPI  — matching engine, adopter CRUD, chat parse endpoint
                   (Pydantic + SQLAlchemy + Alembic, same stack as fetchr)
```

The **matching engine is the product**; both surfaces sit on it:

```
onboarding answers ──► adopter dimension vector ──┐
                                                  ├──► match() ──► ranked dogs
chatbot free text ──► LLM parse ──► DogQuery ─────┘
                       (never SQL — validated Pydantic struct only)
```

## The dimension contract

Dogs and adopters are vectors in the **same dimension space** — see
`notes/matching-dimension-contract.md`. Two tiers:

- **Tier 1 — hard filters** (SQL WHERE, before scoring): location radius, kids /
  existing dogs / existing cats safety, max size, `status = 'available'`.
- **Tier 2 — soft scoring**: size, age, grooming, breed group, plus the nine
  Phase-5 semantic scores (energy, affection, sociability, playfulness,
  trainability, confidence, independence, special_needs, placement_restriction).

Every onboarding question MUST map to a dimension in this contract. A question
that maps to nothing is friction that can't affect matching — don't add it.

## Invariants (restate these when designing anything)

1. **The LLM never emits SQL.** Chat text is parsed into a typed, enum-constrained
   Pydantic struct (`DogQuery`); worst case is wrong filters, never injection.
2. **`adopter_interactions` is append-only and irreplaceable.** Never update or
   delete rows. Preferences are derived state, recomputable from this log.
3. **Explicit beats inferred.** A pop-up/questionnaire answer overrides any
   behavioral inference. Track the source per preference field.
4. **The Null Trap:** only hard-exclude dogs *known bad* (value = 0). Unknown
   (NULL) stays in the pool — most compatibility fields are majority-unknown.
   Surface "not confirmed — check with the shelter" in the UI instead.
5. **One shared vocabulary.** Onboarding questions, the chatbot's `DogQuery`, and
   the matcher all speak the dimension contract. Enums/literals are defined once
   and imported everywhere — never re-declared per module.
6. **A dog score of 0 on a bipolar dimension means "no signal", not "medium".**
   Down-weight that dimension's contribution; don't score it as a confident match.

## Design traps (already analyzed — don't rediscover them)

- **Breed bias:** expose breed *group* filtering, never raw breed names (58% of
  inventory is pit-type/mixed; name filters produce empty results). Pit-type
  breed group is audit-only — never a filter.
- **Cold-start uniformity:** the first ~10–15 cards must be intentionally diverse
  across size/age/breed group. Exploration before exploitation.
- **Over-questioning:** pop-up questions only fire to confirm a behavioral
  hypothesis; max one per session; two dismissals = never ask again.
- **No fabricated axes:** don't add a matching dimension no data feeds (e.g.
  travel-friendliness is a *proxy* over size + energy + independence, not a new
  column; reactivity/noise is deferred until a source provides it).

## Locked decisions (2026-07-12 — don't re-litigate)

| Decision | Choice |
|---|---|
| Database | Shared Postgres with fetchr (see boundary rules above) |
| Stack | FastAPI + Next.js (Django evaluated and rejected: second ORM vs Alembic on a shared DB, weaker async for the chatbot, templates poor for swipe UI) |
| Repo | One repo: `/web` + `/api` |
| Onboarding | Safety fields + ~4 lifestyle questions, each mapped to a dimension |
| Chatbot | Shares the matching engine — parser output feeds `match()`, not a parallel search path |
| Embeddings | Deferred; v1 is structured parse only. Semantic re-rank is a reserved Stage-4 slot |
| Auth | Stubbed (hardcoded test adopter) until real users; managed auth later — never custom |
| Hosting | Local only for now |
| Build order | Feed first (onboarding → ranked matches), chatbot second |

## Build order

1. Design docs first (see `notes/adopter-profile-matching-design.md`, "Pre-Coding
   Workflow"): data contract vs fetchr → adopter ERD → API contract → matching
   pseudocode → chatbot spec. **Docs before code at each step.**
2. First migration: `adopter_profiles` + `adopter_interactions` (the append-only
   log is the piece that can't be retrofitted — capture signals from day one).
3. Matching engine as a testable Python package inside `/api` (pure functions,
   DB session injected, no framework coupling).
4. Feed endpoint + onboarding UI.
5. Chatbot (LLM parser → `DogQuery` → same engine).

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Shared Postgres (same DB as fetchr), e.g. `postgresql://postgres:postgres@localhost:5432/fetchr` |
| `ANTHROPIC_API_KEY` | Chatbot parse calls (never logged, never in code) |

## Relationship to fetchr's docs

The authoritative design docs live in this repo's `notes/`:
- `matching-dimension-contract.md` — the shared vector space (THE contract)
- `adopter-profile-matching-design.md` — behavioral model, pop-up system, staging
- `matching-app-schema.md` — adopter-side tables (needs column-name reconciliation
  per the contract doc's "Stale references to fix")
- `matching-app-api.md` — endpoint sketches

When fetchr changes `dog_features`/`dog_profiles`, the column dependency list in
the data-contract doc is the compatibility surface — treat it as a versioned API.
