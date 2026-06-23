# Matching App — Schema Design

> Design doc — not yet implemented.
> All adopter-side tables live in the matching app repository.
> fetchr tables (`dog_profiles`, `dog_features`, etc.) are read-only from the matching app's perspective.

---

## Data Contract with fetchr

The matching app queries these columns and nothing else from fetchr's schema.
fetchr must treat these as a versioned API — no rename or drop without coordination.

### From `dog_profiles`

| Column | Used for |
|---|---|
| `id` | FK target from `adopter_interactions.dog_profile_id` |
| `source_id` | Display / dedup reference |
| `name` | Feed display |
| `breed_primary` | Display |
| `breed_canonical_id` | Breed group lookup |
| `age_category` | Display fallback |
| `age_years_approx` | Feed display |
| `gender` | Display + soft filter |
| `size` | Display |
| `status` | Hard filter: `WHERE status = 'available'` |
| `city`, `state`, `postal_code` | Location display; PostGIS radius filter (once lat/lng geocoded) |
| `good_with_kids` | Hard filter for adopters with children |
| `good_with_dogs` | Hard filter for adopters with existing dogs |
| `good_with_cats` | Hard filter for adopters with cats (73% null — only exclude known bad) |
| `description` | Display + embedding computation (matching app computes embeddings) |
| `photos` | Feed card display |
| `shelter_name` | Display |
| `shelter_url` | "Contact shelter" link |
| `first_seen_at` | Freshness signal |

### From `dog_features`

| Column | Used for |
|---|---|
| `dog_profile_id` | FK join to `dog_profiles` |
| `size_ord` | Preference scoring: `size_ord <= adopter.max_size_ord` |
| `age_category_ord` | Preference scoring |
| `age_years_imputed` | Preference scoring; more reliable than `age_category` |
| `age_was_imputed` | Confidence weighting — imputed ages carry less weight |
| `coat_length_ord` | Preference scoring |
| `compat_kids` | Hard filter: exclude where `= 0` if adopter has children |
| `compat_dogs` | Hard filter: exclude where `= 0` if adopter has dogs |
| `compat_cats` | Hard filter: exclude where `= 0` if adopter has cats |
| `house_trained_flag` | Preference scoring |
| `vaccinated_flag` | Preference scoring |
| `spayed_neutered_flag` | Preference scoring |
| `breed_group` | Preference scoring; breed group filtering |
| `is_purebred` | Display + preference scoring |
| `trait_affectionate` … `trait_*` | Multi-hot preference scoring (dot product with adopter trait preferences) |
| `other_traits_count` | Completeness signal |
| `days_listed` | Urgency signal; input to cold-start popularity scoring |

---

## Adopter-Side Tables

### `adopter_profiles`

Slow-changing facts. Collected at onboarding. Rarely updated.

```sql
CREATE TABLE adopter_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Location (Tier 1 hard filter)
    zip_code            TEXT NOT NULL,
    lat                 DOUBLE PRECISION,       -- geocoded from zip_code at write time
    lng                 DOUBLE PRECISION,
    max_distance_miles  INTEGER NOT NULL DEFAULT 50,

    -- Household composition (Tier 1 — safety constraints, not preferences)
    has_children        BOOLEAN NOT NULL,
    youngest_child_age  INTEGER,                -- null if has_children = false
    has_existing_dogs   BOOLEAN NOT NULL,
    has_existing_cats   BOOLEAN NOT NULL,

    deleted_at          TIMESTAMPTZ             -- soft delete
);

CREATE INDEX ix_adopter_profiles_zip ON adopter_profiles (zip_code);
```

**Why UUID for PK:** Consumer-facing IDs should not be enumerable. Sequential integers in a URL let anyone iterate over all adopter profiles.

**Why geocode at write time:** PostGIS radius queries need `lat/lng`. Geocoding at read time on every feed request is slow and expensive. Geocode once on insert.

---

### `adopter_interactions`

Append-only event log. Never update. Never delete (soft-delete the adopter profile if needed, not individual events).

```sql
CREATE TABLE adopter_interactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adopter_id          UUID NOT NULL REFERENCES adopter_profiles(id),
    dog_profile_id      INTEGER NOT NULL,       -- soft reference to fetchr's dog_profiles.id
                                                -- no hard FK: fetchr owns that table
    event_type          TEXT NOT NULL,          -- see event type enum below
    event_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata            JSONB                   -- flexible per-event payload
);

-- Inference job reads all events for a given adopter constantly
CREATE INDEX ix_interactions_adopter_time ON adopter_interactions (adopter_id, event_at DESC);

-- Feed construction needs to know which dogs an adopter has already seen
CREATE INDEX ix_interactions_adopter_dog ON adopter_interactions (adopter_id, dog_profile_id);
```

**Why soft reference for `dog_profile_id`:** A hard FK would require fetchr and the matching app to share the same Postgres schema or use cross-schema references. Soft reference keeps the repos independently deployable. The matching app must tolerate a dog being deleted from fetchr (soft-deleted) — it should handle nulls gracefully in the join.

**Event type values:**
```
right_swipe
left_swipe
save_to_watchlist
dismiss_from_watchlist
view_detail
contact_shelter
explicit_answer             -- pop-up question answered; detail in metadata
```

