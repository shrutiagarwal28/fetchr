"""
Backfill Phase 3 features: age_years_imputed and age_was_imputed.

Computes category medians once, then applies impute_age() to every dog.
Updates existing dog_features rows — Phase 1 backfill must have run first.

Safe to re-run — updates are idempotent.

Usage:
    python scripts/backfill_phase3.py
"""

from __future__ import annotations

import logging
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import Session
from features.imputation import compute_category_medians, impute_age
from models.dog import DogORM
from models.features import DogFeaturesORM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def backfill_phase3() -> None:
    with Session() as session:
        medians = compute_category_medians(session)

        # Compute global median as the fallback for dogs with unknown age_category.
        all_ages = [
            row[0] for row in
            session.query(DogORM.age_years_approx)
            .filter(DogORM.deleted_at.is_(None))
            .filter(DogORM.age_years_approx.isnot(None))
        ]
        global_median = statistics.median(all_ages) if all_ages else 0.0
        logger.info("Global age median: %.2f years", global_median)

        pairs: list[tuple[DogFeaturesORM, DogORM]] = (
            session.query(DogFeaturesORM, DogORM)
            .join(DogORM, DogORM.id == DogFeaturesORM.dog_profile_id)
            .filter(DogORM.deleted_at.is_(None))
            .all()
        )

        total = len(pairs)
        imputed_count = 0
        logger.info("Processing %d dog_features rows for Phase 3 features", total)

        for i, (feat, dog) in enumerate(pairs):
            age_imputed, was_imputed = impute_age(
                dog.age_years_approx,
                dog.age_category,
                medians,
                global_median,
            )
            feat.age_years_imputed = age_imputed
            feat.age_was_imputed = was_imputed
            feat.computed_at = datetime.now(timezone.utc)

            if was_imputed:
                imputed_count += 1

            if (i + 1) % BATCH_SIZE == 0:
                session.commit()
                logger.info("Committed batch — %d / %d", i + 1, total)

        session.commit()

    logger.info(
        "Phase 3 backfill complete — total: %d | imputed: %d | direct: %d",
        total, imputed_count, total - imputed_count,
    )


if __name__ == "__main__":
    backfill_phase3()
