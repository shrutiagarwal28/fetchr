# Adopter Profile & Matching Algorithm Design

> Discussion notes — June 11, 2026.
> No code written yet. This is the design and plan.
> Updated same session: repository boundary decision added.

---

## Product Philosophy

**Don't ask adopters a long questionnaire upfront.** Collect the minimum viable profile, then learn preferences from behavior. Occasional pop-up questions fill in the gaps strategically — triggered by observed behavior, not shown on a schedule.

This mirrors how Netflix, Spotify, and Hinge work: cold start with minimal input, warm up fast from implicit signals, use targeted explicit questions to confirm hypotheses the behavioral data already suggests.

---

## The Three-Layer Data Model

Resist the temptation to store a single `adopter_preferences` row and update it in place. Split into three distinct tables:

```
adopter_profiles        ← slow-changing facts (location, household)
                          collected upfront; rarely changes

adopter_interactions    ← append-only event log (swipes, saves, views)
                          NEVER overwrite; this is your source of truth

adopter_preferences     ← learned/derived state
                          recomputed from interactions + explicit answers
                          can be rebuilt from scratch at any time
```

**Why this separation matters:** `adopter_interactions` is irreplaceable. If you store only the derived `adopter_preferences` and your inference logic changes later (it will), you've lost the raw signal forever. The event log lets you retrain the model on historical data. The preference table is just a materialized view.

---

## Minimum Viable Upfront Profile

Collect only what cannot be inferred from behavior and what is needed for safety filters:

```
zip_code                      # can't infer from swipes; needed for location radius
has_children: bool
youngest_child_age: int?      # null if no kids; needed to distinguish under-8 vs 8+
has_existing_dogs: bool
has_existing_cats: bool
```

That's it. No size preference, no breed, no activity level — those are learned from behavior.

**Why household composition is the exception to minimal collection:**
These fields aren't preferences — they're safety constraints. A family with a toddler should never be shown a dog that is *known* bad with children under 8. You cannot wait to infer this from swipes; you need it before showing the first card.

---

## What to NOT Collect Upfront

Fields that belong in `adopter_preferences` once inferred, not in the onboarding form:

| Field | Why not upfront |
|---|---|
| `preferred_size` | Learned from right-swipe patterns in ~10 interactions |
| `preferred_age_range` | Learned from swipes; confirm via pop-up |
| `preferred_breed_group` | Learned from swipes |
| `activity_level` | Hard to infer from swipes; ask via triggered pop-up |
| `home_type` | Ask via triggered pop-up after behavioral pattern emerges |
| `experience_level` | Ask via pop-up |
| `ok_with_special_needs` | Ask via pop-up after enough swipes to have context |
| `coat_length_preference` | Low priority; infer or ask late |

---

## The Interaction Event Log (`adopter_interactions`)

Every behavioral signal is a row. Append-only. The columns that matter:

```
id
adopter_id          FK to adopter_profiles
dog_profile_id      FK to dog_profiles
event_type          enum (see below)
event_at            TIMESTAMPTZ
metadata            JSONB    # flexible payload per event type
                              # e.g. view_duration_seconds, source_screen
```

### Event Types and Signal Strength

| Event type | Signal | What you can infer |
|---|---|---|
| `right_swipe` | Strong positive | Size, breed group, age, personality traits of liked dogs |
| `left_swipe` | Strong negative | What they are filtering out |
| `save_to_watchlist` | Very strong positive | Near-intent; stronger than a swipe |
| `dismiss_from_watchlist` | Strong negative | Changed mind after initial interest |
| `view_detail` | Moderate positive | Curious enough to tap through |
| `view_duration_seconds` | Continuous signal | Lingered = interest; immediate back = low interest |
| `contact_shelter` | Near-conversion signal | Extremely strong positive |
| `explicit_answer` | Highest signal | Explicit always overrides implicit inference |

From a stream of right-swipe events you can derive, **without ever asking**: preferred size, preferred age range, breed group affinity, which personality traits correlate with engagement.

---

## Derived Preferences (`adopter_preferences`)

This table is a materialized representation of what has been learned. It is recomputable from `adopter_interactions` at any time. Schema sketch:

```
adopter_id              FK
max_dog_size            nullable — inferred from right-swipe size distribution
preferred_age_range     nullable — inferred or confirmed via pop-up
preferred_breed_groups  JSONB array — inferred or confirmed
activity_level          nullable — hard to infer; usually set via pop-up
home_type               nullable — set via pop-up
experience_level        nullable — set via pop-up
ok_with_special_needs   nullable (true/false/not_yet_known)
free_text_description   nullable — "looking for a calm apartment dog..."
                                   embedded for semantic matching (Phase 3)

-- Source tracking per field
max_dog_size_source     enum: inferred / explicit
preferred_age_source    enum: inferred / explicit
-- etc.
```

