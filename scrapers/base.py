"""
BaseScraper — shared browser lifecycle for all scrapers.

Every concrete scraper inherits this and implements _scrape(page).
The run() method owns browser setup + teardown so subclasses never
deal with Playwright context management directly.

Design choice: sync Playwright API instead of async because this is a
single-threaded CLI tool. Async would add complexity with no throughput
benefit (we deliberately run one page at a time for anti-detection).
"""

from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import sync_playwright, Page

from config import USER_AGENT

logger = logging.getLogger(__name__)


class BaseScraper:
    START_URL: str = ""
    SOURCE_NAME: str = ""

    def __init__(self, max_results: int = 100, headless: bool = True, location: str = "") -> None:
        self.max_results = max_results
        self.headless = headless
        self.location = location

    def run(self) -> None:
        """Entry point: launch browser, delegate to _scrape()."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=self.headless,
                args=[
                    # Suppress automation flags that bot detectors look for
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=USER_AGENT,
                # Viewport matching a typical MacBook screen reduces bot signals
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()

            # playwright-stealth patches navigator properties that headless
            # Chrome exposes (webdriver flag, plugins list, etc.)
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
                logger.debug("Stealth applied to page")
            except ImportError:
                logger.warning(
                    "playwright-stealth not installed — browser may be detected. "
                    "Run: pip install playwright-stealth"
                )

            try:
                self._scrape(page)
            finally:
                browser.close()

    def _scrape(self, page: Page) -> None:
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _scrape()"
        )

    def _random_delay(self, low: float = 2.0, high: float = 5.0) -> None:
        """Polite delay between requests. Randomized to avoid rhythm detection."""
        delay = random.uniform(low, high)
        logger.debug("Waiting %.1fs", delay)
        time.sleep(delay)
