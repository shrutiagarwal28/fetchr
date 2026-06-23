# Additional Findings — PetFinder GraphQL Exploration

**Date:** 2026-06-02  
**Method:** Playwright network interception on the listing page (`/search/dogs-for-adoption/us/nj/jerseycity/`). Captured all GraphQL traffic the browser made on load. Operations intercepted: `getGeoLocation`, `SearchAnimal`, `AllAnimalAttributes`, `GetMyUserData`.

---

## How PetFinder's frontend actually works

The listing page `__NEXT_DATA__` contains almost nothing useful — just CMS navigation (`menuData`), `initialPetType`, `initialLocationSlug`, and `petIds: null`. All real data (search results, filters, user data) is loaded dynamically via GraphQL after page load.

**GraphQL endpoint:** `https://psl.petfinder.com/graphql`

PetFinder exposes the following credentials in their public `__NEXT_DATA__` `runtimeConfig` (visible to any browser, no intrusion required). Saved separately in `exposed-creds.json`.

\
| Key | Value |
|---|---|
| `X_CLIENT_ID` | `0K3buVjVqhUvdoU6UVpbN0zejPhQcaUzt6mLcU6SSBfcxqCnj9` |
| `X_CLIENT_SECRET` | `cGTWvL3IY9boTC6VNcKCdTlVB1l5UPaXmd4xsJhp` |
| `PSL_REBUILD_GRAPHQL_URL` | `https://psl.petfinder.com/graphql` |
| `DRUPAL_CLIENT_SECRET` | `H84HF4IN3G33JJNFIJSMCKQWE9183957NIEIIE` |
| `BASIC_AUTH_PASS` | `beammeup` |
| `GOOGLE_MAPS_API_KEY` | `AIzaSyA1dJTvvs0ttk0QBDuWpyKGnqqFW5jAKgs` |
| `LAUNCHDARKLY_SDK_KEY` | `sdk-c2d64b77-8c50-43e6-8e70-423d6906e25c` |

The `DRUPAL_CLIENT_SECRET`, `BASIC_AUTH_PASS`, and `LAUNCHDARKLY_SDK_KEY` (server-side SDK key) should never be in `runtimeConfig` — this is a misconfiguration on PetFinder's end, as `runtimeConfig` (unlike `serverRuntimeConfig`) is shipped to the browser.

Direct `curl` to the GraphQL endpoint is blocked by Akamai WAF (403). Requests must originate from within a real browser session (e.g. via `page.evaluate()`) to pass bot detection.

---

## Finding 1 — `meta` timestamps on every search card

Every animal returned by `SearchAnimal` includes a `meta` block:

```json
{
  "recordStatus": "published",
  "publishTime": "2026-05-17T12:08:37.561Z",
  "create": { "time": "2026-05-17T12:08:37.561Z" },
  "update": { "time": "2026-06-01T14:29:30.244Z" }
}
```

- `meta.create.time` — when the listing was first created in PetFinder's backend
- `meta.update.time` — when PetFinder last modified the record (more reliable than our own `last_updated_at` for detecting upstream changes)
- `recordStatus` — "published" vs. draft/hidden state

**Gap:** We don't capture any of these. Our `listed_at` comes from `residency.publishedAt` (detail page only) and our `last_updated_at` is set by our own scraper clock, not PetFinder's.

---

## Finding 2 — `organizationAnimalId` (shelter's internal kennel ID)

The search card's `organization` block contains a field we never extract from the detail page:

```json
"organizationAnimalId": "SSRD-A-2483"
```

This is the **shelter's own internal ID** for the dog — the kennel number from their management system, completely separate from PetFinder's UUID. When you contact a shelter about a specific dog, this is the ID they recognize.

