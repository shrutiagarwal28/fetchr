"""
Backfill Phase 2 features: tristate boolean encodings for compatibility and
medical fields. Updates existing dog_features rows — Phase 1 backfill must
have run first.

Safe to re-run — updates are idempotent.

Usage:
    python scripts/backfill_phase2.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import Session
from features.booleans import encode_tristate
from models.dog import DogORM
from models.features import DogFeaturesORM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def backfill_phase2() -> None:
    with Session() as session:
        # Join dog_features with dog_profiles to get both the feature row
        # (to update) and the source fields (to encode) in one query.
        pairs: list[tuple[DogFeaturesORM, DogORM]] = (
            session.query(DogFeaturesORM, DogORM)
            .join(DogORM, DogORM.id == DogFeaturesORM.dog_profile_id)
            .filter(DogORM.deleted_at.is_(None))
            .all()
        )

        total = len(pairs)
        logger.info("Processing %d dog_features rows for Phase 2 features", total)

        # Log per-field unknown (-1) counts so the null distribution is visible.
        unknown_counts: dict[str, int] = {
            "good_with_kids": 0, "good_with_dogs": 0, "good_with_cats": 0,
            "house_trained": 0, "vaccinated": 0, "spayed_neutered": 0,
        }

        for i, (feat, dog) in enumerate(pairs):
            feat.good_with_kids_enc = encode_tristate(dog.good_with_kids)
            feat.good_with_dogs_enc = encode_tristate(dog.good_with_dogs)
            feat.good_with_cats_enc = encode_tristate(dog.good_with_cats)
            feat.house_trained_enc = encode_tristate(dog.house_trained)
            feat.vaccinated_enc = encode_tristate(dog.vaccinated)
            feat.spayed_neutered_enc = encode_tristate(dog.spayed_neutered)
            feat.computed_at = datetime.now(timezone.utc)

            for field in unknown_counts:
                if getattr(dog, field) is None:
                    unknown_counts[field] += 1

            if (i + 1) % BATCH_SIZE == 0:
                session.commit()
                logger.info("Committed batch — %d / %d", i + 1, total)

        session.commit()

    logger.info("Phase 2 backfill complete — total: %d", total)
    for field, count in unknown_counts.items():
        pct = 100 * count / total if total else 0
        logger.info("  %-22s unknown (-1): %d / %d (%.0f%%)", field, count, total, pct)


if __name__ == "__main__":
    backfill_phase2()