**`metadata` JSONB payload per event type:**
```
right_swipe / left_swipe:   { source_screen: "feed" | "watchlist" }
view_detail:                { duration_seconds: int, source_screen: str }
contact_shelter:            { shelter_url: str }
explicit_answer:            { question_id: int, response: str | bool }
```

---

### `adopter_preferences`

Derived state. Recomputable from `adopter_interactions` at any time. Never treat this as the source of truth — that is `adopter_interactions`.

```sql
CREATE TABLE adopter_preferences (
    adopter_id              UUID PRIMARY KEY REFERENCES adopter_profiles(id),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Tier 1 size constraint (inferred or explicit)
    max_dog_size            TEXT,               -- small / medium / large / xlarge
    max_dog_size_source     TEXT,               -- inferred / explicit

    -- Tier 2 preferences
    preferred_age_range     TEXT,               -- puppy / young / adult / senior
    preferred_age_source    TEXT,
    preferred_breed_groups  JSONB,              -- ["Bully", "Hound"] — empty = no preference
    preferred_breed_source  TEXT,
    preferred_gender        TEXT,               -- male / female / any
    preferred_gender_source TEXT,

    -- Lifestyle (hard to infer from swipes — usually set via pop-up)
    activity_level          TEXT,               -- sedentary / moderate / active / very_active
    activity_level_source   TEXT,
    home_type               TEXT,               -- apartment / house_no_yard / house_with_yard / farm
    home_type_source        TEXT,
    experience_level        TEXT,               -- first_time / some / experienced
    experience_level_source TEXT,

    ok_with_special_needs   BOOLEAN,
    special_needs_source    TEXT,

    -- Semantic preference (from pop-up free text; used for embedding matching)
    free_text_description   TEXT,

    -- Confidence signal
    total_interactions      INTEGER NOT NULL DEFAULT 0,
    is_cold_start           BOOLEAN NOT NULL DEFAULT true   -- false once >= N interactions
);
```

**Why source tracking per field:** Explicit answers from pop-ups override inferred values. Tracking the source lets the inference job know not to overwrite an explicit answer with a new inference.

**Why `is_cold_start`:** The feed construction query needs to know whether to use popularity-based ranking or preference-based ranking. Rather than recomputing `COUNT(interactions)` on every feed request, keep it materialized here.

---

### `question_trigger_config`

Static configuration. Defines which behavioral patterns fire which questions.
Populated by the engineering team — not by adopters.

```sql
CREATE TABLE question_trigger_config (
    id                          SERIAL PRIMARY KEY,
    trigger_condition           JSONB NOT NULL,
    -- Example:
    -- { "event_type": "right_swipe", "field": "size_ord", "value": 2,
    --   "count_threshold": 3, "window": "session" }

    question_text               TEXT NOT NULL,
    response_options            JSONB,
    -- Example: [{"label": "Sedentary", "value": "sedentary"},
    --            {"label": "Moderate",  "value": "moderate"},
    --            {"label": "Active",    "value": "active"}]
    -- null = free-text response

    maps_to_preference_field    TEXT NOT NULL,
    priority                    INTEGER NOT NULL DEFAULT 0,
    is_active                   BOOLEAN NOT NULL DEFAULT true
);
```

---

### `adopter_question_events`

Per-adopter pop-up history. Tracks what was shown, answered, and dismissed.

```sql
CREATE TABLE adopter_question_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    adopter_id              UUID NOT NULL REFERENCES adopter_profiles(id),
    trigger_config_id       INTEGER NOT NULL REFERENCES question_trigger_config(id),
    shown_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    response                JSONB,              -- null if dismissed
    dismissed               BOOLEAN NOT NULL DEFAULT false
);

-- Don't show a dismissed question again; check this before firing
CREATE INDEX ix_question_events_adopter ON adopter_question_events (adopter_id, trigger_config_id);
```

**Dismissal rule:** If `dismissed = true` appears twice for the same `(adopter_id, trigger_config_id)`, never show that question to that adopter again.

---

## Indexes to Request from fetchr

These indexes need to exist on fetchr's tables for the matching app's feed query to be performant:

```sql
-- Hard filter: status is always the first WHERE clause
-- Already likely exists, but confirm
CREATE INDEX ix_dog_profiles_status ON dog_profiles (status) WHERE deleted_at IS NULL;

-- Preference scoring join
CREATE INDEX ix_dog_features_dog_profile_id ON dog_features (dog_profile_id);

-- Breed group scoring
CREATE INDEX ix_dog_features_breed_group ON dog_features (breed_group);

-- Size filter
CREATE INDEX ix_dog_features_size_ord ON dog_features (size_ord);
```

---

## Open Schema Questions

- **Hard vs. soft FK on `adopter_interactions.dog_profile_id`:** Final decision deferred until database sharing strategy is confirmed. If shared DB, make it a hard FK with `ON DELETE SET NULL`. If separate DBs later, soft reference is correct from the start.
- **Geocoding service:** What geocodes `zip_code → lat/lng` at adopter profile creation? Options: Google Maps API, PostGIS built-in, a free batch geocoder. Decide before building the onboarding flow.
- **`adopter_preferences` update frequency:** Does the inference job run after every interaction or in batches? Batch (e.g., after every 5 interactions) is simpler to start.
