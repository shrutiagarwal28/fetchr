"""
Age derivation for dog profiles.

PetFinder doesn't expose a numeric age directly — it gives either a birth_date
or an age_range_label like "(1-3 years)". derive_age_years_approx() normalizes
both sources into a single float that downstream features can use.

These are pure functions: no DB access, no side effects, fully unit-testable.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def parse_age_range_label(label: Optional[str]) -> Optional[float]:
    """
    Extract a numeric age in years from PetFinder's range label string.

    Exhaustively verified formats from the current dataset:
        "(1-3 years)"        → 2.0  (midpoint of the range)
        "(3-8 years)"        → 5.5  (midpoint of the range)
        "(less than 1 year)" → 0.5  (convention: half a year for sub-1 labels)

    Returns None if the label is None or doesn't match any known pattern
    so callers can distinguish "could not parse" from "parsed as zero".
    """
    if label is None:
        return None

    normalized = label.lower().strip("() ")

    # Check for the sub-year label before the digit extraction so the lone
    # "1" in "less than 1 year" doesn't produce a misleading result.
    if "less than" in normalized:
        return 0.5

    numbers = [int(m) for m in re.findall(r"\d+", normalized)]

    if len(numbers) == 2:
        # Range label — return the midpoint as a proxy for expected age
        return (numbers[0] + numbers[1]) / 2.0
    if len(numbers) == 1:
        # Single value (e.g. "12+ years") — take the floor as-is
        return float(numbers[0])

    logger.warning("Could not parse age_range_label: %r — returning None", label)
    return None


def derive_age_years_approx(
    birth_date: Optional[datetime],
    age_range_label: Optional[str],
    as_of: Optional[datetime] = None,
) -> Optional[float]:
    """
    Derive a numeric age in years from the available PetFinder fields.

    Priority:
      1. birth_date — exact elapsed days, rounded to 2 decimal places.
      2. age_range_label — midpoint of the published range.
      3. None — neither source available.

    as_of defaults to UTC now and is injectable so tests can assert exact values
    without depending on the current wall clock. birth_date is guaranteed
    tz-aware (UTC) by _parse_iso_dt() in the scraper layer.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)

    if birth_date is not None:
        age_years = (as_of - birth_date).days / 365.25
        return round(age_years, 2)

    result = parse_age_range_label(age_range_label)
    if result is None:
        # Debug, not warning — nulls on both inputs are expected for some dogs
        # and the caller will log a summary count.
        logger.debug(
            "No age source: birth_date=None, age_range_label=%r",
            age_range_label,
        )
    return result