**Explicit always beats inferred.** If a pop-up answer says "adult dog", that locks in and overrides any conflicting behavioral inference. Track the source per field so you know which answers came from explicit confirmation vs. pattern recognition.

---

## The Pop-Up Question System

### Design Rules

1. **One question per session maximum** in early stages. Never stack two pop-ups.
2. **Questions are triggered by behavior, not scheduled.** Never ask a question until behavioral data suggests a hypothesis.
3. **Questions are confirmatory, not exploratory.** You already have a hypothesis; the question confirms or refutes it.
4. **Explicit beats implicit.** Once answered, the answer is locked in (until the user changes it in settings).
5. **Track dismissals.** If an adopter dismisses a question twice, never ask it again. Respect the signal.
6. **Max 1 unanswered question in flight.** Don't queue up 3 questions and show them consecutively.

### Trigger Examples

| Behavioral pattern | Question shown | Maps to |
|---|---|---|
| 3+ consecutive right-swipes on large dogs | "Looks like you're open to bigger dogs — do you have space for one?" | `max_dog_size` |
| Consistent left-swipes on senior dogs | "Are you looking for a younger dog specifically?" | `preferred_age_range` |
| 3+ saves all have `trait_playful` / `trait_athletic` | "How active is your typical day?" (sedentary / moderate / active) | `activity_level` |
| First shelter contact | "What stood out about this dog?" (free text) | `free_text_description` → embedding |
| Right-swipes on 2+ special needs dogs | "Are you open to a dog with special needs?" | `ok_with_special_needs` |
| Consistent left-swipes on all pit bull types | Do NOT ask — show breed group diversity options in settings instead | breed filtering |

### Schema

Two tables:

**`question_trigger_config`** — static config, not per-adopter:
```
id
trigger_condition       JSONB   # rule definition: event_type, count threshold, field pattern
question_text           TEXT
response_options        JSONB   # [{label, maps_to_value}] — null if free text
maps_to_preference_field  VARCHAR
priority                INT     # higher = shown first if multiple triggers fire at once
```

**`adopter_question_events`** — per-adopter history:
```
id
adopter_id
trigger_config_id
shown_at              TIMESTAMPTZ
response              JSONB     # null if dismissed
dismissed             BOOL
```

---

## How the Matching Algorithm Evolves

### Stage 1 — Cold Start (0 interactions)

- Hard filter: location radius, household safety constraints (kids, existing pets)
- Rank by: popularity across all adopters (most-saved dogs) + recency (recently listed)
- **Show diverse dogs intentionally.** Do not show 15 medium mixed breeds in a row. Distribute across size, age, breed group. You need reactions across the full space to learn preferences fast. This is the exploration phase.

### Stage 2 — Warm Start (~10–20 interactions)

- Content-based matching: "You right-swiped 8 dogs — 7 were medium, 6 had `trait_friendly`, 5 were 1–3 years old → surface more of those"
- Implementation: weighted dot product between the adopter's inferred preference vector and each dog's feature vector (from `dog_features` table)
- Pop-up questions begin firing here to confirm inferred hypotheses

### Stage 3 — Mature (~enough adopters for collaborative signal)

- Collaborative filtering: *"Adopters with similar household composition and behavioral patterns in NJ right-swiped these dogs"*
- Matrix factorization or embedding-based user similarity
- `dog_profile_history` feeds in here: `went_pending_count` and `days_to_adoption` are demand signals that reflect real human behavior across all adopters, not just the current user

### Stage 4 — Semantic Re-ranking (once embeddings exist)

- Adopt a description (`free_text_description` from a pop-up answer or profile) → embed it
- Dog descriptions are already 100% populated with median 980 chars — excellent candidate
- Reorder the top-N results by cosine similarity of adopter embedding ↔ dog description embedding
- This is `pgvector` territory — already on the expansion plan

---

## The Matching Function (Conceptual)

```
match(adopter, candidate_dogs):

  1. Hard filter (SQL WHERE)
     - status = 'available'
     - PostGIS: distance <= adopter.max_distance_miles
     - IF adopter.has_children AND youngest_child_age < 8:
         EXCLUDE dogs WHERE compat_kids = 0  (known bad — not null)
     - IF adopter.has_existing_dogs:
         EXCLUDE dogs WHERE compat_dogs = 0
     - IF adopter.has_existing_cats:
         EXCLUDE dogs WHERE compat_cats = 0  (note: 73% null — only exclude known bad)
     - IF adopter.max_dog_size is known:
         EXCLUDE dogs WHERE size_ord > adopter.max_size_ord

  2. Preference scoring (weighted sum over dog_features)
     - size match weight
     - age range match weight
     - breed group match weight
     - personality trait overlap weight (multi-hot dot product)
     - activity level ↔ energy trait alignment weight
     - compatibility field alignment (partial credit for unknown, full for known-match)

  3. Semantic re-rank (top-N only, once embeddings exist)
     - pgvector cosine similarity: adopter_embedding ↔ dog_description_embedding

  4. Diversity injection (cold start only)
     - Force representation across size/breed_group/age buckets
     - Prevents the preference loop from collapsing too fast
```

