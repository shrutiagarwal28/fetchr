"""
One-time seed script: populate the petfinder_breeds table from petfinder_breeds.json.

This is idempotent — running it twice is safe. Existing rows are skipped via
INSERT ... ON CONFLICT DO NOTHING, so re-running after a partial load or after
PetFinder adds new breeds is harmless.

Run from the project root:
    source .venv/bin/activate
    python3 scripts/seed_breeds.py

When to re-run:
    PetFinder occasionally adds breeds to their taxonomy (the ID gaps in the data
    show this has happened several times). Re-run this script after any
    AllAnimalAttributes GraphQL call returns breeds not yet in the table.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Allow imports from the project root regardless of working directory
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from db.connection import Session
from models.reference import PetFinderBreedORM

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BREEDS_FILE = Path(__file__).parent.parent / "petfinder_breeds.json"


def seed_breeds() -> None:
    with open(BREEDS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    breeds = data["breeds"]
    seeded_at = datetime.now(timezone.utc)

    inserted = 0
    skipped = 0

    with Session() as session:
        for breed in breeds:
            existing = session.get(PetFinderBreedORM, breed["id"])
            if existing is not None:
                skipped += 1
                continue

            session.add(PetFinderBreedORM(
                id=breed["id"],
                alternate_id=breed["alternateId"],
                name=breed["name"],
                seeded_at=seeded_at,
            ))
            inserted += 1

        session.commit()

    logger.info(
        "Breeds seeded: %d inserted, %d already existed (total in file: %d)",
        inserted, skipped, len(breeds),
    )


if __name__ == "__main__":
    seed_breeds()
