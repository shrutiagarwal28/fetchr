"""
Smoke tests for upsert_dog() — the only write path to dog_profiles.

Requires a running Postgres instance. Set DATABASE_URL before running:

  PYTHONPATH=. python3 tests/test_upsert_dog.py

Each test gets a clean schema via drop_all + create_all, so there is no shared
state between test cases. Point DATABASE_URL at a dedicated test database —
the entire schema is wiped and rebuilt on every test invocation.

Why Postgres and not SQLite:
  - JSONB columns (personality_traits, photos, tags, etc.) behave differently
    from SQLite's TEXT fallback — JSONB containment queries won't even parse on SQLite.
  - DateTime(timezone=True) maps to TIMESTAMPTZ in Postgres; SQLite ignores
    the timezone flag entirely.
  - The FK constraint on dog_profile_history only exists in Postgres.
  Testing against the real backend catches real bugs.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as SessionType

from sqlalchemy.exc import IntegrityError

from models.dog import Base, DogORM, DogProfile, DogProfileHistory
from db.connection import upsert_dog, mark_deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Module-level engine and session kept so _make_session() can close the
# previous session before calling drop_all(). Without this, the previous
# session's implicit transaction (started by the COUNT query in assertions)
# holds an ACCESS SHARE lock on dog_profiles. DROP TABLE needs ACCESS
# EXCLUSIVE, which conflicts — causing Postgres to hang indefinitely.
_engine = None          # sqlalchemy.engine.Engine, created once per process
_current_session: SessionType | None = None


def _make_session() -> SessionType:
    """
    Wipe and recreate the full schema against the test Postgres DB.

    drop_all + create_all gives per-test isolation: every test starts from
    an empty database, the same guarantee the old in-memory SQLite approach
    gave — but now against the actual production backend.
    """
    global _engine, _current_session

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit(
            "DATABASE_URL is not set.\n"
            "Add it to your .env file:\n"
            "  DATABASE_URL=postgresql://shruti@localhost:5432/fetchr_test"
        )

    # Close the previous session so its connection is returned to the pool
    # as idle (not idle-in-transaction). This releases any ACCESS SHARE locks
    # before we attempt the DROP TABLE below.
    if _current_session is not None:
        _current_session.close()

    if _engine is None:
        _engine = create_engine(url)

    Base.metadata.drop_all(_engine)
    Base.metadata.create_all(_engine)

    # The FK on dog_profile_history.dog_profile_id is declared in the Alembic
    # migration, not the ORM model, so create_all() won't add it. Apply it here
    # so the test schema matches the production Alembic-managed schema exactly.
    with _engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE dog_profile_history "
            "ADD CONSTRAINT fk_history_dog_profile_id "
            "FOREIGN KEY (dog_profile_id) REFERENCES dog_profiles(id) "
            "ON DELETE RESTRICT"
        ))
        conn.commit()

    _current_session = sessionmaker(bind=_engine)()
    return _current_session


def _make_profile(**overrides) -> DogProfile:
    """Minimal valid DogProfile. Override any field to set up specific scenarios."""
    base = dict(
        source="petfinder",
        source_id="test-dog-001",
        source_url="https://www.petfinder.com/dog/test-001",
        name="Biscuit",
        breed_primary="Mixed Breed",
        age_category="young",
        size="medium",
        gender="male",
    )
    base.update(overrides)
    return DogProfile(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_new_record() -> None:
    session = _make_session()

    result = upsert_dog(session, _make_profile())

    assert result == "created", f"expected 'created', got {result!r}"
    assert session.query(DogORM).count() == 1
    print("OK  new dog → 'created', 1 row in dog_profiles")


def test_unchanged_on_rescrape() -> None:
    session = _make_session()
    upsert_dog(session, _make_profile())

    result = upsert_dog(session, _make_profile())

    assert result == "unchanged", f"expected 'unchanged', got {result!r}"
    assert session.query(DogORM).count() == 1
    assert session.query(DogProfileHistory).count() == 0, \
        "no history row should be written when nothing changed"
    print("OK  identical rescrape → 'unchanged', no history row written")


def test_updated_archives_old_live_field() -> None:
    # status is a live field — changing it should archive the old state and
    # update the main row.
    session = _make_session()
    upsert_dog(session, _make_profile(status="available"))

    result = upsert_dog(session, _make_profile(status="adopted"))

    assert result == "updated", f"expected 'updated', got {result!r}"
    assert session.query(DogORM).count() == 1
    assert session.query(DogProfileHistory).count() == 1, \
        "one history row should be written for the old state"

    row = session.query(DogORM).first()
    assert row.status == "adopted", \
        f"main row should reflect new status, got {row.status!r}"

    history = session.query(DogProfileHistory).first()
    assert history.status == "available", \
        f"history row should hold old status, got {history.status!r}"
    assert history.dog_profile_id == row.id, \
        "history row must reference the correct dog_profiles.id"
    print("OK  status change → 'updated', old status archived, main row updated")


def test_stable_fields_not_overwritten_on_rescrape() -> None:
    # name and breed_primary are stable — a re-scrape with different values
    # must not clobber them, even when a live field (status) also changes.
    session = _make_session()
    upsert_dog(session, _make_profile(name="Biscuit", breed_primary="Labrador"))

    upsert_dog(session, _make_profile(
        name="WrongName",
        breed_primary="Poodle",
        status="adopted",   # live field change to trigger the update path
    ))

    row = session.query(DogORM).first()
    assert row.name == "Biscuit", \
        f"name must not be overwritten on rescrape, got {row.name!r}"
    assert row.breed_primary == "Labrador", \
        f"breed_primary must not be overwritten on rescrape, got {row.breed_primary!r}"
    print("OK  stable fields (name, breed) preserved after rescrape")


def test_jsonb_list_fields_round_trip() -> None:
    # Verify JSONB columns store and retrieve Python lists correctly on Postgres.
    session = _make_session()
    traits = ["energetic", "friendly", "good with kids"]
    profile = _make_profile(personality_traits=traits, tags=["staff-pick"])

    upsert_dog(session, profile)

    row = session.query(DogORM).first()
    assert row.personality_traits == traits, \
        f"personality_traits JSONB round-trip failed: {row.personality_traits!r}"
    assert row.tags == ["staff-pick"], \
        f"tags JSONB round-trip failed: {row.tags!r}"
    print("OK  JSONB list fields round-trip correctly through Postgres")


def test_timestamptz_is_timezone_aware() -> None:
    # Verify that datetimes written to TIMESTAMPTZ columns come back as
    # timezone-aware (not naive) datetimes from Postgres.
    session = _make_session()
    upsert_dog(session, _make_profile())

    row = session.query(DogORM).first()
    assert row.first_seen_at.tzinfo is not None, \
        "first_seen_at should be timezone-aware after TIMESTAMPTZ round-trip"
    assert row.last_updated_at.tzinfo is not None, \
        "last_updated_at should be timezone-aware after TIMESTAMPTZ round-trip"
    print("OK  TIMESTAMPTZ columns return timezone-aware datetimes")


def test_mark_deleted_sets_fields() -> None:
    session = _make_session()
    upsert_dog(session, _make_profile())

    found = mark_deleted(session, source="petfinder", source_id="test-dog-001",
                         reason="erroneous")

    assert found is True, "mark_deleted should return True when the row exists"
    row = session.query(DogORM).first()
    assert row.deleted_at is not None, "deleted_at must be set after mark_deleted"
    assert row.deleted_at.tzinfo is not None, "deleted_at must be timezone-aware"
    assert row.deletion_reason == "erroneous", \
        f"expected 'erroneous', got {row.deletion_reason!r}"
    assert session.query(DogProfileHistory).count() == 1, \
        "mark_deleted must archive a final snapshot before soft-deleting"
    print("OK  mark_deleted sets deleted_at, deletion_reason, and archives final snapshot")


def test_fk_constraint_rejects_bad_profile_id() -> None:
    # The FK on dog_profile_history.dog_profile_id must reject inserts where
    # the referenced dog_profiles.id does not exist.
    session = _make_session()

    session.add(DogProfileHistory(
        id="hist-bad-001",
        dog_profile_id="nonexistent-id",   # no matching row in dog_profiles
        source="petfinder",
        source_id="test-dog-001",
        archived_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        status="available",
        photos=[], media_records=[], tags=[], personality_traits=[],
    ))
    try:
        session.commit()
        raise AssertionError("expected IntegrityError from FK violation — not raised")
    except IntegrityError:
        session.rollback()
    print("OK  FK constraint rejects history row with non-existent dog_profile_id")


def test_restrict_blocks_hard_delete() -> None:
    # ON DELETE RESTRICT must prevent hard-deleting a dog_profiles row
    # while dog_profile_history rows reference it.
    session = _make_session()
    upsert_dog(session, _make_profile(status="available"))
    mark_deleted(session, source="petfinder", source_id="test-dog-001",
                 reason="erroneous")  # writes one history row

    try:
        session.query(DogORM).filter_by(
            source="petfinder", source_id="test-dog-001"
        ).delete(synchronize_session=False)
        session.commit()
        raise AssertionError("expected IntegrityError from ON DELETE RESTRICT — not raised")
    except IntegrityError:
        session.rollback()
    print("OK  ON DELETE RESTRICT blocks hard delete of dog_profiles row with history")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_create_new_record()
    test_unchanged_on_rescrape()
    test_updated_archives_old_live_field()
    test_stable_fields_not_overwritten_on_rescrape()
    test_jsonb_list_fields_round_trip()
    test_timestamptz_is_timezone_aware()
    test_mark_deleted_sets_fields()
    test_fk_constraint_rejects_bad_profile_id()
    test_restrict_blocks_hard_delete()
    print("\nAll upsert_dog() smoke tests passed.")
