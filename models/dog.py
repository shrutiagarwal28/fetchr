"""
DogProfile — the single source of truth for a scraped dog listing.

Two representations live here intentionally:
  - DogProfile (Pydantic): validates and normalizes raw scraped strings at
    the boundary, before anything touches the DB.
  - DogORM (SQLAlchemy): the persistent record. JSON columns store lists
    (photos, tags) since SQLite has no native array type.

Pattern: Extract → Validate (Pydantic) → Upsert (ORM).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# SQLAlchemy base (shared across all ORM models in this project)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------

class DogORM(Base):
    __tablename__ = "dog_profiles"
    __table_args__ = (
        # Dedup key: same dog on the same source is always the same row.
        UniqueConstraint("source", "source_id", name="uq_source_source_id"),
    )

    id = Column(String(36), primary_key=True)
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    source_url = Column(Text, nullable=False)
    name = Column(String(255), nullable=False)
    breed_primary = Column(String(255), nullable=False)
    breed_secondary = Column(String(255), nullable=True)
    is_mixed = Column(Boolean, default=False, nullable=False)
    age_category = Column(String(20), nullable=False)
    age_years_approx = Column(Float, nullable=True)
    size = Column(String(20), nullable=False)
    gender = Column(String(20), nullable=False)
    color = Column(String(100), nullable=True)
    good_with_kids = Column(Boolean, nullable=True)
    good_with_dogs = Column(Boolean, nullable=True)
    good_with_cats = Column(Boolean, nullable=True)
    house_trained = Column(Boolean, nullable=True)
    special_needs = Column(Boolean, default=False, nullable=False)
    energy_level = Column(String(20), default="unknown", nullable=False)
    shelter_name = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip = Column(String(20), nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    # Lists stored as JSON — SQLite has no native array type
    photos = Column(JSON, default=list, nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(JSON, default=list, nullable=False)
    status = Column(String(20), default="available", nullable=False)
    birth_date = Column(DateTime, nullable=True)
    intake_date = Column(DateTime, nullable=True)
    listed_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_updated_at = Column(DateTime, nullable=False)


# ---------------------------------------------------------------------------
# History / audit table
# ---------------------------------------------------------------------------

class DogProfileHistory(Base):
    """
    Append-only archive of a dog's live fields, snapshotted before each update.

    Whenever upsert_dog() detects a change in any live field (status, behavior,
    location, photos, etc.), it writes the *old* values here before overwriting
    the main dog_profiles row. This gives a full timeline of every state change
    — e.g. adoptable → pending → adopted — without bloating the main table.

    dog_profile_id is a soft FK to dog_profiles.id (no DB-level constraint so
    SQLite doesn't need foreign-key pragma to be enabled).
    """
    __tablename__ = "dog_profile_history"
    __table_args__ = (
        Index("ix_dog_profile_history_profile_id", "dog_profile_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    dog_profile_id = Column(String(36), nullable=False)
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    # When this snapshot was taken — equals the main row's last_updated_at at
    # the moment of archiving, so you can reconstruct "what was true at time T".
    archived_at = Column(DateTime, nullable=False)
    # Live fields — same types as dog_profiles
    status = Column(String(20), nullable=False)
    photos = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=False)
    good_with_dogs = Column(Boolean, nullable=True)
    good_with_cats = Column(Boolean, nullable=True)
    good_with_kids = Column(Boolean, nullable=True)
    house_trained = Column(Boolean, nullable=True)
    shelter_name = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip = Column(String(20), nullable=True)
    listed_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Pydantic schema (validation layer — never bypass this on the way to the DB)
# ---------------------------------------------------------------------------

class DogProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    source_id: str
    source_url: str
    name: str
    breed_primary: str
    breed_secondary: Optional[str] = None
    is_mixed: bool = False
    age_category: str  # puppy | young | adult | senior
    age_years_approx: Optional[float] = None
    size: str          # small | medium | large | xlarge
    gender: str
    color: Optional[str] = None
    good_with_kids: Optional[bool] = None
    good_with_dogs: Optional[bool] = None
    good_with_cats: Optional[bool] = None
    house_trained: Optional[bool] = None
    special_needs: bool = False
    energy_level: str = "unknown"  # low | medium | high | unknown
    shelter_name: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    photos: list[str] = []
    description: Optional[str] = None
    tags: list[str] = []
    status: str = "available"  # available | pending | adopted
    birth_date: Optional[datetime] = None       # dog's date of birth (physical.birthDate)
    intake_date: Optional[datetime] = None      # when shelter first took the dog in
    listed_at: Optional[datetime] = None        # when adoption status last changed on PetFinder
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
