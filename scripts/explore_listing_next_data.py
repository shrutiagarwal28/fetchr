"""
One-shot script: open the PetFinder listing page (non-headless),
dump the full __NEXT_DATA__ JSON blob to explore_listing_next_data.json.

Run from the project root:
    source .venv/bin/activate
    python3 explore_listing_next_data.py

The output file will contain the raw JSON — look for keys like:
  props.pageProps.filters  (breed dropdowns, filter options)
  props.pageProps.animals  (summary cards, if they appear here)
  anything else PetFinder embeds at the listing level
"""

import json
import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

LOCATION = os.getenv("PETFINDER_LOCATION", "nj/jersey-city")
state, city = LOCATION.strip("/").split("/", 1)
city_slug = city.strip().lower().replace(" ", "").replace("-", "")
LISTING_URL = f"https://www.petfinder.com/search/dogs-for-adoption/us/{state}/{city_slug}/"

OUTPUT_FILE = "explore_listing_next_data.json"


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()

        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
        except ImportError:
            pass

        print(f"Navigating to listing page: {LISTING_URL}")
        page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=60_000)

        # Give the page a moment to finish any JS-driven rendering
        page.wait_for_timeout(4000)

        raw = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent : null; }"
        )

        if not raw:
            print("ERROR: __NEXT_DATA__ not found on the listing page.")
            browser.close()
            return

        data = json.loads(raw)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"Saved full __NEXT_DATA__ to {OUTPUT_FILE}  ({len(raw):,} bytes)")

        # Print the top-level keys so we know where to look
        print("\nTop-level keys in __NEXT_DATA__:")
        for key in data:
            print(f"  {key}")

        props = data.get("props", {})
        page_props = props.get("pageProps", {})
        print("\nKeys inside props.pageProps:")
        for key in page_props:
            val = page_props[key]
            if isinstance(val, list):
                print(f"  {key}  (list, {len(val)} items)")
            elif isinstance(val, dict):
                print(f"  {key}  (dict, keys: {list(val.keys())[:6]})")
            else:
                print(f"  {key}  = {repr(val)[:80]}")

        browser.close()


if __name__ == "__main__":
    main()
