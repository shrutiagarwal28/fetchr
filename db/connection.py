"""
DB setup, upsert logic, and JSON export.

upsert_dog() is the only write path — everything funnels through it so
deduplication is always enforced. It returns an action string so callers
can count created/updated/skipped without re-querying.

export_to_json() is a read-only snapshot: it reads all rows from the database
and writes them to a JSON file for use by the audit notebook.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as SessionType

from config import DATABASE_URL, JSON_PATH
from features.age import derive_age_years_approx
from models.dog import Base, DogORM, DogProfile, DogProfileHistory, RawScrape
from models.reference import BreedSupplySnapshotORM, UrlToVisitORM

logger = logging.getLogger(__name__)

# None when DATABASE_URL is unset — callers that inject their own session
# (tests, one-off scripts) can import this module without a running Postgres.
engine = (
    create_engine(
        DATABASE_URL,
        pool_pre_ping=True,   # detect stale connections before handing them out
        pool_size=5,          # baseline connections kept alive between scrape runs
        max_overflow=10,      # burst capacity for parallel future workers
    )
    if DATABASE_URL
    else None
)
Session = sessionmaker(bind=engine) if engine else None


def create_tables() -> None:
    """Create all tables if they don't already exist. Safe to call on every run."""
    Base.metadata.create_all(engine)
    logger.info("Database tables ready")


def save_raw_scrape(
    session: SessionType, source: str, source_id: str, url: str, raw: dict
) -> None:
    """
    Append the raw JSON payload from a scrape to raw_scrapes.

    Called before any normalization so the blob reflects exactly what the
    source sent. One row per scrape run — intentionally not deduplicated.
    Commit failures are logged and swallowed so a raw-save error never
    blocks the main upsert path.
    """
    try:
        session.add(RawScrape(
            id=str(uuid.uuid4()),
            source=source,
            source_id=source_id,
            source_url=url,
            scraped_at=datetime.now(timezone.utc),
            raw_json=raw,
        ))
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to save raw scrape source_id=%s", source_id)


# Fields that reflect the live state of a listing — updated on re-scrape
# and snapshotted to dog_profile_history before each change.
# Kept as a module-level tuple so both _has_live_changes and _archive share it.
_LIVE_FIELDS: tuple[str, ...] = (
    "status", "photos", "media_records", "description", "extended_description",
    "tags", "personality_traits",
    "good_with_dogs", "good_with_cats", "good_with_kids", "good_with_other_animals",
    "house_trained", "activity_level", "requires_fenced_yard", "vaccinated",
    "adoption_fee", "adoption_fee_waived",
    "shelter_name", "city", "state", "zip", "listed_at",
)


def _has_live_changes(existing: DogORM, profile: DogProfile) -> bool:
    """Return True if any live field differs between the stored row and the new profile."""
    return any(getattr(existing, f) != getattr(profile, f) for f in _LIVE_FIELDS)


def _archive_snapshot(session: SessionType, existing: DogORM) -> None:
    """
    Write a snapshot of the current live fields to dog_profile_history.
    archived_at is set to last_updated_at so the history timestamp reflects
    when that state was last confirmed, not when the archiving happened.
    """
    session.add(DogProfileHistory(
        id=str(uuid.uuid4()),
        dog_profile_id=existing.id,
        source=existing.source,
        source_id=existing.source_id,
        archived_at=existing.last_updated_at,
        **{f: getattr(existing, f) for f in _LIVE_FIELDS},
    ))


