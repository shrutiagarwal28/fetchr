"""
One-shot script: open a known PetFinder detail page (non-headless),
dump the full props.pageProps JSON to explore_detail_page_props.json.

We currently only extract props.pageProps.animal — this reveals what else
lives alongside it at the pageProps level (breed lists, filters, etc.).

Run:
    source .venv/bin/activate
    python3 explore_detail_page_props.py
"""

import json
import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

DETAIL_URL = (
    "https://www.petfinder.com/dog/"
    "violet-08a24224-d920-4dec-9ab7-4dd45c0c1784/"
    "nj/jersey-city/red-collar-rescue-tx1086/details/"
)
OUTPUT_FILE = "explore_detail_page_props.json"


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

        print(f"Navigating to: {DETAIL_URL}")
        page.goto(DETAIL_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3000)

        raw = page.evaluate(
            "() => { const el = document.getElementById('__NEXT_DATA__'); "
            "return el ? el.textContent : null; }"
        )

        if not raw:
            print("ERROR: __NEXT_DATA__ not found.")
            browser.close()
            return

        data = json.loads(raw)
        page_props = data.get("props", {}).get("pageProps", {})

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(page_props, f, indent=2, ensure_ascii=False)

        print(f"Saved props.pageProps to {OUTPUT_FILE}  ({len(raw):,} bytes raw)")
        print(f"\nKeys inside props.pageProps ({len(page_props)} total):")
        for key, val in page_props.items():
            if key == "animal":
                print(f"  animal  {{dict — the dog we already parse}}")
            elif isinstance(val, list):
                print(f"  {key}  [list, {len(val)} items]")
                if val and isinstance(val[0], dict):
                    print(f"    first item keys: {list(val[0].keys())[:8]}")
                elif val:
                    print(f"    first item: {repr(val[0])[:100]}")
            elif isinstance(val, dict):
                print(f"  {key}  {{dict, keys: {list(val.keys())[:10]}}}")
            else:
                print(f"  {key} = {repr(val)[:100]}")

        browser.close()


if __name__ == "__main__":
    main()
