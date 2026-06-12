"""
Ordinal encodings for categorical dog profile fields.

All functions are pure: no DB access, no side effects, fully unit-testable.
Each logs a warning on any unrecognized input value so new PetFinder values
are surfaced in logs rather than silently discarded.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SIZE_MAP: dict[str, int] = {
    "small":  1,
    "medium": 2,
    "large":  3,
    "xlarge": 4,
}

_AGE_CATEGORY_MAP: dict[str, int] = {
    "puppy":  1,
    "young":  2,
    "adult":  3,
    "senior": 4,
    # "unknown" is intentionally absent — maps to None below
}

# Grooming effort proxy. PetFinder's coat_length field mixes length descriptors
# (Short/Medium/Long) and texture descriptors (Wire/Curly). Encoding as a
# grooming effort scale is more semantically meaningful for matching:
#   Tier 1 — Short:         wash and go, minimal maintenance
#   Tier 2 — Medium, Wire:  moderate effort; Wire needs professional stripping
#   Tier 3 — Long, Curly:   high maintenance; Curly is often hypoallergenic
_COAT_TYPE_MAP: dict[str, int] = {
    "short":  1,
    "medium": 2,
    "wire":   2,
    "long":   3,
    "curly":  3,
}


def encode_size(val: Optional[str]) -> Optional[int]:
    """Map dog_profiles.size to an ordinal integer. Returns None for null input."""
    if val is None:
        return None
    result = _SIZE_MAP.get(val.lower().strip())
    if result is None:
        logger.warning("Unrecognized size value: %r — returning None", val)
    return result


def encode_age_category(val: Optional[str]) -> Optional[int]:
    """
    Map dog_profiles.age_category to an ordinal integer.
    "unknown" and null both return None — unknown age is not the same as any
    specific life stage and shouldn't be forced onto the scale.
    """
    if val is None:
        return None
    normalized = val.lower().strip()
    if normalized == "unknown":
        return None
    result = _AGE_CATEGORY_MAP.get(normalized)
    if result is None:
        logger.warning("Unrecognized age_category value: %r — returning None", val)
    return result


def encode_coat_type(val: Optional[str]) -> Optional[int]:
    """
    Map dog_profiles.coat_length to a grooming effort ordinal.

    The source field is named coat_length but contains both length and texture
    values. This function reframes it as coat_type (grooming effort proxy) to
    be honest about what is actually being encoded. See _COAT_TYPE_MAP for the
    full tier assignments and reasoning.
    """
    if val is None:
        return None
    result = _COAT_TYPE_MAP.get(val.lower().strip())
    if result is None:
        logger.warning("Unrecognized coat_length value: %r — returning None", val)
    return result