def upsert_dog(session: SessionType, profile: DogProfile) -> str:
    """
    Insert or update a dog record. Returns "created", "updated", "unchanged",
    or "skipped".

    Dedup key: (source, source_id).

    On re-scrape:
      - If live fields are unchanged, only last_updated_at is bumped → "unchanged".
      - If any live field differs, the old values are archived to
        dog_profile_history before the main row is updated → "updated".

    Stable fields (name, breed, age, gender, etc.) are never overwritten so
    manual DB corrections survive re-runs.
    """
    existing: DogORM | None = (
        session.query(DogORM)
        .filter_by(source=profile.source, source_id=profile.source_id)
        .first()
    )

    if existing is not None:
        if not _has_live_changes(existing, profile):
            existing.last_updated_at = datetime.now(timezone.utc)
            session.commit()
            return "unchanged"

        _archive_snapshot(session, existing)
        for field in _LIVE_FIELDS:
            setattr(existing, field, getattr(profile, field))
        existing.last_updated_at = datetime.now(timezone.utc)
        session.commit()
        return "updated"

    row = DogORM(**profile.model_dump())

    # Derive numeric age at insert time so it's never null on new dogs.
    # We modify the ORM row directly (not profile) to keep the Pydantic input
    # unchanged — consistent with how breed_canonical_id is resolved below.
    if row.age_years_approx is None:
        row.age_years_approx = derive_age_years_approx(
            profile.birth_date, profile.age_range_label
        )

    from models.reference import PetFinderBreedORM
    breed_row = session.query(PetFinderBreedORM).filter_by(name=profile.breed_primary).first()
    if breed_row:
        row.breed_canonical_id = breed_row.id
    else:
        # Unknown breed — not yet in petfinder_breeds (PetFinder adds breeds occasionally).
        # Auto-insert with a synthetic negative ID so breed_canonical_id is never NULL
        # and FK-based matching queries never silently drop this dog.
        # Negative IDs are unambiguously synthetic; PetFinder's own IDs are always positive.
        # When you re-seed and PetFinder officially adds the breed, update the ID + FK.
        logger.warning(
            "Unrecognized breed %r for source_id=%s — auto-inserting into petfinder_breeds "
            "with synthetic negative ID. Re-run scripts/seed_breeds.py to reconcile.",
            profile.breed_primary, profile.source_id,
        )
        slug = profile.breed_primary.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        # nextval() is atomic and never re-issued even on rollback, so concurrent
        # callers never collide. ID is known before the INSERT — no flush() needed.
        synthetic_id = session.execute(
            text("SELECT nextval('synthetic_breed_id_seq')")
        ).scalar()
        session.add(PetFinderBreedORM(
            id=synthetic_id,
            alternate_id=f"auto_{slug}",
            name=profile.breed_primary,
            seeded_at=datetime.now(timezone.utc),
        ))
        row.breed_canonical_id = synthetic_id
    session.add(row)
    try:
        session.commit()
        return "created"
    except Exception:
        session.rollback()
        logger.exception("Failed to insert dog source_id=%s", profile.source_id)
        return "skipped"


# Fields the search scraper (card-level data) is allowed to update.
# Deliberately excludes detail-page-only fields so a search scraper run
# never wipes data the detail scraper spent time collecting.
#
# Excluded from card updates — detail scraper owns these:
#   description, extended_description, media_records, tags, personality_traits,
#   city, state, zip, listed_at, detail_scraped_at,
#   house_trained, activity_level, requires_fenced_yard, vaccinated,
#   good_with_dogs, good_with_cats, good_with_kids, good_with_other_animals.
#
# Behavioral fields are excluded because any PetFinder update to them bumps
# petfinder_updated_at, which already queues the dog for a full detail visit.
# Writing them from the card response would archive-then-overwrite on each
# explore run, generating spurious history entries with no queueing benefit.
_CARD_LIVE_FIELDS: tuple[str, ...] = (
    "status",
    "adoption_date",
    "photos",
    "shelter_name",
    "petfinder_created_at", "petfinder_updated_at", "record_status",
    "org_animal_id",
)


def upsert_card(session: SessionType, profile: DogProfile) -> str:
    """
    Insert or update a dog record from search card (GraphQL) data.

    Identical dedup key to upsert_dog — (source, source_id) — but only
    updates _CARD_LIVE_FIELDS on an existing row. This means a search scraper
    run never overwrites description, extended_description, media_records, or
    other detail-page-only fields that upsert_dog populates.

    History archiving only triggers when status changes — that is the
    operationally meaningful transition worth recording from card data.

    Returns "created", "updated", "unchanged", or "skipped".
    """
    existing: DogORM | None = (
        session.query(DogORM)
        .filter_by(source=profile.source, source_id=profile.source_id)
        .first()
    )

    if existing is not None:
        changed = any(
            getattr(existing, f) != getattr(profile, f)
            for f in _CARD_LIVE_FIELDS
        )
        if not changed:
            existing.last_updated_at = datetime.now(timezone.utc)
            session.commit()
            return "unchanged"

        # Archive history only when status changes — the key lifecycle event.
        if existing.status != profile.status:
            _archive_snapshot(session, existing)

        for field in _CARD_LIVE_FIELDS:
            setattr(existing, field, getattr(profile, field))
        existing.last_updated_at = datetime.now(timezone.utc)
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


