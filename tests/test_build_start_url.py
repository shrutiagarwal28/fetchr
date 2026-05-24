"""
Smoke tests for _build_start_url — the location normalizer in petfinder.py.

Run with:
  source .venv/bin/activate
  PYTHONPATH=. python3 tests/test_build_start_url.py
"""

from scrapers.petfinder import _build_start_url

_CASES = [
    # (input, expected output)
    ("nj/jersey-city",  "https://www.petfinder.com/search/dogs-for-adoption/us/nj/jerseycity/"),
    ("NJ/Jersey City",  "https://www.petfinder.com/search/dogs-for-adoption/us/nj/jerseycity/"),
    ("ca/los-angeles",  "https://www.petfinder.com/search/dogs-for-adoption/us/ca/losangeles/"),
    ("ny/new york",     "https://www.petfinder.com/search/dogs-for-adoption/us/ny/newyork/"),
    ("TX/Austin",       "https://www.petfinder.com/search/dogs-for-adoption/us/tx/austin/"),
]

_BAD_INPUTS = [
    "just-a-city",   # missing state segment
    "",              # empty string
]


def test_valid_locations() -> None:
    for loc, expected in _CASES:
        result = _build_start_url(loc)
        assert result == expected, f"FAIL {loc!r}: expected {expected!r}, got {result!r}"
        print(f"OK  {loc!r:25} → {result}")


def test_invalid_locations() -> None:
    for loc in _BAD_INPUTS:
        try:
            _build_start_url(loc)
            print(f"FAIL {loc!r}: should have raised ValueError")
        except ValueError as exc:
            print(f"OK  ValueError for {loc!r}: {exc}")


if __name__ == "__main__":
    print("=== valid inputs ===")
    test_valid_locations()
    print("\n=== invalid inputs ===")
    test_invalid_locations()
