"""
Backfill Phase 1 features: size_enc, age_category_enc, coat_type_enc.

Inserts a dog_features row for every active dog. Safe to re-run —
ON CONFLICT DO UPDATE overwrites existing values with freshly computed ones.

Usage:
    python scripts/backfill_phase1.py
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import Session
from features.categorical import encode_age_category, encode_coat_type, encode_size
from models.dog import DogORM
from models.features import DogFeaturesORM
from sqlalchemy.dialects.postgresql import insert as pg_insert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def backfill_phase1() -> None:
    with Session() as session:
        dogs: list[DogORM] = (
            session.query(DogORM)
            .filter(DogORM.deleted_at.is_(None))
            .all()
        )

        total = len(dogs)
        logger.info("Processing %d active dogs for Phase 1 features", total)

        coat_null_count = 0

        for i, dog in enumerate(dogs):
            coat_enc = encode_coat_type(dog.coat_length)
            if coat_enc is None:
                coat_null_count += 1

            stmt = (
                pg_insert(DogFeaturesORM)
                .values(
                    id=str(uuid.uuid4()),
                    dog_profile_id=dog.id,
                    size_enc=encode_size(dog.size),
                    age_category_enc=encode_age_category(dog.age_category),
                    coat_type_enc=coat_enc,
                    computed_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_update(
                    constraint="uq_dog_features_dog_profile_id",
                    set_={
                        "size_enc": encode_size(dog.size),
                        "age_category_enc": encode_age_category(dog.age_category),
                        "coat_type_enc": coat_enc,
                        "computed_at": datetime.now(timezone.utc),
                    },
                )
            )
            session.execute(stmt)

            if (i + 1) % BATCH_SIZE == 0:
                session.commit()
                logger.info("Committed batch — %d / %d", i + 1, total)

        session.commit()

    logger.info(
        "Phase 1 backfill complete — total: %d | coat_type null: %d (%.0f%%)",
        total, coat_null_count, 100 * coat_null_count / total if total else 0,
    )


if __name__ == "__main__":
    backfill_phase1()