---

## Critical Design Traps

### The Null Trap
`compat_cats` is 73% null. `compat_dogs` is 28% null. Hard-filtering out nulls for adopters who have cats or dogs will eliminate most of the inventory.

**Rule: only hard-exclude dogs that are *known bad* (value = 0). Unknowns stay in the pool.**

Surface a UI note for the adopter: *"Cat compatibility not confirmed — check with the shelter before applying."*

### The Breed Bias Trap
58% of current inventory is pit bulls and mixed breeds. If you expose raw breed name filtering and an adopter filters to "Labrador Retriever only", they'll see almost nothing. Expose breed *group* filtering (Bully, Hound, Sporting, etc.) rather than breed names. The groups are more useful and less likely to produce empty result sets.

### The Cold Start Uniformity Trap
The biggest failure mode in session 1 is showing 20 dogs of the same type before you know anything about the adopter. You learn nothing. Intentionally diversify the first 10–15 cards across all dimensions — you are in exploration mode.

### The Over-questioning Trap
Don't ask a question until you have a behavioral hypothesis to confirm. Asking "what size dog do you want?" before any swipes is just a questionnaire by another name. The pop-up system only has value if the timing is earned.

---

## What to Build First (Implementation Order)

When implementation begins, the order should be:

1. **`adopter_profiles` table** — minimal schema (location + household only)
2. **`adopter_interactions` event log** — append-only; capture every signal from day one. This is the most irreplaceable piece. Cannot be retrofitted from future usage.
3. **`adopter_preferences` table** — derived; initially empty; populated as inference runs
4. **`question_trigger_config` + `adopter_question_events`** — pop-up system
5. **Hard filter matching** — Stage 1 (SQL-only, content-based)
6. **Preference scoring** — Stage 2 (weighted sum against `dog_features`)
7. **Embeddings + semantic re-ranking** — Stage 3/4 (pgvector; requires dog description embeddings first)

---

## Repository Boundary: fetchr vs. Matching App

fetchr is the scraping and data pipeline layer only. The matching app is a separate repository. The boundary is clean.

### What fetchr owns (this repository)

```
Scrapers (PetFinder, AdoptaPet, ...)
    ↓
raw_scrapes           ← exact blobs; never queried by the matching app
    ↓
dog_profiles          ← canonical normalized record
    ↓
dog_profile_history   ← status transitions; ML training signal
    ↓
dog_features          ← ML-ready feature vector  ◄── THIS IS THE INTERFACE
```

Supporting reference tables: `petfinder_breeds`, `organizations` (planned).

### What the matching app owns (separate repository)

```
adopter_profiles
adopter_interactions
adopter_preferences
question_trigger_config + adopter_question_events
matching algorithm
product API / UI
```

### The contract

`dog_features` is the handoff point. fetchr writes it; the matching app reads it. The matching app queries `dog_features JOIN dog_profiles` and nothing else from fetchr's schema. `raw_scrapes`, scraper internals, migration history — none of that crosses the boundary.

### The one edge case: dog description embeddings

Dog `description` text lives in `dog_profiles` (fetchr). The embeddings for semantic re-ranking could be computed by either side.

**Decision: matching app computes embeddings.**

Embedding strategy — model choice, dimensionality, chunking — is a matching concern, not a scraping concern. It will change as the model evolves. Putting it in fetchr would mean redeploying the scraper every time the matching team changes their embedding model. The matching app reads the raw `description` text from the shared database and owns the vectorization entirely.

### Schema stability note (for later)

Once both repos are in production, fetchr's changes to `dog_features` and `dog_profiles` can silently break the matching app. When that time comes: the matching app should declare explicitly which columns it depends on, and fetchr treats those columns as a versioned API — no rename or drop without coordination.

---

## Pre-Coding Workflow for the Matching App Repository

Before writing a single line of code in the new repo, complete these steps in order. Don't skip to repo setup — the design work is what makes the coding fast.

### Step 1 — Answer the Product Questions First

These are decisions only you can make. Every technical decision downstream depends on them.

**Define the MVP explicitly.**
The minimum thing that has to exist for the app to be useful to one real adopter. Proposed MVP: an adopter can create a profile, see a ranked feed of dogs near them filtered by their household, and swipe. No pop-up questions, no collaborative filtering, no embeddings — just Stage 1 matching (hard filters + popularity ranking). Write this down before designing anything.

**Decide the surface.**
Web app, mobile app, or API-only for now? This determines framework choice and how you think about authentication. Wrong choice here is expensive to undo.

