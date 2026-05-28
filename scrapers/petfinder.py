"""
PetFinderScraper — scrapes dog adoption listings from PetFinder.

Data flow:
  listing page (scroll to load all cards)
    └─► collect detail page URLs from card hrefs
          └─► for each URL: navigate → parse __NEXT_DATA__ JSON → DogProfile → upsert

PetFinder is a Next.js app. Every page embeds all its data in a
<script id="__NEXT_DATA__"> tag as JSON. Parsing that JSON is far more reliable
than CSS selectors, which break whenever PetFinder redeploys.

Data lives at: __NEXT_DATA__.props.pageProps.animal
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PWTimeoutError
from sqlalchemy.orm import Session as SessionType

from db.connection import Session, save_raw_scrape, upsert_dog
from models.dog import DogProfile
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.petfinder.com"

# Listing page card selectors — tried in order, first match wins.
# The href pattern is the most stable since PetFinder dog URLs always contain /dog/
CARD_SELECTORS = [
    "a[data-test='petCard']",
    "a[class*='petCard']",
    "a[href*='/dog/']",
]

# Map PetFinder's adoption status labels → our enum
STATUS_MAP = {
    "adoptable": "available",
    "pending": "pending",
    "adopted": "adopted",
}


def _build_start_url(location: str) -> str:
    """
    Build the PetFinder dog-search URL from a '{state}/{city}' slug.

    Accepts loose input — 'NJ/Jersey City', 'nj/jersey-city', 'nj/jerseycity'
    all produce the same URL. Spaces and hyphens are stripped from the city
    segment because PetFinder URLs use compact lowercase slugs (e.g. 'jerseycity').
    """
    parts = location.strip("/").split("/", 1)
    if len(parts) != 2:
        raise ValueError(
            f"--location must be '{{state}}/{{city}}', e.g. 'nj/jersey-city'. Got: {location!r}"
        )
    state = parts[0].strip().lower()
    city = parts[1].strip().lower().replace(" ", "").replace("-", "")
    return f"{BASE_URL}/search/dogs-for-adoption/us/{state}/{city}/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_dt(raw: Optional[str]) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string from PetFinder into a naive UTC datetime.
    Handles both 'Z' and '+00:00' suffixes. Returns None if raw is absent or unparseable.
    Stored as naive UTC to stay consistent with first_seen_at / last_updated_at.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        logger.warning("Could not parse datetime: %s", raw)
        return None


def _yn_to_bool(val: Optional[str]) -> Optional[bool]:
    """Convert PetFinder's Yes/No/Unknown strings to Python bool or None."""
    if val is None:
        return None
    v = val.strip().lower()
    if v == "yes":
        return True
    if v == "no":
        return False
    return None  # "Unknown" → None, not False


def _normalize_age(raw: str) -> tuple[str, Optional[float]]:
    """Map PetFinder age labels to our enum and an approximate year count."""
    raw = raw.strip().lower()

    months_match = re.search(r"(\d+)\s*month", raw)
    years_match = re.search(r"(\d+)\s*year", raw)

    if months_match:
        months = int(months_match.group(1))
        approx = round(months / 12, 1)
        return ("puppy" if months < 12 else "young" if months < 36 else "adult"), approx

    if years_match:
        years = int(years_match.group(1))
        return ("young" if years < 2 else "adult" if years < 8 else "senior"), float(years)

    if "baby" in raw or "puppy" in raw:
        return "puppy", None
    if "young" in raw:
        return "young", None
    if "senior" in raw:
        return "senior", None

    if not raw:
        return "unknown", None

    # Pass the raw value through so no data is silently dropped
    logger.warning("Unrecognized age string %r — storing as-is", raw)
    return raw, None


def _normalize_size(raw: str) -> str:
    """Collapse PetFinder's size labels to our enum."""
    raw = raw.strip().lower()
    if "extra" in raw or "xl" in raw:
        return "xlarge"
    if "large" in raw:
        return "large"
    if "small" in raw:
        return "small"
    return "medium"


def _extract_source_id(url: str) -> str:
    """
    Pull the UUID from the PetFinder URL slug.
    e.g. /dog/violet-08a24224-d920-4dec-9ab7-4dd45c0c1784/nj/... → the UUID
    Falls back to the last path segment if no UUID is found.
    """
    match = re.search(
        r"/dog/[^/]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url,
    )
    if match:
        return match.group(1)
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else url


