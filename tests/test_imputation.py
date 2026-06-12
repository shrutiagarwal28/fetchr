"""
Unit tests for features/imputation.py — impute_age() only.

compute_category_medians() touches the DB and is tested implicitly by the
backfill script. impute_age() is pure and tested here in isolation.

No database required.

Run with:
    source .venv/bin/activate
    PYTHONPATH=. python3 tests/test_imputation.py
"""

from __future__ import annotations

from features.imputation import impute_age

_MEDIANS = {"puppy": 0.5, "young": 2.0, "adult": 3.5, "senior": 7.0}
_GLOBAL_MEDIAN = 2.5


def test_present_age_is_returned_unchanged() -> None:
    age, imputed = impute_age(
        age_years_approx=4.2,
        age_category="adult",
        medians=_MEDIANS,
        global_median=_GLOBAL_MEDIAN,
    )
    assert age == 4.2, f"FAIL: expected 4.2, got {age}"
    assert imputed is False, f"FAIL: expected False, got {imputed}"
    print("OK  age present → returned as-is, was_imputed=False")


def test_null_age_uses_category_median() -> None:
    age, imputed = impute_age(
        age_years_approx=None,
        age_category="senior",
        medians=_MEDIANS,
        global_median=_GLOBAL_MEDIAN,
    )
    assert age == 7.0, f"FAIL: expected 7.0 (senior median), got {age}"
    assert imputed is True, f"FAIL: expected True, got {imputed}"
    print("OK  age null + known category → category median, was_imputed=True")


def test_null_age_with_unknown_category_uses_global_median() -> None:
    # "unknown" is not in _MEDIANS — should fall back to global median
    age, imputed = impute_age(
        age_years_approx=None,
        age_category="unknown",
        medians=_MEDIANS,
        global_median=_GLOBAL_MEDIAN,
    )
    assert age == _GLOBAL_MEDIAN, f"FAIL: expected {_GLOBAL_MEDIAN}, got {age}"
    assert imputed is True, f"FAIL: expected True, got {imputed}"
    print("OK  age null + unknown category → global median, was_imputed=True")


def test_null_age_with_none_category_uses_global_median() -> None:
    # age_category itself is None — same fallback path
    age, imputed = impute_age(
        age_years_approx=None,
        age_category=None,
        medians=_MEDIANS,
        global_median=_GLOBAL_MEDIAN,
    )
    assert age == _GLOBAL_MEDIAN, f"FAIL: expected {_GLOBAL_MEDIAN}, got {age}"
    assert imputed is True, f"FAIL: expected True, got {imputed}"
    print("OK  age null + category None → global median, was_imputed=True")


def test_zero_age_is_not_treated_as_null() -> None:
    # 0.0 is a valid age (dog born today) — must not be imputed
    age, imputed = impute_age(
        age_years_approx=0.0,
        age_category="puppy",
        medians=_MEDIANS,
        global_median=_GLOBAL_MEDIAN,
    )
    assert age == 0.0, f"FAIL: expected 0.0, got {age}"
    assert imputed is False, f"FAIL: expected False, got {imputed}"
    print("OK  age=0.0 (born today) → returned as-is, not imputed")


if __name__ == "__main__":
    test_present_age_is_returned_unchanged()
    test_null_age_uses_category_median()
    test_null_age_with_unknown_category_uses_global_median()
    test_null_age_with_none_category_uses_global_median()
    test_zero_age_is_not_treated_as_null()

    print("\nAll imputation tests passed.")
