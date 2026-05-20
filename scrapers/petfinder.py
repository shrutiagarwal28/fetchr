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

from db.connection import Session, upsert_dog
from models.dog import DogProfile
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://www.petfinder.com"
START_URL = "https://www.petfinder.com/search/dogs-for-adoption/us/nj/jerseycity/"

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
    return "adult", None


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
        counts = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}

        logger.info("Loading listing page: %s", START_URL)
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60_000)

        card_urls = self._collect_card_urls(page)
        card_urls = card_urls[: self.max_results]
        total = len(card_urls)
        logger.info("Found %d dog cards to scrape", total)

        with Session() as session:
            for idx, detail_url in enumerate(card_urls, start=1):
                try:
                    profile = self._scrape_detail_page(page, detail_url)
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
            "Done. fetched=%d created=%d updated=%d skipped=%d errors=%d",
            counts["fetched"],
            counts["created"],
            counts["updated"],
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

    def _collect_card_urls(self, page: Page) -> list[str]:
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
                next_page_url = f"{START_URL}?page={page_num}"
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

    def _scrape_detail_page(self, page: Page, url: str) -> Optional[DogProfile]:
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

        logger.info("residency object: %s", animal.get("residency"))
        logger.info("physical.birthDate: %s", (animal.get("physical") or {}).get("birthDate"))

        # --- Identity ---
        source_id = animal.get("animalId") or _extract_source_id(url)
        name = (animal.get("animalName") or "").strip()
        if not name:
            logger.warning("No name at %s — skipping", url)
            return None

        # --- Physical attributes (animal.physical) ---
        physical: dict = animal.get("physical") or {}

        breed: dict = physical.get("breed") or {}
        breed_primary = breed.get("primary") or "Unknown"
        breed_secondary = breed.get("secondary") or None
        is_mixed = bool(breed.get("mixed", False))

        age_obj: dict = physical.get("age") or {}
        # "value" is the label ("Young"), "rangeLabel" is "(1-3 years)" — use both
        age_raw = age_obj.get("value") or age_obj.get("rangeLabel") or ""
        age_category, age_years_approx = _normalize_age(age_raw)

        size_obj: dict = physical.get("size") or {}
        size = _normalize_size(size_obj.get("label") or "")

        gender = (physical.get("sex") or "unknown").lower()

        color_obj: dict = physical.get("color") or {}
        color = color_obj.get("primary") or None

        special_needs = bool(physical.get("specialNeeds"))

        # --- Behavior / compatibility (animal.behavior) ---
        behavior: dict = animal.get("behavior") or {}
        house_trained = _yn_to_bool(behavior.get("houseTrained"))
        tags: list[str] = behavior.get("personalityTraits") or []

        interactions: dict = behavior.get("interactions") or {}
        good_with_dogs = _yn_to_bool(interactions.get("dogs"))
        good_with_cats = _yn_to_bool(interactions.get("cats"))

        # PetFinder splits children into two age bands — treat either "Yes" as True
        kids_u8 = _yn_to_bool(interactions.get("childrenUnder8"))
        kids_8up = _yn_to_bool(interactions.get("children8AndUp"))
        if kids_u8 is True or kids_8up is True:
            good_with_kids: Optional[bool] = True
        elif kids_u8 is False or kids_8up is False:
            good_with_kids = False
        else:
            good_with_kids = None

        # --- Location — foster/listing address, not org headquarters (animal._location) ---
        location: dict = (animal.get("_location") or {}).get("address") or {}
        city = location.get("city") or None
        state = location.get("state") or None
        zip_code = location.get("postalCode") or None

        # --- Shelter (animal._organization) ---
        org: dict = animal.get("_organization") or {}
        shelter_name = org.get("organizationName") or None

        # --- Photos — images only, skip mp4 videos (animal._media) ---
        media_list: list[dict] = animal.get("_media") or []
        photos = [
            "https://" + m["publicUrl"]
            for m in media_list
            if m.get("mimeType", "").startswith("image/") and m.get("publicUrl")
        ]

        # --- Description ---
        description = (animal.get("description") or "").strip() or None

        # --- Adoption status (animal.residency) ---
        residency: dict = animal.get("residency") or {}
        raw_status = (residency.get("adoptionStatus") or "").lower()
        status = STATUS_MAP.get(raw_status, "available")

        return DogProfile(
            source=self.SOURCE_NAME,
            source_id=source_id,
            source_url=url,
            name=name,
            breed_primary=breed_primary,
            breed_secondary=breed_secondary,
            is_mixed=is_mixed,
            age_category=age_category,
            age_years_approx=age_years_approx,
            size=size,
            gender=gender,
            color=color,
            good_with_kids=good_with_kids,
            good_with_dogs=good_with_dogs,
            good_with_cats=good_with_cats,
            house_trained=house_trained,
            special_needs=special_needs,
            shelter_name=shelter_name,
            city=city,
            state=state,
            zip=zip_code,
            photos=photos,
            description=description,
            tags=tags,
            status=status,
        )
