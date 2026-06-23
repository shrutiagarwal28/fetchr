"""
Backfill Phase 4 feature: breed_group.

Maps every dog's breed_primary to one of 9 groups using data/breed_groups.json,
which covers the full 309-breed PetFinder taxonomy. Updates existing dog_features
rows — Phase 1 backfill must have run first.

Safe to re-run — updates are idempotent.

Usage:
    python scripts/backfill_phase4.py
"""

from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import Session
from features.breed import assign_breed_group, load_breed_groups
from models.dog import DogORM
from models.features import DogFeaturesORM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def backfill_phase4() -> None:
    breed_groups = load_breed_groups()

    with Session() as session:
        pairs: list[tuple[DogFeaturesORM, DogORM]] = (
            session.query(DogFeaturesORM, DogORM)
            .join(DogORM, DogORM.id == DogFeaturesORM.dog_profile_id)
            .filter(DogORM.deleted_at.is_(None))
            .all()
        )

        total = len(pairs)
        group_counts: Counter[str] = Counter()
        logger.info("Processing %d dog_features rows for Phase 4 features", total)

        for i, (feat, dog) in enumerate(pairs):
            group = assign_breed_group(dog.breed_primary, breed_groups)
            feat.breed_group = group
            feat.computed_at = datetime.now(timezone.utc)

            group_counts[group] += 1

            if (i + 1) % BATCH_SIZE == 0:
                session.commit()
                logger.info("Committed batch — %d / %d", i + 1, total)

        session.commit()

    logger.info("Phase 4 backfill complete — total: %d", total)
    for group, count in sorted(group_counts.items(), key=lambda x: -x[1]):
        pct = 100 * count / total if total else 0
        logger.info("  %-20s %d / %d (%.0f%%)", group, count, total, pct)

    unknown_count = group_counts.get("Unknown", 0)
    if unknown_count > 0:
        logger.warning(
            "%d dogs assigned 'Unknown' breed group — check breed_groups.json coverage",
            unknown_count,
        )


if __name__ == "__main__":
    backfill_phase4()
