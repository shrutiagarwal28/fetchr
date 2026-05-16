"""
AdoptAPetScraper — stub. Not yet implemented.

TODO: Implement Adopt-a-Pet scraper.
  Start URL: https://www.adoptapet.com/pet-search?pet_type_id=1 (dogs)
  The site uses a paginated API rather than infinite scroll, so the
  _collect_card_urls approach will differ from PetFinderScraper.
  Refer to PetFinderScraper as a structural reference.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Page

from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class AdoptAPetScraper(BaseScraper):
    SOURCE_NAME = "adoptapet"

    def _scrape(self, page: Page) -> None:
        logger.warning(
            "Adopt-a-Pet scraper is not yet implemented. "
            "Run with --source petfinder to scrape live data."
        )
