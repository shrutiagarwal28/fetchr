"""
Intercept every GraphQL request Playwright's browser makes while loading
the PetFinder listing page, then fire a breed-list query using the real
cookies + headers the browser established.

Saves two files:
  - graphql_intercepted_requests.json  — all GraphQL calls the page makes on load
  - graphql_breed_list.json            — result of our own breed query, if it works

Run:
    source .venv/bin/activate
    python3 explore_graphql_intercept.py
"""

import json
import os
import threading
from typing import Any

from playwright.sync_api import sync_playwright, Request, Response

LISTING_URL = "https://www.petfinder.com/search/dogs-for-adoption/us/nj/jerseycity/"
GRAPHQL_URL = "https://psl.petfinder.com/graphql"

_CLIENT_ID = os.environ["PETFINDER_CLIENT_ID"]
_CLIENT_SECRET = os.environ["PETFINDER_CLIENT_SECRET"]

intercepted: list[dict[str, Any]] = []
lock = threading.Lock()


def on_request(request: Request) -> None:
    if GRAPHQL_URL not in request.url:
        return
    try:
        body = request.post_data_json or {}
    except Exception:
        body = {}
    with lock:
        intercepted.append({
            "url": request.url,
            "method": request.method,
            "headers": dict(request.headers),
            "body": body,
        })


def on_response(response: Response) -> None:
    if GRAPHQL_URL not in response.url:
        return
    # Match this response back to the last intercepted entry with no response yet
    try:
        body = response.json()
    except Exception:
        body = {}
    with lock:
        for entry in reversed(intercepted):
            if "response" not in entry:
                entry["response"] = body
                break


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

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"Loading listing page and intercepting GraphQL traffic...")
        page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=60_000)
        # Wait for lazy-loaded GraphQL calls (filters, search results, etc.)
        page.wait_for_timeout(6000)

        # Save all intercepted calls
        with open("graphql_intercepted_requests.json", "w") as f:
            json.dump(intercepted, f, indent=2, ensure_ascii=False)

        print(f"\nIntercepted {len(intercepted)} GraphQL request(s).")
        print("Operation names found:")
        for entry in intercepted:
            op = entry.get("body", {}).get("operationName") or "(unnamed)"
            variables = entry.get("body", {}).get("variables", {})
            resp_keys = list((entry.get("response") or {}).get("data", {}).keys())
            print(f"  {op}  vars={json.dumps(variables)[:80]}  → data keys: {resp_keys}")

        # Now look for a breed-specific query among what we captured,
        # or try common breed query names using the real browser session cookies
        breed_entries = [
            e for e in intercepted
            if any(
                kw in (e.get("body", {}).get("operationName") or "").lower()
                for kw in ("breed", "filter", "search", "option", "lookup", "type")
            )
        ]

        if breed_entries:
            print(f"\nBreed-related queries ({len(breed_entries)}):")
            for e in breed_entries:
                print(f"  {e['body'].get('operationName')} → {json.dumps(e.get('response', {}))[:200]}")
        else:
            print("\nNo breed-named operations found — check graphql_intercepted_requests.json for all ops.")

        # Try firing a breed query with the real session using page.evaluate
        # (runs inside the browser context, so Akamai sees a legit browser request)
        print("\nFiring breed introspection query from inside the browser context...")
        breed_result = page.evaluate(f"""
            async () => {{
                const res = await fetch('{GRAPHQL_URL}', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'x-client-id': '{_CLIENT_ID}',
                        'x-client-secret': '{_CLIENT_SECRET}',
                    }},
                    body: JSON.stringify({{
                        query: `{{
                            __schema {{
                                queryType {{
                                    fields {{ name description }}
                                }}
                            }}
                        }}`
                    }})
                }});
                return await res.json();
            }}
        """)

        with open("graphql_breed_list.json", "w") as f:
            json.dump(breed_result, f, indent=2, ensure_ascii=False)

        print("Schema introspection result saved to graphql_breed_list.json")
        if "data" in breed_result:
            fields = breed_result["data"]["__schema"]["queryType"]["fields"]
            print(f"Available query fields ({len(fields)}):")
            for fld in fields:
                print(f"  {fld['name']}")
        else:
            print(f"Response: {json.dumps(breed_result)[:300]}")

        browser.close()


if __name__ == "__main__":
    main()
