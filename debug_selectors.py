"""
Run this once to dump the raw HTML of a PetFinder detail page.
We'll use it to find the correct CSS selectors for breed, city, etc.
"""
from playwright.sync_api import sync_playwright

URL = "https://www.petfinder.com/dog/violet-08a24224-d920-4dec-9ab7-4dd45c0c1784/nj/jersey-city/red-collar-rescue-tx1086/details/"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3000)

    html = page.content()
    with open("debug_page.html", "w") as f:
        f.write(html)

    page.screenshot(path="debug_detail.png")
    print("Saved debug_page.html and debug_detail.png")
    browser.close()
