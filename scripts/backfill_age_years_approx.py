"""
Backfill age_years_approx for all active dogs currently missing it.

Safe to re-run: only touches rows where age_years_approx IS NULL.
Commits in batches of BATCH_SIZE to keep individual transactions short.

Usage:
    python scripts/backfill_age_years_approx.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Project root is one level up from scripts/ — add it so we can import
# db/, features/, and models/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import Session
from features.age import derive_age_years_approx
from models.dog import DogORM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def backfill_age_years_approx() -> None:
    """
    Derive age_years_approx for every active dog that doesn't have it yet.

    birth_date takes priority (exact elapsed days); age_range_label is the
    fallback (midpoint of PetFinder's published range). Dogs with neither
    source remain null — they are logged as a warning so you can investigate.
    """
    with Session() as session:
        dogs: list[DogORM] = (
            session.query(DogORM)
            .filter(DogORM.age_years_approx.is_(None))
            .filter(DogORM.deleted_at.is_(None))
            .all()
        )

        total = len(dogs)
        logger.info("Found %d dogs with null age_years_approx — starting backfill", total)

        updated_from_birth_date = 0
        updated_from_label = 0
        still_null = 0

        for i, dog in enumerate(dogs):
            derived = derive_age_years_approx(dog.birth_date, dog.age_range_label)

            if derived is not None:
                dog.age_years_approx = derived
                if dog.birth_date is not None:
                    updated_from_birth_date += 1
                    logger.debug(
                        "source_id=%s: birth_date → %.2f years",
                        dog.source_id, derived,
                    )
                else:
                    updated_from_label += 1
                    logger.debug(
                        "source_id=%s: label %r → %.2f years",
                        dog.source_id, dog.age_range_label, derived,
                    )
            else:
                still_null += 1
                logger.warning(
                    "No age source for source_id=%s name=%r "
                    "(birth_date=None, age_range_label=%r) — leaving null",
                    dog.source_id, dog.name, dog.age_range_label,
                )

            # Commit in batches so a failure only rolls back the current batch,
            # not the entire run.
            if (i + 1) % BATCH_SIZE == 0:
                session.commit()
                logger.info("Committed batch — %d / %d processed", i + 1, total)

        session.commit()

    logger.info(
        "Backfill complete — "
        "from birth_date: %d | from label: %d | still null: %d | total: %d",
        updated_from_birth_date, updated_from_label, still_null, total,
    )


if __name__ == "__main__":
    backfill_age_years_approx()
