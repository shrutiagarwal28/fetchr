"""
Unit tests for features/age.py — parse_age_range_label and derive_age_years_approx.

No database required. Both functions are pure: given the same inputs they
return the same outputs, so every case can be asserted with a fixed as_of date.

Run with:
    source .venv/bin/activate
    PYTHONPATH=. python3 tests/test_age.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from features.age import derive_age_years_approx, parse_age_range_label

# Fixed reference point so birth_date tests are deterministic regardless of
# when the test suite runs.
_AS_OF = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# parse_age_range_label
# ---------------------------------------------------------------------------

# (input, expected_output)
_LABEL_CASES: list[tuple[str | None, float | None]] = [
    # --- formats confirmed present in the current dataset ---
    ("(1-3 years)",        2.0),
    ("(3-8 years)",        5.5),
    ("(less than 1 year)", 0.5),

    # --- case and whitespace robustness ---
    ("(Less Than 1 Year)", 0.5),   # title-case variant PetFinder sometimes emits
    (" (1-3 years) ",      2.0),   # leading/trailing whitespace

    # --- single-number label (defensive — not in current data but parser must handle it) ---
    ("(12+ years)",        12.0),  # trailing '+' ignored; single digit taken directly

    # --- None and unparseable inputs ---
    (None,        None),
    ("",          None),   # empty string — no digits found
    ("old dog",   None),   # free-text with no digits
]


def test_parse_age_range_label() -> None:
    for label, expected in _LABEL_CASES:
        result = parse_age_range_label(label)
        assert result == expected, (
            f"FAIL parse_age_range_label({label!r}): "
            f"expected {expected!r}, got {result!r}"
        )
        print(f"OK  {str(label)!r:30} → {result!r}")


# ---------------------------------------------------------------------------
# derive_age_years_approx — birth_date path
# ---------------------------------------------------------------------------

# (days_before_as_of, expected_age_years)
# Expected = round(days / 365.25, 2)
_BIRTH_DATE_CASES: list[tuple[int, float]] = [
    (0,    0.0),   # born today — age is exactly zero
    (365,  1.0),   # ~1 year: 365/365.25 = 0.9993 → rounds to 1.0
    (730,  2.0),   # ~2 years: 730/365.25 = 1.9986 → rounds to 2.0
    (1826, 5.0),   # ~5 years: 1826/365.25 = 4.9986 → rounds to 5.0
    (183,  0.50),  # ~6 months: 183/365.25 = 0.5010 → rounds to 0.5
]


def test_derive_from_birth_date() -> None:
    for days, expected in _BIRTH_DATE_CASES:
        birth_date = _AS_OF - timedelta(days=days)
        result = derive_age_years_approx(
            birth_date=birth_date,
            age_range_label=None,
            as_of=_AS_OF,
        )
        assert result == expected, (
            f"FAIL birth_date {days}d before as_of: "
            f"expected {expected!r}, got {result!r}"
        )
        print(f"OK  birth_date {days:4d} days ago → {result!r} years")


# ---------------------------------------------------------------------------
# derive_age_years_approx — label fallback path
# ---------------------------------------------------------------------------

def test_derive_from_label_when_no_birth_date() -> None:
    result = derive_age_years_approx(
        birth_date=None,
        age_range_label="(1-3 years)",
        as_of=_AS_OF,
    )
    assert result == 2.0, f"FAIL: expected 2.0, got {result!r}"
    print("OK  no birth_date + '(1-3 years)' label → 2.0")


def test_derive_returns_none_when_both_sources_absent() -> None:
    result = derive_age_years_approx(
        birth_date=None,
        age_range_label=None,
        as_of=_AS_OF,
    )
    assert result is None, f"FAIL: expected None, got {result!r}"
    print("OK  both sources absent → None")


# ---------------------------------------------------------------------------
# derive_age_years_approx — priority: birth_date must win over label
# ---------------------------------------------------------------------------

def test_birth_date_takes_priority_over_label() -> None:
    # birth_date says ~2 years; label says the 3-8 range (midpoint 5.5).
    # This is the exact mismatch pattern the audit flagged (Corto, Wilma, etc.)
    # — birth_date must win.
    birth_date = _AS_OF - timedelta(days=730)  # 2.0 years
    result = derive_age_years_approx(
        birth_date=birth_date,
        age_range_label="(3-8 years)",          # midpoint would be 5.5
        as_of=_AS_OF,
    )
    assert result == 2.0, (
        f"FAIL: birth_date should take priority over label, got {result!r}"
    )
    print("OK  birth_date (2.0y) takes priority over '(3-8 years)' label (5.5)")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== parse_age_range_label ===")
    test_parse_age_range_label()

    print("\n=== derive_age_years_approx — birth_date path ===")
    test_derive_from_birth_date()

    print("\n=== derive_age_years_approx — label fallback ===")
    test_derive_from_label_when_no_birth_date()
    test_derive_returns_none_when_both_sources_absent()

    print("\n=== derive_age_years_approx — priority ===")
    test_birth_date_takes_priority_over_label()

    print("\nAll age feature tests passed.")
