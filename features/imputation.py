"""
Age imputation for dogs missing age_years_approx.

Two functions with a deliberate split:
  compute_category_medians() — touches the DB, returns a plain dict
  impute_age()               — pure function, takes that dict as input

This separation keeps impute_age() unit-testable without a database connection.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session as SessionType

from models.dog import DogORM

logger = logging.getLogger(__name__)


def compute_category_medians(session: SessionType) -> dict[str, float]:
    """
    Compute the median age_years_approx per age_category across all active dogs.

    Returns a dict keyed by age_category string, e.g.:
        {"puppy": 0.5, "young": 2.0, "adult": 3.5, "senior": 7.0}

    Only categories with at least one non-null age are included. The caller
    should compute a global fallback median before calling impute_age() in case
    a dog's age_category is absent from this dict.
    """
    rows = (
        session.query(DogORM.age_category, DogORM.age_years_approx)
        .filter(DogORM.deleted_at.is_(None))
        .filter(DogORM.age_years_approx.isnot(None))
        .all()
    )

    buckets: dict[str, list[float]] = defaultdict(list)
    for age_category, age_years in rows:
        buckets[age_category].append(age_years)

    medians = {cat: statistics.median(ages) for cat, ages in buckets.items()}
    logger.info("Computed age medians by category: %s", medians)
    return medians


def impute_age(
    age_years_approx: Optional[float],
    age_category: Optional[str],
    medians: dict[str, float],
    global_median: float,
) -> tuple[float, bool]:
    """
    Return (age_years_imputed, age_was_imputed).

    If age_years_approx is present, return it unchanged with was_imputed=False.
    If absent, substitute the category median — or global_median if the dog's
    age_category isn't in the medians dict (e.g. "unknown" or a new PetFinder value).

    global_median is passed explicitly so the caller controls the fallback and
    this function stays pure (no statistics.median call, no DB access).
    """
    if age_years_approx is not None:
        return age_years_approx, False

    category_key = (age_category or "").lower().strip()
    if category_key in medians:
        return medians[category_key], True

    # Age category absent from medians — fall back to global median.
    logger.warning(
        "age_category %r not found in medians dict — using global median %.2f",
        age_category, global_median,
    )
    return global_median, True
