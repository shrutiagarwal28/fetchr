"""
PetFinderExploreScraper — discovers dogs via PetFinder's GraphQL SearchAnimal API.

Responsibilities:
  - Fire paginated SearchAnimal queries from inside a real browser session
    (required to pass Akamai WAF — plain curl/requests are blocked)
  - Upsert card-level fields into dog_profiles via upsert_card()
  - Queue new and updated dogs in urls_to_visit for the detail scraper
  - Write breed + age supply counts to breed_supply_snapshots (first page only)

What this scraper does NOT do:
  - Visit individual detail pages
  - Populate detail-page-only fields (extended_description, full org bio, etc.)

Why GraphQL instead of page scraping:
  - 20 dogs per call vs 1 page load per dog — ~20x fewer requests
  - Returns org_animal_id and meta timestamps not available in __NEXT_DATA__
  - Facets give us nationwide breed supply counts as a free side-effect

Akamai note:
  All GraphQL calls are fired via page.evaluate() from inside a Chromium process
  that navigated to petfinder.com first. Direct curl/requests get a 403.

CLI:
  python3 main.py explore --source petfinder --max 200 --location nj/jersey-city
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

from playwright.sync_api import Page

from db.connection import (
    Session,
    dequeue_detail_visit,
    queue_for_detail_visit,
    save_raw_scrape,
    upsert_card,
)
from models.dog import DogProfile
from models.reference import BreedSupplySnapshotORM
from scrapers.base import BaseScraper
from scrapers.petfinder import (
    BASE_URL,
    STATUS_MAP,
    _build_start_url,
    _extract_source_id,
    _normalize_age,
    _normalize_size,
    _parse_iso_dt,
    _yn_to_bool,
)

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://psl.petfinder.com/graphql"
PAGE_SIZE = 20

_CLIENT_ID = os.environ["PETFINDER_CLIENT_ID"]
_CLIENT_SECRET = os.environ["PETFINDER_CLIENT_SECRET"]

# GraphQL query sent from inside the browser via page.evaluate().
# Requests only the fields available on the search card — not detail-page fields.
_SEARCH_ANIMAL_QUERY = """
query SearchAnimal(
  $pagination: PaginationInfoInput!
  $sort: [SortInput!]!
  $filters: AnimalSearchFiltersInput!
  $facets: AnimalSearchFacetsInput
) {
  searchAnimal(
    pagination: $pagination
    sort: $sort
    filters: $filters
    facets: $facets
    isConsumer: true
  ) {
    totalCount
    timedOut
    animals {
      animalId
      animalName
      animalType
      matchLabel
      outOfTown
      behavior {
        activityLevel
        interactionsOtherAnimals
        houseTrained
        requiresFencedYard
        knowsBasicCommands
        personalityTraits
        interactions {
          cats
          dogs
          otherAnimals
          childrenUnder8
          children8AndUp
        }
      }
      publicUrl { url }
      sponsorAPetUrl { url }
      residency {
        adoptionStatus
        adoptionDate
        adoptionFee
        adoptionFeeWaived
      }
      organization {
        organizationId
        organizationAnimalId
        organizationDisplayId
        organizationName
        organizationCity
        organizationState
        organizationLocationName
      }
      physical {
        birthDate
        coatLength
        declawed
        sex
        species
        spayedNeutered
        specialNeeds
        specialNeedsNotes
        vaccinated
        breed { primary secondary mixed }
        color { primary secondary tertiary }
        size { label range { min max label } }
        age { value label rangeLabel }
      }
      _media {
        animalId
        mediaId
        mimeType
        mediaFormat
        mediaStatus
        publicUrl
        originalFilename
        position
        thumbnailUrl
        mediaIndex
      }
      meta {
        recordStatus
        publishTime
        create { time }
        update { time }
      }
    }
    facets {
      breeds { buckets { name total } }
      age { buckets { name total } }
    }
  }
}
"""


class PetFinderExploreScraper(BaseScraper):
    SOURCE_NAME = "petfinder"

    def _scrape(self, page: Page) -> None:
        counts = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        start_url = _build_start_url(self.location)

        logger.info("Loading listing page to establish browser session: %s", start_url)
        page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3000)

        location_slug = self._build_location_slug()
        total_available = None
        facets_saved = False
        from_page = 0

        with Session() as session:
            while counts["fetched"] < self.max_results:
                remaining = self.max_results - counts["fetched"]
                page_size = min(PAGE_SIZE, remaining)

                logger.info(
                    "Firing SearchAnimal page %d (fetched so far: %d / %d)",
                    from_page, counts["fetched"], self.max_results,
                )

                result = self._fire_search_query(
                    page,
                    from_page=from_page,
                    page_size=page_size,
                    location_slug=location_slug,
                    include_facets=not facets_saved,
                )

                if not result or "data" not in result:
                    logger.error("Empty or error response from SearchAnimal: %s", result)
                    break

                search_data = result["data"].get("searchAnimal", {})
                animals = search_data.get("animals") or []
                facets = search_data.get("facets") or {}

                if total_available is None:
                    total_available = search_data.get("totalCount", 0)
                    logger.info("PetFinder reports %d total dogs available", total_available)

                if not animals:
                    logger.info("No animals returned on page %d — reached end of results", from_page)
                    break

                # Save breed supply snapshot from the first page's facets
                if not facets_saved and facets:
                    self._save_supply_snapshot(session, facets)
                    facets_saved = True

                for animal in animals:
                    try:
                        profile = self._parse_card(animal)
                        if profile is None:
                            counts["errors"] += 1
                            continue

                        # Check if dog is new or updated to decide queue reason
                        from models.dog import DogORM
                        existing = session.query(DogORM).filter_by(
                            source=self.SOURCE_NAME, source_id=profile.source_id
                        ).first()

                        is_new = existing is None
                        is_updated = (
                            not is_new
                            and existing.detail_scraped_at is not None
                            and profile.petfinder_updated_at is not None
                            and profile.petfinder_updated_at > existing.detail_scraped_at
                        )
                        needs_detail = is_new or is_updated or (
                            not is_new and existing.detail_scraped_at is None
                        )

                        action = upsert_card(session, profile)
                        counts[action] += 1
                        counts["fetched"] += 1

                        if needs_detail:
                            reason = "new" if is_new else "updated"
                            queue_for_detail_visit(
                                session,
                                source=self.SOURCE_NAME,
                                source_id=profile.source_id,
                                source_url=profile.source_url,
                                reason=reason,
                            )
                            session.commit()

                        logger.info(
                            "Card %d: %s (%s) [%s] %s",
                            counts["fetched"],
                            profile.name,
                            profile.breed_primary,
                            action,
                            "→ queued" if needs_detail else "",
                        )

                    except Exception:
                        logger.exception("Error processing animal card: %s", animal.get("animalId"))
                        counts["errors"] += 1

                from_page += 1

                # Stop if PetFinder has no more results
                if len(animals) < page_size:
                    logger.info("Last page reached (returned %d < %d requested)", len(animals), page_size)
                    break

                if counts["fetched"] < self.max_results:
                    self._random_delay(low=1.0, high=2.5)

        logger.info(
            "Done. fetched=%d created=%d updated=%d unchanged=%d skipped=%d errors=%d",
            counts["fetched"], counts["created"], counts["updated"],
            counts["unchanged"], counts["skipped"], counts["errors"],
        )

    def _build_location_slug(self) -> str:
        """Convert 'nj/jersey-city' env var format to PetFinder's 'us/nj/jerseycity' slug."""
        parts = self.location.strip("/").split("/", 1)
        state = parts[0].strip().lower()
        city = parts[1].strip().lower().replace(" ", "").replace("-", "") if len(parts) > 1 else ""
        return f"us/{state}/{city}"

    def _fire_search_query(
        self,
        page: Page,
        from_page: int,
        page_size: int,
        location_slug: str,
        include_facets: bool,
    ) -> Optional[dict[str, Any]]:
        """
        Execute SearchAnimal GraphQL from inside the browser via page.evaluate().
        Running inside Chromium means the request carries real browser cookies and
        TLS fingerprint — Akamai WAF lets it through.
        """
        variables = {
            "pagination": {"fromPage": from_page, "pageSize": page_size},
            "sort": [{"field": "distance", "order": "ASC"}],
            "filters": {
                "animalType": "Dog",
                "locationSlug": location_slug,
            },
        }

        if include_facets:
            variables["facets"] = {
                "breeds": {"size": 500},
                "age": {"size": 10},
            }

        query_payload = json.dumps({
            "query": _SEARCH_ANIMAL_QUERY,
            "variables": variables,
        })

        try:
            result = page.evaluate(f"""
                async () => {{
                    const res = await fetch('{GRAPHQL_URL}', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json',
                            'x-client-id': '{_CLIENT_ID}',
                            'x-client-secret': '{_CLIENT_SECRET}',
                        }},
                        body: {json.dumps(query_payload)},
                    }});
                    return await res.json();
                }}
            """)
            return result
        except Exception:
            logger.exception("GraphQL fetch failed on page %d", from_page)
            return None

    def _parse_card(self, animal: dict) -> Optional[DogProfile]:
        """Build a DogProfile from a SearchAnimal card response."""
        source_id = animal.get("animalId")
        name = (animal.get("animalName") or "").strip()
        if not source_id or not name:
            logger.warning("Card missing animalId or animalName — skipping: %s", animal)
            return None

        public_url_obj: dict = animal.get("publicUrl") or {}
        relative_url = public_url_obj.get("url") or ""
        source_url = urljoin(BASE_URL + "/", relative_url) if relative_url else f"{BASE_URL}/dog/{source_id}/"

        physical: dict = animal.get("physical") or {}

        breed: dict = physical.get("breed") or {}
        breed_primary = breed.get("primary") or "Unknown"
        breed_secondary = breed.get("secondary") or None
        is_mixed = bool(breed.get("mixed") or False)

        age_obj: dict = physical.get("age") or {}
        age_raw = age_obj.get("value") or ""
        age_category, age_years_approx = _normalize_age(age_raw)

        size_obj: dict = physical.get("size") or {}
        size = _normalize_size(size_obj.get("label") or "")
        size_range: dict = size_obj.get("range") or {}

        gender = (physical.get("sex") or "unknown").lower()

        color_obj: dict = physical.get("color") or {}

        behavior: dict = animal.get("behavior") or {}
        interactions: dict = behavior.get("interactions") or {}
        kids_u8 = _yn_to_bool(interactions.get("childrenUnder8"))
        kids_8up = _yn_to_bool(interactions.get("children8AndUp"))
        if kids_u8 is True or kids_8up is True:
            good_with_kids: Optional[bool] = True
        elif kids_u8 is False or kids_8up is False:
            good_with_kids = False
        else:
            good_with_kids = None

        org: dict = animal.get("organization") or {}

        residency: dict = animal.get("residency") or {}
        raw_status = (residency.get("adoptionStatus") or "").lower()
        status = STATUS_MAP.get(raw_status)
        if status is None:
            logger.warning("Unrecognized adoptionStatus %r for %s — storing raw value.", raw_status, source_id)
            status = raw_status or "unknown"

        media_list: list[dict] = animal.get("_media") or []
        photos = [
            "https://" + m["publicUrl"]
            for m in media_list
            if m.get("mimeType", "").startswith("image/") and m.get("publicUrl")
        ]
        media_records = [
            {
                "animal_id": m.get("animalId"),
                "media_id": m.get("mediaId"),
                "mime_type": m.get("mimeType"),
                "media_format": m.get("mediaFormat"),
                "media_status": m.get("mediaStatus"),
                "public_url": ("https://" + m["publicUrl"]) if m.get("publicUrl") else None,
                "original_filename": m.get("originalFilename"),
                "position": m.get("position"),
                "thumbnail_url": m.get("thumbnailUrl"),
                "media_index": m.get("mediaIndex"),
            }
            for m in media_list
        ]

        meta: dict = animal.get("meta") or {}
        sponsor_url_obj: dict = animal.get("sponsorAPetUrl") or {}

        return DogProfile(
            source=self.SOURCE_NAME,
            source_id=source_id,
            source_url=source_url,
            name=name,
            animal_type=animal.get("animalType") or None,
            match_label=animal.get("matchLabel") or None,
            out_of_town=animal.get("outOfTown"),
            # Physical
            breed_primary=breed_primary,
            breed_secondary=breed_secondary,
            is_mixed=is_mixed,
            age_category=age_category,
            age_years_approx=age_years_approx,
            age_label=age_obj.get("label") or None,
            age_range_label=age_obj.get("rangeLabel") or None,
            size=size,
            weight_min=size_range.get("min"),
            weight_max=size_range.get("max"),
            weight_range_label=size_range.get("label") or None,
            gender=gender,
            color=color_obj.get("primary") or None,
            color_secondary=color_obj.get("secondary") or None,
            color_tertiary=color_obj.get("tertiary") or None,
            coat_length=physical.get("coatLength") or None,
            declawed=physical.get("declawed"),
            species=physical.get("species") or None,
            spayed_neutered=physical.get("spayedNeutered"),
            vaccinated=physical.get("vaccinated"),
            special_needs=bool(physical.get("specialNeeds")),
            special_needs_notes=physical.get("specialNeedsNotes") or None,
            birth_date=_parse_iso_dt(physical.get("birthDate")),
            # Behavior
            house_trained=_yn_to_bool(behavior.get("houseTrained")),
            activity_level=(behavior.get("activityLevel") or "").lower() or None,
            requires_fenced_yard=_yn_to_bool(behavior.get("requiresFencedYard")),
            knows_basic_commands=_yn_to_bool(behavior.get("knowsBasicCommands")),
            behavior_other_animals=behavior.get("interactionsOtherAnimals") or None,
            good_with_kids=good_with_kids,
            good_with_dogs=_yn_to_bool(interactions.get("dogs")),
            good_with_cats=_yn_to_bool(interactions.get("cats")),
            good_with_other_animals=_yn_to_bool(interactions.get("otherAnimals")),
            personality_traits=behavior.get("personalityTraits") or [],
            # Organization (card-level — does not include full org bio/mission)
            shelter_name=org.get("organizationName") or None,
            org_id=org.get("organizationId") or None,
            org_display_id=org.get("organizationDisplayId") or None,
            org_animal_id=org.get("organizationAnimalId") or None,
            # Media
            photos=photos,
            media_records=media_records,
            # Listing
            petfinder_url=relative_url or None,
            sponsor_a_pet_url=sponsor_url_obj.get("url") or None,
            # Status
            status=status,
            adoption_date=_parse_iso_dt(residency.get("adoptionDate")),
            adoption_fee=residency.get("adoptionFee"),
            adoption_fee_waived=residency.get("adoptionFeeWaived"),
            # PetFinder backend metadata
            record_status=meta.get("recordStatus") or None,
            petfinder_created_at=_parse_iso_dt((meta.get("create") or {}).get("time")),
            petfinder_updated_at=_parse_iso_dt((meta.get("update") or {}).get("time")),
        )

    def _save_supply_snapshot(self, session, facets: dict) -> None:
        """
        Batch-insert breed supply counts from SearchAnimal facets.
        Runs once per scraper invocation — facets reflect PetFinder-wide counts,
        not per-page counts, so one snapshot per run is correct.
        """
        breeds_facet = (facets.get("breeds") or {}).get("buckets") or []
        if not breeds_facet:
            logger.warning("No breed facets returned — supply snapshot skipped")
            return

        snapped_at = datetime.now(timezone.utc)
        rows = [
            BreedSupplySnapshotORM(
                id=str(uuid.uuid4()),
                snapped_at=snapped_at,
                breed_name=bucket["name"],
                breed_alt_id=bucket["name"].lower().replace(" ", "_").replace("/", "_"),
                count=bucket["total"],
            )
            for bucket in breeds_facet
            if bucket.get("name") and bucket.get("total") is not None
        ]

        session.bulk_save_objects(rows)
        session.commit()
        logger.info("Supply snapshot saved: %d breed counts at %s", len(rows), snapped_at.isoformat())
