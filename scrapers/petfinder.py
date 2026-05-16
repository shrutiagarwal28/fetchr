"""
PetFinderScraper — scrapes dog adoption listings from PetFinder.

Data flow:
  listing page (scroll to load all cards)
    └─► for each card: extract card-level fields + detail URL
          └─► visit detail page → extract full profile
                └─► normalize → DogProfile → upsert_dog()

CSS selectors are isolated as module-level constants so a site redesign
only requires updating this section, not hunting through logic.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from db.connection import Session, upsert_dog
from models.dog import DogProfile
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors — update here if PetFinder redesigns their markup
# ---------------------------------------------------------------------------

# Listing page — ordered from most specific to most resilient fallback.
# PetFinder changes data-test attrs periodically; the href pattern is stable.
CARD_SELECTORS = [
    "a[data-test='petCard']",
    "a[class*='petCard']",
    "a[href*='/dog/']",         # URL-based fallback: any link to a dog profile
]
CARD_NAME_SELECTOR = "[data-test='pet-name']"
CARD_BREED_SELECTOR = "[data-test='pet-breed']"
CARD_AGE_SELECTOR = "[data-test='pet-age']"
CARD_GENDER_SELECTOR = "[data-test='pet-gender']"
CARD_SIZE_SELECTOR = "[data-test='pet-size']"
CARD_LOCATION_SELECTOR = "[data-test='petDistance']"

# Detail page
DETAIL_DESC_SELECTOR = "[data-test='Pet_Story_Section'] p"
DETAIL_TAGS_SELECTOR = "[data-test='attribute-chip']"
DETAIL_PHOTOS_SELECTOR = "img[data-test='pet-photo']"
DETAIL_SHELTER_SELECTOR = "[data-test='petCardOrganizationName']"
DETAIL_CHARACTERISTICS_SELECTOR = "[data-test='characteristic']"

GOOD_WITH_LABELS = {
    "children": "good_with_kids",
    "dogs": "good_with_dogs",
    "cats": "good_with_cats",
}

BASE_URL = "https://www.petfinder.com"
START_URL = "https://www.petfinder.com/search/dogs-for-adoption/us/nj/jerseycity/"


def _normalize_age(raw: str) -> tuple[str, Optional[float]]:
    """
    Map PetFinder's freeform age strings to our fixed enum + an approximate
    years value for sorting/filtering later.

    PetFinder uses "Baby", "Young", "Adult", "Senior" as primary buckets
    but sometimes shows "X years" or "X months" on detail pages.
    """
    raw = raw.strip().lower()

    # Explicit duration strings take precedence
    months_match = re.search(r"(\d+)\s*month", raw)
    years_match = re.search(r"(\d+)\s*year", raw)

    if months_match:
        months = int(months_match.group(1))
        approx = round(months / 12, 1)
        category = "puppy" if months < 12 else ("young" if months < 36 else "adult")
        return category, approx

    if years_match:
        years = int(years_match.group(1))
        if years < 2:
            category = "young"
        elif years < 8:
            category = "adult"
        else:
            category = "senior"
        return category, float(years)

    # Fall back to PetFinder's own bucket labels
    if "baby" in raw or "puppy" in raw:
        return "puppy", None
    if "young" in raw:
        return "young", None
    if "senior" in raw:
        return "senior", None

    return "adult", None  # safest default for unknown values


def _normalize_size(raw: str) -> str:
    """Collapse PetFinder's verbose size labels to our enum."""
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
    Parse the numeric dog ID from a PetFinder URL.
    URL pattern: /cat-dog-name/12345678/  or  /dog/name-slug/12345678/
    The last path segment before trailing slash is always the numeric ID.
    """
    parts = [p for p in url.rstrip("/").split("/") if p]
    return parts[-1] if parts else url


def _safe_text(page_or_element, selector: str, default: str = "") -> str:
    """Query a selector and return its trimmed text, or default if absent."""
    try:
        el = page_or_element.query_selector(selector)
        return el.inner_text().strip() if el else default
    except Exception:
        return default


def _safe_attr(page_or_element, selector: str, attr: str, default: str = "") -> str:
    try:
        el = page_or_element.query_selector(selector)
        return (el.get_attribute(attr) or default).strip() if el else default
    except Exception:
        return default


class PetFinderScraper(BaseScraper):
    SOURCE_NAME = "petfinder"

    def _scrape(self, page: Page) -> None:
        counts = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}

        logger.info("Loading listing page: %s", START_URL)
        page.goto(START_URL, wait_until="domcontentloaded", timeout=60_000)

        # Scroll until the card count stabilizes or we hit max_results.
        # PetFinder uses infinite scroll — cards load in batches as you scroll.
        card_urls = self._collect_card_urls(page)
        card_urls = card_urls[: self.max_results]
        total = len(card_urls)
        logger.info("Found %d dog cards to scrape", total)

        with Session() as session:
            for idx, detail_url in enumerate(card_urls, start=1):
                try:
                    profile = self._scrape_detail_page(page, detail_url, idx, total)
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
                    logger.exception("Unhandled error on detail page %s", detail_url)
                    counts["errors"] += 1

                # Polite delay — skip after the last dog to avoid a pointless wait
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
        """
        Try each selector in CARD_SELECTORS and return the first one that finds
        elements on the current page. Falls back to the last entry if none match.
        """
        for selector in CARD_SELECTORS:
            if page.query_selector(selector):
                logger.debug("Using card selector: %s", selector)
                return selector
        logger.warning(
            "No card selector matched — page may not have loaded correctly. "
            "Saving debug screenshot to debug_listing.png"
        )
        page.screenshot(path="debug_listing.png")
        # Return the URL-based fallback so the scroll loop at least tries
        return CARD_SELECTORS[-1]

    def _collect_card_urls(self, page: Page) -> list[str]:
        """
        Scroll the listing page until no new cards appear, collecting detail URLs.

        We use a stabilization loop rather than a fixed scroll count because
        PetFinder's batch size is unpredictable and can vary by location/time.
        """
        # Wait for initial content to render before attempting to find cards
        page.wait_for_timeout(3000)

        card_selector = self._resolve_card_selector(page)

        prev_count = 0
        stable_rounds = 0
        max_stable_rounds = 3  # stop after 3 consecutive scrolls with no new cards

        while stable_rounds < max_stable_rounds:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2500)

            cards = page.query_selector_all(card_selector)
            current_count = len(cards)

            if current_count >= self.max_results:
                logger.debug("Reached max_results cap (%d)", self.max_results)
                break

            if current_count == prev_count:
                stable_rounds += 1
                logger.debug(
                    "No new cards after scroll (%d/%d stable rounds)",
                    stable_rounds,
                    max_stable_rounds,
                )
            else:
                stable_rounds = 0
                logger.debug("Card count grew: %d → %d", prev_count, current_count)

            prev_count = current_count

        cards = page.query_selector_all(card_selector)
        urls: list[str] = []
        seen: set[str] = set()
        for card in cards:
            href = card.get_attribute("href") or ""
            if href:
                full_url = href if href.startswith("http") else urljoin(BASE_URL, href)
                # Deduplicate — the href fallback selector can match nav links too
                if full_url not in seen:
                    seen.add(full_url)
                    urls.append(full_url)

        return urls

    def _scrape_detail_page(
        self, page: Page, url: str, idx: int, total: int
    ) -> Optional[DogProfile]:
        """
        Navigate to a single dog detail page and extract a validated DogProfile.
        Returns None on hard failure so the caller can count errors without crashing.
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        except PWTimeoutError:
            logger.warning("Timeout loading detail page %s", url)
            return None

        source_id = _extract_source_id(url)

        # --- Name and breed (usually present in the page <h1>) ---
        name = _safe_text(page, "h1[data-test='pet-name']") or _safe_text(page, "h1")
        if not name:
            logger.warning("Could not extract name from %s — skipping", url)
            return None

        breed_raw = _safe_text(page, "[data-test='pet-breed-element']")
        if not breed_raw:
            breed_raw = _safe_text(page, "[data-test='pet-breed']")
        breed_primary, breed_secondary, is_mixed = self._parse_breed(breed_raw)

        # --- Age and size ---
        age_raw = _safe_text(page, "[data-test='pet-age']")
        age_category, age_years_approx = _normalize_age(age_raw)

        size_raw = _safe_text(page, "[data-test='pet-size']")
        size = _normalize_size(size_raw) if size_raw else "medium"

        gender_raw = _safe_text(page, "[data-test='pet-sex']")
        gender = gender_raw.lower() if gender_raw else "unknown"

        color = _safe_text(page, "[data-test='pet-color']") or None

        # --- Location and shelter ---
        shelter_name = _safe_text(page, DETAIL_SHELTER_SELECTOR) or None
        location_raw = _safe_text(page, "[data-test='pet-location-distance']")
        city, state, zip_code = self._parse_location(location_raw)

        # --- Compatibility attributes ---
        good_with_kids = self._extract_bool_attribute(page, "children")
        good_with_dogs = self._extract_bool_attribute(page, "dogs")
        good_with_cats = self._extract_bool_attribute(page, "cats")
        house_trained = self._extract_bool_attribute(page, "house trained")
        special_needs = self._has_attribute(page, "special needs")

        # --- Description ---
        desc_els = page.query_selector_all(DETAIL_DESC_SELECTOR)
        description = "\n\n".join(
            el.inner_text().strip() for el in desc_els if el.inner_text().strip()
        ) or None

        # --- Tags (personality chips) ---
        tag_els = page.query_selector_all(DETAIL_TAGS_SELECTOR)
        tags = [el.inner_text().strip() for el in tag_els if el.inner_text().strip()]

        # --- Photos ---
        photo_els = page.query_selector_all(DETAIL_PHOTOS_SELECTOR)
        photos = [
            el.get_attribute("src") or ""
            for el in photo_els
            if el.get_attribute("src")
        ]

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
        )

    @staticmethod
    def _parse_breed(raw: str) -> tuple[str, Optional[str], bool]:
        """
        Split "Labrador Retriever / Poodle Mix" into:
          breed_primary="Labrador Retriever", breed_secondary="Poodle", is_mixed=True

        PetFinder uses " / " as separator and appends " Mix" for crossbreeds.
        """
        if not raw:
            return "Unknown", None, False

        is_mixed = "mix" in raw.lower()
        # Strip trailing " Mix" label before splitting
        raw_clean = re.sub(r"\s+mix\s*$", "", raw, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in raw_clean.split("/") if p.strip()]

        breed_primary = parts[0] if parts else "Unknown"
        breed_secondary = parts[1] if len(parts) > 1 else None
        return breed_primary, breed_secondary, is_mixed

    @staticmethod
    def _parse_location(raw: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse "Jersey City, NJ 07302" → ("Jersey City", "NJ", "07302").
        The distance suffix (e.g. "· 2 miles away") is stripped first.
        """
        if not raw:
            return None, None, None

        # Strip distance info that sometimes appears after a dot separator
        raw = re.split(r"[·•]", raw)[0].strip()

        # Pattern: "City, ST 00000"
        match = re.match(r"^(.+?),\s*([A-Z]{2})\s*(\d{5})?$", raw.strip())
        if match:
            return match.group(1).strip(), match.group(2), match.group(3)

        # Partial match: "City, ST" with no zip
        match = re.match(r"^(.+?),\s*([A-Z]{2})$", raw.strip())
        if match:
            return match.group(1).strip(), match.group(2), None

        return raw or None, None, None

    @staticmethod
    def _extract_bool_attribute(page: Page, label: str) -> Optional[bool]:
        """
        PetFinder shows compatibility as "Good with children: Yes/No" text blocks.
        We scan all characteristic elements for a matching label and parse the value.
        """
        try:
            els = page.query_selector_all(DETAIL_CHARACTERISTICS_SELECTOR)
            for el in els:
                text = el.inner_text().strip().lower()
                if label.lower() in text:
                    if "yes" in text or "good" in text:
                        return True
                    if "no" in text:
                        return False
        except Exception:
            pass
        return None

    @staticmethod
    def _has_attribute(page: Page, label: str) -> bool:
        """Return True if a tag/chip containing 'label' text is present on the page."""
        try:
            els = page.query_selector_all(DETAIL_TAGS_SELECTOR)
            return any(label.lower() in el.inner_text().strip().lower() for el in els)
        except Exception:
            return False
