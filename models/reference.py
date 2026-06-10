"""
Reference and infrastructure ORM models.

These are not dog profile data — they support the scraping pipeline:

  PetFinderBreedORM       canonical breed taxonomy, seeded once from petfinder_breeds.json
  BreedSupplySnapshotORM  time-series of PetFinder-wide breed supply counts (from SearchAnimal facets)
  UrlToVisitORM           queue written by search scraper, consumed by detail scraper

All three share the same Base as DogORM so Alembic manages them in one metadata graph.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from models.dog import Base


class PetFinderBreedORM(Base):
    """
    Canonical breed taxonomy sourced from PetFinder's AllAnimalAttributes GraphQL query.
    309 rows as of 2026-06-02. Seeded once via scripts/seed_breeds.py.

    The integer `id` is PetFinder's own internal breed ID — kept as the PK so our
    rows stay aligned with their system and gaps in the sequence remain visible.
    The `alternate_id` slug is what SearchAnimal uses in filter variables.
    """
    __tablename__ = "petfinder_breeds"

    id = Column(Integer(), primary_key=True)          # PetFinder's own integer ID
    alternate_id = Column(String(100), nullable=False)  # URL slug for SearchAnimal breed filters
    name = Column(String(255), nullable=False)          # display name
    seeded_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("alternate_id", name="uq_petfinder_breeds_alternate_id"),
    )


class BreedSupplySnapshotORM(Base):
    """
    One row per breed per snapshot run. Written by the supply snapshot scraper
    (scrapers/petfinder_supply_snapshot.py) from the facets block of a SearchAnimal
    response — a single GraphQL call returns counts for all 309 breeds at once.

    Query patterns this supports:
      - Per-breed trend:  WHERE breed_alt_id = 'labrador_retriever' ORDER BY snapped_at
      - Point-in-time:    WHERE snapped_at BETWEEN x AND y
    """
    __tablename__ = "breed_supply_snapshots"
    __table_args__ = (
        Index("ix_breed_supply_snapshots_snapped_at", "snapped_at"),
        Index("ix_breed_supply_snapshots_breed_alt_id_snapped_at", "breed_alt_id", "snapped_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapped_at = Column(DateTime(timezone=True), nullable=False)
    breed_name = Column(String(255), nullable=False)   # display name at snapshot time
    breed_alt_id = Column(String(100), nullable=False)  # slug — join key to petfinder_breeds
    count = Column(Integer(), nullable=False)            # available dogs of this breed on PetFinder


class UrlToVisitORM(Base):
    """
    Queue that decouples the search scraper from the detail scraper.

    Search scraper writes a row when:
      - reason='new'     — dog not yet in dog_profiles
      - reason='updated' — petfinder_updated_at has advanced since last detail visit

    Detail scraper reads rows ordered by queued_at, visits each source_url, then
    deletes the row on success. On failure the row stays and is retried next run.

    The unique constraint on (source, source_id) prevents a dog from being queued
    twice if the search scraper runs again before the detail scraper processes it.
    """
    __tablename__ = "urls_to_visit"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_urls_to_visit_source_source_id"),
        Index("ix_urls_to_visit_source_queued_at", "source", "queued_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    source_url = Column(Text(), nullable=False)
    queued_at = Column(DateTime(timezone=True), nullable=False,
                       default=lambda: datetime.now(timezone.utc))
    reason = Column(String(50), nullable=False)   # 'new' | 'updated'
