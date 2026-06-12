# Matching App — API Design

> Design doc — not yet implemented.
> Sketches the endpoints the product needs and their request/response shapes.
> Purpose: catch design gaps before coding, not after.

---

## Design Principles

- **Return enough for the feed card in one call.** The feed endpoint must not require a second round-trip to render a dog card. Embed the dog fields the UI needs directly in the response.
- **Record interactions server-side only.** Clients send raw events (`right_swipe`, `view_detail`). The server writes to `adopter_interactions` and decides when to update `adopter_preferences`. The client never writes preferences directly.
- **Don't expose internal IDs in URLs where possible.** Use the adopter's UUID, not a serial integer.
- **Status codes matter.** 404 means the resource doesn't exist. 422 means the request is malformed. Don't return 200 with an error body.

---

## MVP Endpoints

### `POST /adopters`

Create an adopter profile. Called once during onboarding.

**Request body:**
```json
{
  "email": "user@example.com",
  "zip_code": "07302",
  "max_distance_miles": 50,
  "has_children": true,
  "youngest_child_age": 4,
  "has_existing_dogs": false,
  "has_existing_cats": true
}
```

**Response `201 Created`:**
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "created_at": "2026-06-12T10:00:00Z"
}
```

**Side effects:**
- Geocodes `zip_code → lat/lng` and stores on the profile.
- Creates an empty `adopter_preferences` row with `is_cold_start = true`.

---

### `GET /adopters/{id}/feed`

Returns a ranked, paginated list of available dogs for this adopter.
This is the core product endpoint — the most important one to get right.

**Query params:**
```
limit       int     default 20
cursor      str     opaque pagination cursor (not a page number)
```

**Response `200 OK`:**
```json
{
  "dogs": [
    {
      "dog_profile_id": 123,
      "name": "Biscuit",
      "breed_primary": "Pit Bull Terrier",
      "breed_group": "Bully",
      "age_years_approx": 2.5,
      "age_category": "Young",
      "size": "Medium",
      "gender": "Female",
      "city": "Jersey City",
      "state": "NJ",
      "distance_miles": 3.2,
      "photo_url": "https://...",
      "days_listed": 12,
      "compatibility_notes": ["cat compatibility not confirmed"],
      "match_score": 0.84
    }
  ],
  "next_cursor": "opaque_string_or_null",
  "is_cold_start": true
}
```

**How the feed is built (server-side):**

1. Hard filter (SQL WHERE):
   - `status = 'available'`
   - PostGIS radius: `distance <= adopter.max_distance_miles`
   - If `has_children AND youngest_child_age < 8`: exclude `compat_kids = 0`
   - If `has_existing_dogs`: exclude `compat_dogs = 0`
   - If `has_existing_cats`: exclude `compat_cats = 0`
   - Exclude dogs already seen: `dog_profile_id NOT IN (SELECT dog_profile_id FROM adopter_interactions WHERE adopter_id = ?)`
   - If `max_dog_size` is known in preferences: exclude `size_ord > adopter.max_size_ord`

2. Rank:
   - **Cold start (`is_cold_start = true`):** rank by `(save_count DESC, days_listed ASC)` across all adopters — most popular and most recently listed first. Inject diversity across `size_ord` and `breed_group` buckets.
   - **Warm start (`is_cold_start = false`):** weighted preference scoring against `dog_features`.

3. `compatibility_notes`: surface warnings for null fields that couldn't be hard-filtered, e.g. `"cat compatibility not confirmed"`. The UI shows this as a soft warning on the card.

**Why cursor pagination, not page numbers:** The feed is personalized and changing. Page 2 on a second request may not be the same as page 2 on the first request. Cursors are stable.

---

### `POST /adopters/{id}/interactions`

Record a behavioral event. The most frequently called endpoint.

**Request body:**
```json
{
  "dog_profile_id": 123,
  "event_type": "right_swipe",
  "metadata": {
    "source_screen": "feed"
  }
}
```

**Valid `event_type` values:**
```
right_swipe
left_swipe
save_to_watchlist
dismiss_from_watchlist
view_detail               — metadata must include duration_seconds
contact_shelter           — metadata must include shelter_url
explicit_answer           — metadata must include question_id and response
```

**Response `204 No Content`**

No body. The client doesn't need confirmation — it's fire-and-forget.

**Side effects:**
- Writes a row to `adopter_interactions`.
- Checks if `total_interactions` has crossed the warm-start threshold; if so, sets `is_cold_start = false` on `adopter_preferences`.
- Checks if any `question_trigger_config` conditions are now satisfied; if so, queues a pop-up for the next feed response. (The pop-up is delivered inline with the feed response, not via a separate push.)

---

### `GET /adopters/{id}/watchlist`

Returns all dogs the adopter has saved (`save_to_watchlist` events that have not been followed by `dismiss_from_watchlist`).

**Response `200 OK`:**
```json
{
  "dogs": [
    {
      "dog_profile_id": 123,
      "name": "Biscuit",
      "status": "available",
      "photo_url": "https://...",
      "saved_at": "2026-06-10T14:22:00Z",
      "status_changed_since_save": false
    }
  ]
}
```

`status_changed_since_save` is `true` if the dog's `status` in fetchr changed (e.g., went pending) after the adopter saved it. This is a useful UI signal: "Biscuit is now pending — someone else applied."

---

### `GET /adopters/{id}/preferences`

Read the adopter's current learned preferences. Used to power a "your profile" settings screen where adopters can see and manually override what the app has inferred.

**Response `200 OK`:**
```json
{
  "max_dog_size": "medium",
  "max_dog_size_source": "inferred",
  "preferred_age_range": "young",
  "preferred_age_source": "explicit",
  "preferred_breed_groups": ["Bully", "Mixed"],
  "activity_level": null,
  "home_type": null,
  "experience_level": null,
  "ok_with_special_needs": null,
  "is_cold_start": false,
  "total_interactions": 34
}
```

`null` fields = not yet known. The UI should show these as "still learning..." rather than empty.

---

### `PATCH /adopters/{id}/preferences`

Manually override a preference. Called when an adopter edits their profile in settings.
Overrides always set the source to `explicit`.

**Request body (partial update — only include fields being changed):**
```json
{
  "max_dog_size": "large",
  "activity_level": "active"
}
```

**Response `200 OK`:** returns the full updated preferences object (same shape as GET).

---

## Post-MVP Endpoints (Planned, Not for MVP)

These are defined here so the MVP design doesn't accidentally make them hard to add later.

```
GET  /adopters/{id}/feed/question     — check if a pop-up question is pending
POST /adopters/{id}/questions/{qid}/answer   — submit a pop-up answer
DELETE /adopters/{id}                 — account deletion (GDPR; soft delete)
GET  /dogs/{id}                       — full dog detail page
GET  /admin/triggers                  — list/edit question trigger configs
```

---

## Authentication (Undecided)

The API above assumes the caller is authenticated — every `{id}` endpoint must verify the caller owns that adopter profile. Authentication strategy is not yet chosen.

Options:
- **Supabase Auth / Clerk** — managed service; handles email/password + OAuth; integrates cleanly with FastAPI via JWT verification middleware. Recommended: avoids building auth from scratch.
- **Custom JWT** — full control; more work; not recommended at this stage.

Whatever is chosen, the auth middleware must:
1. Verify the JWT on every request
2. Inject the `adopter_id` from the token, not from the URL (never trust the client to self-identify)
3. Return `403` if the `adopter_id` from the token doesn't match the `{id}` in the URL

---

## Open API Questions

- **How is the cold-start diversity injection implemented?** Force top-N results to include at least one dog per `size_ord` bucket and two breed groups? What if the inventory doesn't have enough variety to satisfy the constraint?
- **Pop-up question delivery:** Is the pending question returned inline with `GET /feed` (in the response body), or via a separate polling endpoint? Inline is simpler; separate gives more control over timing.
- **Match score visibility:** Should `match_score` be returned to the client, or kept server-side only? Showing a score can be gimmicky; hiding it keeps the UX cleaner. Not decided.
- **Rate limiting:** How many right-swipes per minute is reasonable? Bots and scrapers can hit the interactions endpoint. Needs a rate-limit strategy before going to production.