**Decide who runs the database.**
Is the matching app reading directly from fetchr's Postgres, or will there eventually be two separate databases? For now, sharing one Postgres instance is the right call — it avoids a sync layer that isn't needed yet. But decide this explicitly; it affects config and migration setup.

---

### Step 2 — Nail the Data Contract with fetchr

Before designing the matching app's schema, write down exactly what it will consume from fetchr. This produces two things:

**A query spec** — the actual SQL the matching app will run against fetchr's tables. Write it out before coding. This forces you to discover missing indexes or columns in fetchr before you're blocked mid-build.

**A column dependency list** — the specific `dog_features` and `dog_profiles` columns the matching app depends on. Anything not on this list, fetchr can change freely. This is the schema stability contract.

This step often reveals that fetchr needs one or two additions before the matching app can start.

→ See `matching-app-schema.md` for the schema design.

---

### Step 3 — Design the Adopter-Side Schema

Sketch the full ERD before writing a single migration. Tables needed:

```
adopter_profiles
adopter_interactions      (append-only event log)
adopter_preferences       (derived; recomputable)
question_trigger_config   (static config)
adopter_question_events   (per-adopter pop-up history)
```

For each table, decide: columns, types, nullability, indexes, foreign keys. Key questions:
- Primary key strategy: UUID vs serial? (UUID is safer for a consumer app — no enumerable IDs)
- Does `adopter_interactions.dog_profile_id` need a hard FK to fetchr's `dog_profiles`, or is it a soft reference since the repos are conceptually separate?
- What indexes does `adopter_interactions` need? The inference job will read it constantly.

Don't write migrations yet — design the schema as a document first.

→ See `matching-app-schema.md`.

---

### Step 4 — Design the API Contract

List the endpoints the product needs and sketch their request/response shapes. Don't implement — just name and describe.

MVP endpoints:
```
POST /adopters                      — create profile (onboarding)
GET  /adopters/{id}/feed            — ranked dog feed
POST /adopters/{id}/interactions    — record a swipe, save, or view
GET  /adopters/{id}/watchlist       — saved dogs
GET  /adopters/{id}/preferences     — read learned preferences
```

This step reveals design gaps early — e.g., what does the feed response look like? Does it return full dog profiles or just IDs? Does it include a match score? Answering those before coding saves significant refactoring.

→ See `matching-app-api.md`.

---

### Step 5 — Write the Matching Logic in Pseudocode

Before writing any SQL or Python, write out the matching function in plain pseudocode detailed enough that a second engineer could implement it without asking questions.

Decide explicitly:
- What is the exact SQL for the hard filter stage?
- For cold start, how is popularity scored? `save_to_watchlist` count? Right-swipe count across all adopters? A combination?
- What is the fallback if a dog has zero interactions from any adopter?
- What are you explicitly NOT building in the MVP? Write this down — scope creep at coding time is the main reason projects stall.

→ Matching logic pseudocode lives in the Matching Function section above and in `matching-app-schema.md`.

---

### Step 6 — Choose the Tech Stack

Only after the product and design decisions are made, pick tools. Those decisions constrain the choices sensibly.

Things to decide:
- **Language / framework** — Python + FastAPI is natural given fetchr is Python, but make it a conscious decision
- **Auth** — how do adopters log in? Email/password, Google OAuth? Do not design custom auth; use a library or managed service (e.g. Supabase Auth, Clerk)
- **Hosting** — local only for now, or deploy from the start?
- **Migration tooling** — Alembic again (consistent with fetchr) is the default choice

---

### Step 7 — Set Up the Repo

Only now create the repository. With the above done, you know:
- What the folder structure should look like
- What goes in `requirements.txt`
- What environment variables are needed (including fetchr's DB URL)
- What the first migration will create

Repo setup takes an afternoon. The design work above is what takes real time — and what makes the coding fast once it starts.

---

## Open Questions (Not Yet Resolved)

- **How long does cold start last?** What's the threshold (N interactions) at which we switch from popularity-based to preference-based ranking? Needs experimentation.
- **Recency weighting.** A right-swipe from 3 months ago should carry less weight than one from yesterday. Decay function TBD.
- **Adopter-side feedback on matches.** If the adopter is shown a dog and immediately left-swipes, does that feed back into improving the dog's features or just the adopter's preference model?
- **Multi-household decision making.** Real adopter decisions often involve two people (partners). The profile models one person. Is this a problem at this stage?
- **Re-engagement.** If an adopter goes dark for 3 months and comes back, do we reset or preserve their preference model? Preferences can drift.
- **MVP surface decision.** Web app, mobile app, or API-only? Not yet decided.
- **Database sharing decision.** Shared Postgres with fetchr vs. separate DB? Not yet decided — leaning toward shared for now.
