"""
Breed group assignment for dog_features.breed_group.

load_breed_groups() reads data/breed_groups.json once and caches it.
assign_breed_group() is pure: takes a breed name and the loaded dict,
returns a group string — never None.

The 9 groups: Sporting, Working, Herding, Hound, Terrier, Toy,
Non-Sporting, Pit Bull Type, Mixed/Unknown.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _PROJECT_ROOT / "data" / "breed_groups.json"

_cache: dict[str, str] = {}

# ---------------------------------------------------------------------------
# GOVERNANCE: breed_group is AUDIT-ONLY. See plan "Guardrail (LOCKED)".
#
# breed_group — and the 'Pit Bull Type' value in particular — must NEVER feed an
# adopter-facing exclusion filter or the matcher's ranking/scoring logic. It exists
# for internal monitoring and disparate-impact audits only (e.g. measuring whether
# pit-type dogs suffer longer time-to-adoption so we can correct for bias, not encode
# it). Breed-based exclusion is the discriminatory pattern that harms these dogs in
# the real world; replicating it in software would launder that bias behind an
# algorithm. Adopter constraints are expressed through trait filters instead
# (good_with_cats, energy/low-energy, novice-friendly) — which describe the individual
# dog, not its breed label.
#
# AUDIT_ONLY_FEATURES is the machine-checkable contract. Any future filter/ranking
# code must call assert_not_audit_only() on its feature inputs so a breed_group leak
# fails loudly at the call site rather than silently shaping who sees which dog.
# ---------------------------------------------------------------------------
AUDIT_ONLY_FEATURES: frozenset[str] = frozenset({"breed_group"})


def assert_not_audit_only(feature_names: Iterable[str]) -> None:
    """
    Guard for adopter-facing filter/ranking code.

    Raises ValueError if any audit-only feature (see AUDIT_ONLY_FEATURES) appears
    among the features being used for filtering or ranking. Call this wherever
    adopter-facing selection or match scoring is built, before the features are
    consumed — it is the enforcement point for the Pit Bull Type guardrail.
    """
    leaked = AUDIT_ONLY_FEATURES.intersection(feature_names)
    if leaked:
        raise ValueError(
            f"Audit-only feature(s) {sorted(leaked)} used in filtering/ranking — "
            "forbidden by the Pit Bull Type guardrail (see plan). "
            "Use trait filters (good_with_cats, energy, novice-friendly) instead."
        )


def load_breed_groups(path: Optional[str | Path] = None) -> dict[str, str]:
    """Load breed_groups.json into a dict and cache it in memory."""
    global _cache
    if _cache:
        return _cache

    resolved = Path(path) if path is not None else _DEFAULT_PATH
    with resolved.open() as f:
        _cache = json.load(f)

    logger.info("Loaded %d breed group mappings from %s", len(_cache), resolved)
    return _cache


def assign_breed_group(
    breed_primary: Optional[str],
    breed_groups: dict[str, str],
) -> str:
    """
    Look up breed_primary in breed_groups dict.

    Returns the mapped group string, or "Unknown" if not found.
    Strips surrounding whitespace before lookup to handle PetFinder
    data quality issues (e.g. trailing spaces in the source API).
    No case-folding — PetFinder breed names are title-case and stored
    that way in dog_profiles; exact match after whitespace strip.
    """
    if breed_primary is None:
        return "Unknown"

    key = breed_primary.strip()
    group = breed_groups.get(key)
    if group is None:
        logger.warning("Unknown breed %r — assigning group 'Unknown'", breed_primary)
        return "Unknown"

    return group
