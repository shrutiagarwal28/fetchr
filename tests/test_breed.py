"""
Unit tests for features/breed.py — assign_breed_group() only.

load_breed_groups() does file I/O and is implicitly tested by the backfill
script. assign_breed_group() is pure and tested here in isolation.

No database required. No file I/O — dict passed directly.

Run with:
    source .venv/bin/activate
    PYTHONPATH=. python3 tests/test_breed.py
"""

from __future__ import annotations

from features.breed import (
    AUDIT_ONLY_FEATURES,
    assert_not_audit_only,
    assign_breed_group,
)

# Pre-built dict covering one breed per group plus edge-case entries.
_GROUPS: dict[str, str] = {
    "Labrador Retriever": "Sporting",
    "Great Pyrenees": "Working",
    "German Shepherd Dog": "Herding",
    "Beagle": "Hound",
    "Airedale Terrier": "Terrier",
    "Chihuahua": "Toy",
    "French Bulldog": "Non-Sporting",
    "Pit Bull Terrier": "Pit Bull Type",
    "Mixed Breed": "Mixed/Unknown",
    # Bare type labels
    "Terrier": "Mixed/Unknown",
    "Hound": "Mixed/Unknown",
    "Spaniel": "Mixed/Unknown",
    "Retriever": "Mixed/Unknown",
    # Pit Bull family
    "Bull Terrier": "Pit Bull Type",
    "American Staffordshire Terrier": "Pit Bull Type",
    # Trailing-space variant (mimics raw PetFinder API data)
    "Japanese Akita": "Working",
}


def test_known_breed_returns_correct_group() -> None:
    for breed, expected in _GROUPS.items():
        result = assign_breed_group(breed, _GROUPS)
        assert result == expected, (
            f"FAIL assign_breed_group({breed!r}): expected {expected!r}, got {result!r}"
        )
    print(f"OK  all {len(_GROUPS)} known breeds → correct groups")


def test_none_input_returns_unknown() -> None:
    result = assign_breed_group(None, _GROUPS)
    assert result == "Unknown", f"FAIL: expected 'Unknown', got {result!r}"
    print("OK  None input → 'Unknown'")


def test_missing_breed_returns_unknown() -> None:
    result = assign_breed_group("Space Corgi", _GROUPS)
    assert result == "Unknown", f"FAIL: expected 'Unknown', got {result!r}"
    print("OK  unrecognized breed → 'Unknown'")


def test_case_sensitive_no_match() -> None:
    # PetFinder names are title-case; lowercase must NOT match.
    result = assign_breed_group("labrador retriever", _GROUPS)
    assert result == "Unknown", (
        f"FAIL: lowercase 'labrador retriever' should not match, got {result!r}"
    )
    print("OK  lowercase breed name → 'Unknown' (no case-folding)")


def test_trailing_whitespace_stripped() -> None:
    # PetFinder's API returns some breed names with trailing spaces (e.g. "Japanese Akita ").
    result = assign_breed_group("Japanese Akita ", _GROUPS)
    assert result == "Working", (
        f"FAIL: trailing space should be stripped before lookup, got {result!r}"
    )
    print("OK  trailing whitespace stripped before lookup")


def test_bare_type_labels_are_mixed_unknown() -> None:
    for label in ("Terrier", "Hound", "Spaniel", "Retriever"):
        result = assign_breed_group(label, _GROUPS)
        assert result == "Mixed/Unknown", (
            f"FAIL: generic label {label!r} expected 'Mixed/Unknown', got {result!r}"
        )
    print("OK  bare type labels ('Terrier', 'Hound', 'Spaniel', 'Retriever') → 'Mixed/Unknown'")


def test_pit_bull_type_group() -> None:
    for breed in ("Pit Bull Terrier", "Bull Terrier", "American Staffordshire Terrier"):
        result = assign_breed_group(breed, _GROUPS)
        assert result == "Pit Bull Type", (
            f"FAIL: {breed!r} expected 'Pit Bull Type', got {result!r}"
        )
    print("OK  pit-bull-type breeds → 'Pit Bull Type'")


def test_assert_not_audit_only_passes_for_clean_features() -> None:
    # A realistic adopter-facing filter set — all trait/individual-dog features,
    # no breed_group. Must NOT raise.
    clean = ["size_enc", "good_with_cats_enc", "age_years_imputed", "house_trained_enc"]
    assert_not_audit_only(clean)  # no exception == pass
    print("OK  assert_not_audit_only passes for clean trait-only feature set")


def test_assert_not_audit_only_raises_when_breed_group_leaks() -> None:
    # breed_group must never be used for filtering/ranking — the guard must catch it.
    leaky = ["size_enc", "breed_group", "good_with_cats_enc"]
    raised = False
    try:
        assert_not_audit_only(leaky)
    except ValueError as e:
        raised = True
        assert "breed_group" in str(e), f"FAIL: error should name breed_group, got: {e}"
    assert raised, "FAIL: expected ValueError when breed_group used in filtering/ranking"
    assert "breed_group" in AUDIT_ONLY_FEATURES, "FAIL: breed_group must be audit-only"
    print("OK  assert_not_audit_only raises when breed_group leaks into filtering/ranking")


def test_all_nine_groups_represented() -> None:
    nine_groups = {
        "Sporting", "Working", "Herding", "Hound",
        "Terrier", "Toy", "Non-Sporting", "Pit Bull Type", "Mixed/Unknown",
    }
    assigned = {assign_breed_group(b, _GROUPS) for b in _GROUPS}
    missing = nine_groups - assigned
    assert not missing, f"FAIL: groups not represented in test dict: {missing}"
    print(f"OK  all 9 groups represented in test fixture")


if __name__ == "__main__":
    test_known_breed_returns_correct_group()
    test_none_input_returns_unknown()
    test_missing_breed_returns_unknown()
    test_case_sensitive_no_match()
    test_trailing_whitespace_stripped()
    test_bare_type_labels_are_mixed_unknown()
    test_pit_bull_type_group()
    test_assert_not_audit_only_passes_for_clean_features()
    test_assert_not_audit_only_raises_when_breed_group_leaks()
    test_all_nine_groups_represented()

    print("\nAll breed group tests passed.")
