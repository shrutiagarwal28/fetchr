"""
Smoke tests for upsert_dog() — the only write path to dog_profiles.

Uses an in-memory SQLite database so the real fetchr.db is never touched.
Each test gets its own isolated DB so there is no shared state between cases.

Run with:
  source .venv/bin/activate
  PYTHONPATH=. python3 tests/test_upsert_dog.py
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session as SessionType

from models.dog import Base, DogORM, DogProfile, DogProfileHistory
from db.connection import upsert_dog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session() -> SessionType:
    """Isolated in-memory SQLite DB — discarded when the session is garbage-collected."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


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
    # No live fields change between the two scrapes.
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


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_create_new_record()
    test_unchanged_on_rescrape()
    test_updated_archives_old_live_field()
    test_stable_fields_not_overwritten_on_rescrape()
    print("\nAll upsert_dog() smoke tests passed.")
