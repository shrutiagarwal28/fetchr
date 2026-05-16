"""
DB setup and upsert logic.

upsert_dog() is the only write path — everything funnels through it so
deduplication is always enforced. It returns an action string so callers
can count created/updated/skipped without re-querying.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session as SessionType

from config import DB_PATH
from models.dog import Base, DogORM, DogProfile

logger = logging.getLogger(__name__)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine)


def create_tables() -> None:
    """Create all tables if they don't already exist. Safe to call on every run."""
    Base.metadata.create_all(engine)
    logger.info("Database ready at %s", DB_PATH)


def upsert_dog(session: SessionType, profile: DogProfile) -> str:
    """
    Insert or update a dog record. Returns "created", "updated", or "skipped".

    Dedup key: (source, source_id). If a row already exists we only touch
    last_updated_at — we don't overwrite fields that a human might have
    corrected manually in the DB.
    """
    existing: DogORM | None = (
        session.query(DogORM)
        .filter_by(source=profile.source, source_id=profile.source_id)
        .first()
    )

    if existing is not None:
        existing.last_updated_at = datetime.utcnow()
        session.commit()
        return "updated"

    row = DogORM(**profile.model_dump())
    session.add(row)
    try:
        session.commit()
        return "created"
    except Exception:
        session.rollback()
        logger.exception("Failed to insert dog source_id=%s", profile.source_id)
        return "skipped"
