"""
DogFeaturesORM — the Feature Store table.

One row per dog (1:1 with dog_profiles via dog_profile_id). All values here
are derived from dog_profiles and are re-computable — this table is a cache,
not a source of truth. Backfill scripts populate it; scrapes do not write here.

Columns are added phase by phase via Alembic migrations:
  Phase 1: size_enc, age_category_enc, coat_type_enc
  Phase 2: good_with_*_enc, house_trained_enc, vaccinated_enc, spayed_neutered_enc
  Phase 3: age_years_imputed, age_was_imputed
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, SmallInteger, String, UniqueConstraint

from models.dog import Base


class DogFeaturesORM(Base):
    __tablename__ = "dog_features"
    __table_args__ = (
        UniqueConstraint("dog_profile_id", name="uq_dog_features_dog_profile_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # FK to dog_profiles.id declared in the Alembic migration, not here —
    # consistent with how all FK constraints are managed in this project.
    dog_profile_id = Column(String(36), nullable=False)

    # Phase 1 — ordinal encodings
    # size:         small=1, medium=2, large=3, xlarge=4
    # age_category: puppy=1, young=2, adult=3, senior=4, unknown→null
    # coat_type:    grooming effort proxy — Short=1, Medium/Wire=2, Long/Curly=3
    size_enc = Column(SmallInteger, nullable=True)
    age_category_enc = Column(SmallInteger, nullable=True)
    coat_type_enc = Column(SmallInteger, nullable=True)

    # Phase 2 — tristate boolean encoding: 1=yes, 0=no, -1=unknown
    # null in dog_profiles means untested/unknown, not false — -1 preserves that.
    good_with_kids_enc = Column(SmallInteger, nullable=True)
    good_with_dogs_enc = Column(SmallInteger, nullable=True)
    good_with_cats_enc = Column(SmallInteger, nullable=True)
    house_trained_enc = Column(SmallInteger, nullable=True)
    vaccinated_enc = Column(SmallInteger, nullable=True)
    spayed_neutered_enc = Column(SmallInteger, nullable=True)

    # Phase 3 — imputed age (zero nulls for ML models that require complete features)
    age_years_imputed = Column(Float, nullable=True)
    age_was_imputed = Column(Boolean, nullable=True)

    # When this row was last computed — useful for staleness checks after re-scrapes.
    computed_at = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))
