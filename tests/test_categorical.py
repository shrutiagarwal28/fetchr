"""
Unit tests for features/categorical.py.

No database required — all three functions are pure.

Run with:
    source .venv/bin/activate
    PYTHONPATH=. python3 tests/test_categorical.py
"""

from __future__ import annotations

from features.categorical import encode_age_category, encode_coat_type, encode_size

# ---------------------------------------------------------------------------
# encode_size
# ---------------------------------------------------------------------------

_SIZE_CASES: list[tuple[str | None, int | None]] = [
    ("small",   1),
    ("medium",  2),
    ("large",   3),
    ("xlarge",  4),
    # case insensitivity
    ("Small",   1),
    ("MEDIUM",  2),
    # null and unknown
    (None,      None),
    ("giant",   None),   # unrecognized — not a PetFinder value, returns None
]


def test_encode_size() -> None:
    for val, expected in _SIZE_CASES:
        result = encode_size(val)
        assert result == expected, (
            f"FAIL encode_size({val!r}): expected {expected!r}, got {result!r}"
        )
        print(f"OK  encode_size({str(val)!r:10}) → {result!r}")


# ---------------------------------------------------------------------------
# encode_age_category
# ---------------------------------------------------------------------------

_AGE_CATEGORY_CASES: list[tuple[str | None, int | None]] = [
    ("puppy",   1),
    ("young",   2),
    ("adult",   3),
    ("senior",  4),
    # "unknown" must map to None — not a position on the ordinal scale
    ("unknown", None),
    # case insensitivity
    ("Puppy",   1),
    ("ADULT",   3),
    # null and unrecognized
    (None,      None),
    ("elderly", None),   # unrecognized future value
]


def test_encode_age_category() -> None:
    for val, expected in _AGE_CATEGORY_CASES:
        result = encode_age_category(val)
        assert result == expected, (
            f"FAIL encode_age_category({val!r}): expected {expected!r}, got {result!r}"
        )
        print(f"OK  encode_age_category({str(val)!r:12}) → {result!r}")


# ---------------------------------------------------------------------------
# encode_coat_type
# ---------------------------------------------------------------------------

_COAT_TYPE_CASES: list[tuple[str | None, int | None]] = [
    # length values
    ("Short",   1),
    ("Medium",  2),
    ("Long",    3),
    # texture values — must be included, not discarded
    ("Wire",    2),   # moderate grooming effort, same tier as Medium
    ("Curly",   3),   # high grooming effort, same tier as Long
    # case insensitivity
    ("short",   1),
    ("LONG",    3),
    ("wire",    2),
    ("curly",   3),
    # null and unrecognized
    (None,      None),
    ("Wavy",    None),   # hypothetical new PetFinder value
]


def test_encode_coat_type() -> None:
    for val, expected in _COAT_TYPE_CASES:
        result = encode_coat_type(val)
        assert result == expected, (
            f"FAIL encode_coat_type({val!r}): expected {expected!r}, got {result!r}"
        )
        print(f"OK  encode_coat_type({str(val)!r:10}) → {result!r}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== encode_size ===")
    test_encode_size()

    print("\n=== encode_age_category ===")
    test_encode_age_category()

    print("\n=== encode_coat_type ===")
    test_encode_coat_type()

    print("\nAll categorical encoding tests passed.")