def queue_for_detail_visit(
    session: SessionType,
    source: str,
    source_id: str,
    source_url: str,
    reason: str,
) -> None:
    """
    Add or refresh a dog in the urls_to_visit queue.

    If the dog is already queued (unique constraint on source + source_id),
    the existing row is updated with the latest queued_at and reason so the
    detail scraper always processes the most recent trigger.

    reason: 'new' — dog not yet in dog_profiles
            'updated' — petfinder_updated_at advanced since last detail visit
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(UrlToVisitORM)
        .values(
            id=str(uuid.uuid4()),
            source=source,
            source_id=source_id,
            source_url=source_url,
            queued_at=datetime.now(timezone.utc),
            reason=reason,
        )
        .on_conflict_do_update(
            constraint="uq_urls_to_visit_source_source_id",
            set_={
                "source_url": source_url,
                "queued_at": datetime.now(timezone.utc),
                "reason": reason,
            },
        )
    )
    session.execute(stmt)


def dequeue_detail_visit(
    session: SessionType, source: str, source_id: str
) -> None:
    """
    Remove a dog from the urls_to_visit queue after a successful detail visit.
    Called by the detail scraper immediately after stamping detail_scraped_at.
    """
    session.query(UrlToVisitORM).filter_by(
        source=source, source_id=source_id
    ).delete()


def stamp_detail_scraped_at(
    session: SessionType, source: str, source_id: str
) -> None:
    """
    Record that the detail scraper successfully visited and parsed this dog's
    detail page. Only called on success — a failed visit leaves this null
    so the dog stays in the queue and is retried on the next run.
    """
    session.query(DogORM).filter_by(source=source, source_id=source_id).update(
        {"detail_scraped_at": datetime.now(timezone.utc)}
    )


def fetch_queued_urls(
    session: SessionType, source: str, limit: int
) -> list[tuple[str, str]]:
    """
    Return up to `limit` queued detail-visit URLs for the given source,
    ordered oldest-first. Rows are NOT deleted here — each is removed by
    dequeue_detail_visit() after the detail scraper confirms success.
    """
    rows = (
        session.query(UrlToVisitORM)
        .filter_by(source=source)
        .order_by(UrlToVisitORM.queued_at.asc())
        .limit(limit)
        .all()
    )
    return [(row.source_id, row.source_url) for row in rows]


def mark_deleted(
    session: SessionType,
    source: str,
    source_id: str,
    reason: str,
) -> bool:
    """
    Soft-delete a dog profile.

    Archives a final snapshot of the current live fields to dog_profile_history
    before setting deleted_at, so the last known state is always preserved.
    Returns True if the row was found and marked, False if not found.

    Valid reason values: "erroneous", "delisted_by_source", "duplicate", "manual".
    Dogs soft-deleted this way are excluded from export_to_json() and active
    listing queries (WHERE deleted_at IS NULL) but remain in dog_profiles for
    audit and ML training purposes.
    """
    existing: DogORM | None = (
        session.query(DogORM)
        .filter_by(source=source, source_id=source_id)
        .first()
    )
    if existing is None:
        logger.warning("mark_deleted: no row found for %s/%s", source, source_id)
        return False

    _archive_snapshot(session, existing)
    existing.deleted_at = datetime.now(timezone.utc)
    existing.deletion_reason = reason
    existing.last_updated_at = datetime.now(timezone.utc)
    session.commit()
    logger.info("Soft-deleted %s/%s reason=%s", source, source_id, reason)
    return True


def export_to_json(path: str = JSON_PATH, include_deleted: bool = False) -> int:
    """
    Snapshot all dog records from the database into a JSON file.

    Uses an atomic write (temp file → os.replace) so a crash mid-export
    never leaves a half-written file at the destination path.

    Returns the number of records written.
    """
    with Session() as session:
        q = session.query(DogORM)
        if not include_deleted:
            q = q.filter(DogORM.deleted_at.is_(None))
        rows: list[DogORM] = q.all()

    records = []
    for row in rows:
        # Strip SQLAlchemy's internal tracking key before converting to Pydantic.
        row_dict = {k: v for k, v in row.__dict__.items() if not k.startswith("_")}
        # model_dump(mode="json") converts datetime → ISO string automatically.
        profile = DogProfile.model_validate(row_dict)
        records.append(profile.model_dump(mode="json"))

    dest = Path(path)
    dir_ = dest.parent
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
        json.dump(records, f, indent=2)
        tmp_path = f.name

    os.replace(tmp_path, str(dest))
    logger.info("Exported %d records to %s", len(records), path)
    return len(records)