def _parse_next_data(page: Page) -> Optional[dict[str, Any]]:
    """
    Read and parse the __NEXT_DATA__ JSON blob that Next.js embeds in every page.
    Returns the animal dict at props.pageProps.animal, or None if not found.

    We run a tiny JS snippet inside the browser to grab the script tag's text,
    then parse it in Python — no CSS selectors needed.
    """
    try:
        content = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent : null; }"
        )
        if not content:
            return None
        data = json.loads(content)
        return data.get("props", {}).get("pageProps", {}).get("animal")
    except Exception:
        logger.exception("Failed to parse __NEXT_DATA__")
        return None


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class PetFinderScraper(BaseScraper):
    SOURCE_NAME = "petfinder"

    def _scrape(self, page: Page) -> None:
        counts = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        start_url = _build_start_url(self.location)
        logger.info("Loading listing page: %s", start_url)
        page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)

        card_urls = self._collect_card_urls(page, start_url)
        card_urls = card_urls[: self.max_results]
        total = len(card_urls)
        logger.info("Found %d dog cards to scrape", total)

        with Session() as session:
            for idx, detail_url in enumerate(card_urls, start=1):
                try:
                    profile = self._scrape_detail_page(page, detail_url, session)
                    if profile is None:
                        counts["errors"] += 1
                        continue

                    action = upsert_dog(session, profile)
                    counts[action] += 1
                    counts["fetched"] += 1

                    logger.info(
                        "Scraped dog %d/%d: %s (%s, %s) [%s]",
                        idx,
                        total,
                        profile.name,
                        profile.breed_primary,
                        profile.city or "unknown city",
                        action,
                    )
                except Exception:
                    logger.exception("Unhandled error on %s", detail_url)
                    counts["errors"] += 1

                if idx < total:
                    self._random_delay()

        logger.info(
            "Done. fetched=%d created=%d updated=%d unchanged=%d skipped=%d errors=%d",
            counts["fetched"],
            counts["created"],
            counts["updated"],
            counts["unchanged"],
            counts["skipped"],
            counts["errors"],
        )

    def _resolve_card_selector(self, page: Page) -> str:
        for selector in CARD_SELECTORS:
            if page.query_selector(selector):
                logger.debug("Card selector: %s", selector)
                return selector
        logger.warning("No card selector matched — saving debug_listing.png")
        page.screenshot(path="debug_listing.png")
        return CARD_SELECTORS[-1]

    def _collect_card_urls(self, page: Page, start_url: str) -> list[str]:
        """
        Paginate through listing pages until max_results URLs are collected or
        a page yields no new cards (signals the last page).

        PetFinder uses ?page=N for pagination. Page 1 is already loaded by the
        caller, so we only navigate for page 2+.
        """
        urls: list[str] = []
        seen: set[str] = set()
        page_num = 1
        # Safety cap: prevents an infinite loop if max_results is very large
        # and PetFinder somehow keeps returning cards. 20 pages × ~20 cards ≈ 400 dogs.
        MAX_PAGES = 20

        while len(urls) < self.max_results and page_num <= MAX_PAGES:
            if page_num > 1:
                next_page_url = f"{start_url}?page={page_num}"
                logger.info("Navigating to listing page %d", page_num)
                page.goto(next_page_url, wait_until="domcontentloaded", timeout=60_000)

            remaining = self.max_results - len(urls)
            new_urls = self._collect_cards_on_page(page, seen, remaining)

            if not new_urls:
                # Empty page means we've gone past the last results page
                logger.info("No new cards on page %d — reached last listing page", page_num)
                break

            urls.extend(new_urls)
            logger.info(
                "Listing page %d: %d new cards (total collected: %d / %d)",
                page_num, len(new_urls), len(urls), self.max_results,
            )
            page_num += 1

            # Polite delay between page navigations (not needed after the last page)
            if len(urls) < self.max_results and page_num <= MAX_PAGES:
                self._random_delay()

        return urls

    def _collect_cards_on_page(
        self, page: Page, seen: set[str], remaining: int
    ) -> list[str]:
        """
        Scroll the current listing page until cards stabilize or `remaining` is hit,
        then return only URLs not yet in `seen`. Updates `seen` in-place.

        `remaining` is passed in (rather than using self.max_results directly) so that
        the scroll-stop condition accounts for cards already collected on prior pages.
        """
        page.wait_for_timeout(3000)
        card_selector = self._resolve_card_selector(page)

        prev_count = 0
        stable_rounds = 0

        while stable_rounds < 3:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2500)
            current_count = len(page.query_selector_all(card_selector))

            # Stop scrolling early once we have enough candidates visible on this page
            if current_count >= remaining:
                break
            if current_count == prev_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                logger.debug("Cards loading: %d → %d", prev_count, current_count)
            prev_count = current_count

        new_urls: list[str] = []
        for card in page.query_selector_all(card_selector):
            href = card.get_attribute("href") or ""
            if not href:
                continue
            full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
            if full_url not in seen:
                seen.add(full_url)
                new_urls.append(full_url)
        return new_urls

    def _scrape_detail_page(
        self, page: Page, url: str, session: SessionType
    ) -> Optional[DogProfile]:
        """
        Navigate to a dog detail page and build a DogProfile from __NEXT_DATA__ JSON.
        Returns None on hard failure so the caller can count the error without crashing.
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PWTimeoutError:
            logger.warning("Timeout: %s", url)
            return None

        animal = _parse_next_data(page)
        if animal is None:
            logger.warning("No __NEXT_DATA__ animal found at %s", url)
            return None

        # --- Identity + top-level fields ---
        source_id = animal.get("animalId") or _extract_source_id(url)
        name = (animal.get("animalName") or "").strip()
        if not name:
            logger.warning("No name at %s — skipping", url)
            return None
        animal_type = animal.get("animalType") or None
        microchip_id = animal.get("microchipId") or None
        internal_notes = animal.get("internalNotes") or None
        match_label = animal.get("matchLabel") or None
        out_of_town = animal.get("outOfTown")
        import_updates_enabled = animal.get("importUpdatesEnabled")
        import_deletes_enabled = animal.get("importDeletesEnabled")

        # Save raw blob before any normalization — insurance against scraper bugs.
        # Failure here is logged but never raises so the upsert path is unaffected.
        save_raw_scrape(session, self.SOURCE_NAME, source_id, url, animal)

        # --- Physical (animal.physical) ---
        physical: dict = animal.get("physical") or {}

        breed: dict = physical.get("breed") or {}
        breed_primary = breed.get("primary") or "Unknown"
        breed_secondary = breed.get("secondary") or None
        is_mixed = bool(breed.get("mixed", False))

        age_obj: dict = physical.get("age") or {}
        age_raw = age_obj.get("value") or age_obj.get("rangeLabel") or ""
        age_category, age_years_approx = _normalize_age(age_raw)
        age_label = age_obj.get("label") or None
        age_range_label = age_obj.get("rangeLabel") or None

        size_obj: dict = physical.get("size") or {}
        size = _normalize_size(size_obj.get("label") or "")
        size_range: dict = size_obj.get("range") or {}
        weight_min = size_range.get("min")
        weight_max = size_range.get("max")
        weight_range_label = size_range.get("label") or None

        gender = (physical.get("sex") or "unknown").lower()
        species = physical.get("species") or None
        declawed = physical.get("declawed")

        color_obj: dict = physical.get("color") or {}
        color = color_obj.get("primary") or None
        color_secondary = color_obj.get("secondary") or None
        color_tertiary = color_obj.get("tertiary") or None

        coat_length = physical.get("coatLength") or None
        spayed_neutered = physical.get("spayedNeutered")
        vaccinated = physical.get("vaccinated")
        special_needs = bool(physical.get("specialNeeds"))
        special_needs_notes = physical.get("specialNeedsNotes") or None
        birth_date = _parse_iso_dt(physical.get("birthDate"))

        # --- Behavior (animal.behavior) ---
        behavior: dict = animal.get("behavior") or {}
        house_trained = _yn_to_bool(behavior.get("houseTrained"))
        activity_level = (behavior.get("activityLevel") or "").strip().lower() or None
        requires_fenced_yard = _yn_to_bool(behavior.get("requiresFencedYard"))
        knows_basic_commands = _yn_to_bool(behavior.get("knowsBasicCommands"))
        behavior_other_animals = behavior.get("interactionsOtherAnimals") or None
        personality_traits: list[str] = behavior.get("personalityTraits") or []

        interactions: dict = behavior.get("interactions") or {}
        good_with_dogs = _yn_to_bool(interactions.get("dogs"))
        good_with_cats = _yn_to_bool(interactions.get("cats"))
        good_with_other_animals = _yn_to_bool(interactions.get("otherAnimals"))

        # PetFinder splits children into two age bands — treat either "Yes" as True
        kids_u8 = _yn_to_bool(interactions.get("childrenUnder8"))
        kids_8up = _yn_to_bool(interactions.get("children8AndUp"))
        if kids_u8 is True or kids_8up is True:
            good_with_kids: Optional[bool] = True
        elif kids_u8 is False or kids_8up is False:
            good_with_kids = False
        else:
            good_with_kids = None

        # --- Location — foster/listing address, not org HQ (animal._location) ---
        location_obj: dict = animal.get("_location") or {}
        location: dict = location_obj.get("address") or {}
        location_id = location_obj.get("locationId") or None
        location_name = location_obj.get("locationName") or None
        location_type = location_obj.get("locationType") or None
        location_contact_name = location_obj.get("contactName") or None
        location_email = location_obj.get("email") or None
        location_phone = (location_obj.get("phone") or "").strip() or None
        is_appt_only = location_obj.get("isApptOnly")
        is_map_hidden = location_obj.get("isMapHidden")
        is_public_location = location_obj.get("isPublic")
        private_address = location_obj.get("privateAddress")
        location_street = location.get("street") or None
        location_street2 = location.get("street2") or None
        city = location.get("city") or None
        state = location.get("state") or None
        zip_code = location.get("postalCode") or None
        country = location.get("country") or None
        lat = location.get("latitude")
        lng = location.get("longitude")

        # --- Organization (animal._organization) ---
        org: dict = animal.get("_organization") or {}
        shelter_name = org.get("organizationName") or None
        org_id = org.get("organizationId") or None
        org_type = org.get("organizationType") or None
        org_custom_url_alias = org.get("customUrlAlias") or None
        org_website = org.get("website") or None
        org_social_urls: list[str] = org.get("socialUrl") or []
        org_mission_statement = org.get("missionStatement") or None
        org_onsite_vet = org.get("onsiteVet")
        org_supports_rehome = org.get("supportsRehome")
        org_spay_neuter_policy = org.get("spayNeuterPolicy") or None
        org_special_services: list[str] = org.get("specialServices") or []
        org_adoption: dict = org.get("adoption") or {}
        org_adoption_url = org_adoption.get("adoptionApplUrl") or None
        org_adoption_fee_min = org_adoption.get("adoptionFeeMin")
        org_adoption_fee_max = org_adoption.get("adoptionFeeMax")
        org_annual_adoptions = org_adoption.get("annualAdoptions")
        org_annual_intake = org_adoption.get("annualIntake")
        org_foster_count = org.get("fosterCount")
        org_employee_count = org.get("employeeCount")
        org_volunteer_count = org.get("volunteerCount")
        org_display_id = org.get("displayId") or None

        # --- Contact (animal._contact) ---
        contact: dict = animal.get("_contact") or {}
        contact_id = contact.get("contactId") or None
        contact_email = contact.get("email") or None
        contact_first_name = contact.get("firstName") or None
        contact_last_name = contact.get("lastName") or None
        contact_phone = contact.get("phone") or None

        # --- Media (animal._media) ---
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
                "original_url": m.get("originalUrl"),
                "s3_url": m.get("s3Url"),
                "s3_uri": m.get("s3Uri"),
                "original_filename": m.get("originalFilename"),
                "position": m.get("position"),
                "media_url": m.get("mediaUrl"),
                "thumbnail_url": m.get("thumbnailUrl"),
                "media_index": m.get("mediaIndex"),
            }
            for m in media_list
        ]

        # --- Listing content ---
        description = (animal.get("description") or "").strip() or None
        extended_description = (animal.get("extendedDescription") or "").strip() or None
        petfinder_notes = animal.get("notes") or None
        tags: list[str] = animal.get("tags") or []
        petfinder_url_obj: dict = animal.get("publicUrl") or {}
        petfinder_url = petfinder_url_obj.get("url") or None
        sponsor_url_obj: dict = animal.get("sponsorAPetUrl") or {}
        sponsor_a_pet_url = sponsor_url_obj.get("url") or None

        # --- Adoption / status (animal.residency) ---
        residency: dict = animal.get("residency") or {}
        raw_status = (residency.get("adoptionStatus") or "").lower()
        status = STATUS_MAP.get(raw_status, "available")
        adoption_fee = residency.get("adoptionFee")
        adoption_fee_waived = residency.get("adoptionFeeWaived")
        display_adoption_fee = residency.get("displayAdoptionFee")
        adoption_date = _parse_iso_dt(residency.get("adoptionDate"))
        adoption_status_change_date = _parse_iso_dt(residency.get("adoptionStatusChangeDate"))
        intake_date = _parse_iso_dt(residency.get("intakeDate"))
        intake_type = residency.get("intakeType") or None
        transfer_date = _parse_iso_dt(residency.get("transferDate"))
        transfer_from_org_id = residency.get("transferFromOrganizationId") or None
        listed_at = _parse_iso_dt(residency.get("publishedAt"))

        return DogProfile(
            source=self.SOURCE_NAME,
            source_id=source_id,
            source_url=url,
            name=name,
            animal_type=animal_type,
            microchip_id=microchip_id,
            internal_notes=internal_notes,
            match_label=match_label,
            out_of_town=out_of_town,
            import_updates_enabled=import_updates_enabled,
            import_deletes_enabled=import_deletes_enabled,
            breed_primary=breed_primary,
            breed_secondary=breed_secondary,
            is_mixed=is_mixed,
            age_category=age_category,
            age_years_approx=age_years_approx,
            age_label=age_label,
            age_range_label=age_range_label,
            size=size,
            weight_min=weight_min,
            weight_max=weight_max,
            weight_range_label=weight_range_label,
            gender=gender,
            species=species,
            declawed=declawed,
            color=color,
            color_secondary=color_secondary,
            color_tertiary=color_tertiary,
            coat_length=coat_length,
            spayed_neutered=spayed_neutered,
            vaccinated=vaccinated,
            special_needs=special_needs,
            special_needs_notes=special_needs_notes,
            birth_date=birth_date,
            house_trained=house_trained,
            activity_level=activity_level,
            requires_fenced_yard=requires_fenced_yard,
            knows_basic_commands=knows_basic_commands,
            behavior_other_animals=behavior_other_animals,
            good_with_kids=good_with_kids,
            good_with_dogs=good_with_dogs,
            good_with_cats=good_with_cats,
            good_with_other_animals=good_with_other_animals,
            personality_traits=personality_traits,
            location_id=location_id,
            location_name=location_name,
            location_type=location_type,
            location_contact_name=location_contact_name,
            location_email=location_email,
            location_phone=location_phone,
            is_appt_only=is_appt_only,
            is_map_hidden=is_map_hidden,
            is_public_location=is_public_location,
            private_address=private_address,
            location_street=location_street,
            location_street2=location_street2,
            city=city,
            state=state,
            zip=zip_code,
            country=country,
            lat=lat,
            lng=lng,
            shelter_name=shelter_name,
            org_id=org_id,
            org_type=org_type,
            org_custom_url_alias=org_custom_url_alias,
            org_website=org_website,
            org_social_urls=org_social_urls,
            org_mission_statement=org_mission_statement,
            org_onsite_vet=org_onsite_vet,
            org_supports_rehome=org_supports_rehome,
            org_spay_neuter_policy=org_spay_neuter_policy,
            org_special_services=org_special_services,
            org_adoption_url=org_adoption_url,
            org_adoption_fee_min=org_adoption_fee_min,
            org_adoption_fee_max=org_adoption_fee_max,
            org_annual_adoptions=org_annual_adoptions,
            org_annual_intake=org_annual_intake,
            org_foster_count=org_foster_count,
            org_employee_count=org_employee_count,
            org_volunteer_count=org_volunteer_count,
            org_display_id=org_display_id,
            contact_id=contact_id,
            contact_email=contact_email,
            contact_first_name=contact_first_name,
            contact_last_name=contact_last_name,
            contact_phone=contact_phone,
            photos=photos,
            media_records=media_records,
            description=description,
            extended_description=extended_description,
            petfinder_notes=petfinder_notes,
            tags=tags,
            petfinder_url=petfinder_url,
            sponsor_a_pet_url=sponsor_a_pet_url,
            status=status,
            adoption_fee=adoption_fee,
            adoption_fee_waived=adoption_fee_waived,
            display_adoption_fee=display_adoption_fee,
            adoption_date=adoption_date,
            adoption_status_change_date=adoption_status_change_date,
            intake_date=intake_date,
            intake_type=intake_type,
            transfer_date=transfer_date,
            transfer_from_org_id=transfer_from_org_id,
            listed_at=listed_at,
        )