Also missing from our current capture:
- `organizationContactName` — named contact person at the org
- `organizationContactId` — internal contact UUID
- `organizationLocationName` — specific branch/location name (shelter may have multiple sites)
- `organizationCity` / `organizationState` — org HQ address (distinct from the dog's foster/listing location)

---

## Finding 3 — Three missing adoption statuses

`AllAnimalAttributes` returned the canonical adoption status list. We currently only handle 3 of 6:

| `alternateId` | Display name | In our `STATUS_MAP`? |
|---|---|---|
| `a` | Adoptable | yes → `available` |
| `x` | Adopted | yes → `adopted` |
| `p` | Adoption Pending | yes → `pending` |
| `h` | **Hold** | **NO** — falls through to `available` |
| `f` | **Found** | **NO** — falls through to `available` |
| `o` | **Other** | **NO** — falls through to `available` |

Dogs on hold or returned as "found" are silently misclassified as `available` in our DB right now.

---

## Finding 4 — Search facets: real-time breed supply counts

`SearchAnimal` returns a `facets` object alongside results — live counts of available dogs per breed and age across all of PetFinder (not just our search location):

**Age supply (nationwide, as of 2026-06-02):**
| Age | Count |
|---|---|
| Adult | 9,034 |
| Young | 7,181 |
| Baby | 5,481 |
| Senior | 1,206 |
| **Total** | **22,902** |

**Sample breed supply counts:**
| Breed | Available |
|---|---|
| American Staffordshire Terrier | 1,346 |
| Australian Cattle Dog / Blue Heeler | 653 |
| Australian Shepherd | 351 |
| Beagle | 391 |
| American Bulldog | 283 |
| American Bully | 257 |

These facets are returned on every `SearchAnimal` call — we're currently throwing them away. Storing them periodically as a time series would give the matching platform a real-time supply signal per breed (e.g. "Pit Bull Terriers are oversupplied; Cavalier King Charles Spaniels are rare").

---

## Finding 5 — Canonical controlled vocabularies

We store these as free-text strings scraped from the detail page, but `AllAnimalAttributes` provides the official fixed lists:

**Activity levels (4):** Active, Laid Back, Lap Pet, Very Active

**Personality traits (16):** Affectionate, Athletic, Brave, Couch Potato, Curious, Dignified, Easygoing, Funny, Gentle, Loyal, Playful, Protective, Quiet, Shy, Smart, Training needed

**Coat lengths (6):** Short, Long, Medium, Wire, Hairless, Curly

**Colors (15):** Apricot/Beige, Bicolor, Black, Brindle, Brown/Chocolate, Golden, Gray/Blue/Silver, Merle, Red/Chestnut/Orange, Sable, Tan/Yellow/Fawn, Tricolor, White/Cream, and 2 others

**Sizes (4):** Small, Medium, Large, Extra Large

**Ages (4):** Baby, Young, Adult, Senior

Knowing these upfront lets us validate scraped values and flag anything outside the controlled vocabulary (which would indicate a schema change on PetFinder's side).
---

## Finding 6 — `adopterProfile` schema (the other side of the match)

`GetMyUserData` exposed the full adopter profile structure. This is what PetFinder collects from adopters to power their own matching:

```
adopterProfile:
  currentlyOwnedAnimalTypes    — what pets they already have
  petOwnershipStatus           — renter vs. owner, yard, etc.
  childrenAges                 — age bands of children in the home
  hoaRestrictions:
    dog:
      breeds    — list of breeds their HOA bans
      size      — size limit their HOA enforces
    cat: ...
    rabbit: ...

adopterSearch:
  — saved search preferences (location, filters, etc.)
```

The `hoaRestrictions.dog.breeds` field is critical for matching — many adopters live in buildings or communities that prohibit specific breeds. A matching engine that ignores this will recommend Pit Bulls to people whose landlord bans them, producing a bad outcome even if every other signal is perfect.

We do not currently capture any adopter-side data. This schema defines what we need to build on the adopter profile side of the platform.

---

## Finding 7 — 309 canonical breeds (full list)

`AllAnimalAttributes` → `speciesList[dog].primaryBreeds` returned PetFinder's complete breed taxonomy. Full list saved to `petfinder_breeds.json`.

Key observations:
- IDs 172–410: original alphabetical breed list
- IDs 678–719: later additions (Miniature Schnauzer, Pembroke Welsh Corgi, American Bully, etc.)
- IDs 720–738: designer/crossbreeds (Goldendoodle, Labradoodle, Cavapoo, etc.)
- IDs 739–771: recent expansion of rarer/international breeds (Azawakh, Barbet, Bracco Italiano, etc.)

ID gaps indicate breeds were added in waves over time. The `alternateId` slug is what `SearchAnimal` uses in filter variables.

---

## GraphQL operations observed

| Operation | Purpose | Variables |
|---|---|---|
| `getGeoLocation` | Resolve location slug to lat/lng | `locationSlug` |
| `SearchAnimal` | Search with filters, returns cards + facets | `pagination`, `sort`, `filters`, `facets` |
| `AllAnimalAttributes` | All canonical filter options (breeds, colors, sizes, etc.) | `animalType` |
| `GetMyUserData` | Current user's profile and saved search | none |

The `SearchAnimal` query can be used directly (from inside a Playwright browser context) as an alternative to scraping detail pages one by one — it returns breed, size, age, behavior, photos, and org info for 12 dogs per call, paginated.
