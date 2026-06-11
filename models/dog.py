"""
DogProfile — the single source of truth for a scraped dog listing.

Two representations live here intentionally:
  - DogProfile (Pydantic): validates and normalizes raw scraped strings at
    the boundary, before anything touches the DB.
  - DogORM (SQLAlchemy): the persistent record. List fields use JSONB for
    native binary storage and GIN-indexable queries on Postgres.

Pattern: Extract → Validate (Pydantic) → Upsert (ORM).
Classes are ordered to match that pipeline: DogProfile first, then DogORM.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# SQLAlchemy base (shared across all ORM models in this project)
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Pydantic schema (validation layer — never bypass this on the way to the DB)
# ---------------------------------------------------------------------------

class DogProfile(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    source_id: str          # animalId from PetFinder
    source_url: str
    name: str               # animalName
    animal_type: Optional[str] = None              # "Dog" — always Dog for us but stored for completeness
    microchip_id: Optional[str] = None             # microchipId
    internal_notes: Optional[str] = None           # internalNotes (org-internal, often null)
    match_label: Optional[str] = None              # matchLabel
    out_of_town: Optional[bool] = None             # outOfTown
    import_updates_enabled: Optional[bool] = None  # importUpdatesEnabled
    import_deletes_enabled: Optional[bool] = None  # importDeletesEnabled

    # --- PetFinder backend metadata (from SearchAnimal card response meta block) ---
    record_status: Optional[str] = None            # meta.recordStatus e.g. "published"
    petfinder_created_at: Optional[datetime] = None  # meta.create.time — when PetFinder first created the record
    petfinder_updated_at: Optional[datetime] = None  # meta.update.time — PetFinder's own last-modified timestamp

    # --- Physical ---
    breed_primary: str
    breed_secondary: Optional[str] = None
    is_mixed: bool = False
    age_category: str           # our normalized label: puppy | young | adult | senior | unknown
    age_years_approx: Optional[float] = None
    age_label: Optional[str] = None         # PetFinder's own display label e.g. "Adult"
    age_range_label: Optional[str] = None   # PetFinder's range string e.g. "(3-8 years)"
    size: str                   # our normalized label: small | medium | large | xlarge
    weight_min: Optional[int] = None        # lbs, from size.range.min
    weight_max: Optional[int] = None        # lbs, from size.range.max
    weight_range_label: Optional[str] = None  # e.g. "26-60 lbs"
    gender: str                             # lowercased from physical.sex
    color: Optional[str] = None             # physical.color.primary
    color_secondary: Optional[str] = None   # physical.color.secondary
    color_tertiary: Optional[str] = None    # physical.color.tertiary
    coat_length: Optional[str] = None       # physical.coatLength
    declawed: Optional[bool] = None         # physical.declawed (cats mostly, but stored)
    species: Optional[str] = None           # physical.species — always "Dog" for us
    spayed_neutered: Optional[bool] = None  # physical.spayedNeutered
    vaccinated: Optional[bool] = None       # physical.vaccinated
    special_needs: bool = False             # physical.specialNeeds
    special_needs_notes: Optional[str] = None  # physical.specialNeedsNotes
    birth_date: Optional[datetime] = None   # physical.birthDate

    # --- Behavior ---
    house_trained: Optional[bool] = None            # behavior.houseTrained (Yes/No/Unknown → bool)
    activity_level: Optional[str] = None            # behavior.activityLevel
    requires_fenced_yard: Optional[bool] = None     # behavior.requiresFencedYard
    knows_basic_commands: Optional[bool] = None     # behavior.knowsBasicCommands
    behavior_other_animals: Optional[str] = None    # behavior.interactionsOtherAnimals — free-text field, distinct from interactions.otherAnimals
    good_with_kids: Optional[bool] = None           # derived from interactions.childrenUnder8 + children8AndUp
    good_with_dogs: Optional[bool] = None           # behavior.interactions.dogs
    good_with_cats: Optional[bool] = None           # behavior.interactions.cats
    good_with_other_animals: Optional[bool] = None  # behavior.interactions.otherAnimals (Yes/No/Unknown → bool)
    personality_traits: list[str] = []              # behavior.personalityTraits

    # --- Location (foster/listing address, not org HQ) ---
    location_id: Optional[str] = None          # _location.locationId
    location_name: Optional[str] = None        # _location.locationName
    location_type: Optional[str] = None        # _location.locationType e.g. "Default Location"
    location_contact_name: Optional[str] = None  # _location.contactName
    location_email: Optional[str] = None       # _location.email
    location_phone: Optional[str] = None       # _location.phone
    is_appt_only: Optional[bool] = None        # _location.isApptOnly
    is_map_hidden: Optional[bool] = None       # _location.isMapHidden
    is_public_location: Optional[bool] = None  # _location.isPublic
    private_address: Optional[bool] = None     # _location.privateAddress
    location_street: Optional[str] = None      # _location.address.street
    location_street2: Optional[str] = None     # _location.address.street2
    city: Optional[str] = None                 # _location.address.city
    state: Optional[str] = None                # _location.address.state
    zip: Optional[str] = None                  # _location.address.postalCode
    country: Optional[str] = None              # _location.address.country
    lat: Optional[float] = None                # _location.address.latitude (often null)
    lng: Optional[float] = None                # _location.address.longitude (often null)

    # --- Organization ---
    shelter_name: Optional[str] = None         # _organization.organizationName
    org_id: Optional[str] = None               # _organization.organizationId
    org_type: Optional[str] = None             # _organization.organizationType e.g. "Rescue Group / Foster-Based"
    org_custom_url_alias: Optional[str] = None # _organization.customUrlAlias
    org_website: Optional[str] = None          # _organization.website
    org_social_urls: list[str] = []            # _organization.socialUrl
    org_mission_statement: Optional[str] = None  # _organization.missionStatement
    org_onsite_vet: Optional[bool] = None      # _organization.onsiteVet
    org_medical_care_provided: Optional[str] = None   # _organization.medicalCareProvided — free text, not a boolean
    org_supports_rehome: Optional[bool] = None # _organization.supportsRehome
    org_spay_neuter_policy: Optional[str] = None  # _organization.spayNeuterPolicy
    org_special_services: list[str] = []       # _organization.specialServices
    org_adoption_url: Optional[str] = None     # _organization.adoption.adoptionApplUrl
    org_adoption_fee_min: Optional[int] = None # _organization.adoption.adoptionFeeMin
    org_adoption_fee_max: Optional[int] = None # _organization.adoption.adoptionFeeMax
    org_annual_adoptions: Optional[int] = None # _organization.adoption.annualAdoptions
    org_annual_intake: Optional[int] = None    # _organization.adoption.annualIntake
    org_foster_count: Optional[int] = None     # _organization.fosterCount
    org_employee_count: Optional[int] = None   # _organization.employeeCount
    org_volunteer_count: Optional[int] = None  # _organization.volunteerCount
    org_display_id: Optional[str] = None       # _organization.displayId e.g. "NJ708"
    org_animal_id: Optional[str] = None        # organization.organizationAnimalId — shelter's own kennel ID for this dog e.g. "SSRD-A-2483"

    # --- Contact ---
    contact_id: Optional[str] = None           # _contact.contactId
    contact_email: Optional[str] = None        # _contact.email
    contact_first_name: Optional[str] = None   # _contact.firstName
    contact_last_name: Optional[str] = None    # _contact.lastName
    contact_phone: Optional[str] = None        # _contact.phone

    # --- Media ---
    photos: list[str] = []          # image URLs only (quick access list)
    media_records: list[dict] = []  # full _media[] objects — every field PetFinder sends

    # --- Listing content ---
    description: Optional[str] = None
    extended_description: Optional[str] = None   # extendedDescription
    petfinder_notes: Optional[str] = None         # top-level `notes` field
    tags: list[str] = []                          # top-level `tags` (distinct from personality_traits)
    petfinder_url: Optional[str] = None           # publicUrl.url (relative)
    sponsor_a_pet_url: Optional[str] = None       # sponsorAPetUrl.url

    # --- Adoption / status ---
    status: str = "available"               # our normalized label: available | pending | adopted | hold | found | other
    adoption_fee: Optional[int] = None      # residency.adoptionFee
    adoption_fee_waived: Optional[bool] = None   # residency.adoptionFeeWaived
    display_adoption_fee: Optional[bool] = None  # residency.displayAdoptionFee
    adoption_date: Optional[datetime] = None     # residency.adoptionDate
    adoption_status_change_date: Optional[datetime] = None  # residency.adoptionStatusChangeDate
    intake_date: Optional[datetime] = None       # residency.intakeDate
    intake_type: Optional[str] = None            # residency.intakeType
    transfer_date: Optional[datetime] = None     # residency.transferDate
    transfer_from_org_id: Optional[str] = None   # residency.transferFromOrganizationId
    listed_at: Optional[datetime] = None         # residency.publishedAt

    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail_scraped_at: Optional[datetime] = None  # set by detail scraper on successful page visit; None means deep fields not yet populated


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
    animal_type = Column(String(50), nullable=True)
    microchip_id = Column(String(255), nullable=True)
    internal_notes = Column(Text, nullable=True)
    match_label = Column(String(100), nullable=True)
    out_of_town = Column(Boolean, nullable=True)
    import_updates_enabled = Column(Boolean, nullable=True)
    import_deletes_enabled = Column(Boolean, nullable=True)

    # PetFinder backend metadata
    record_status = Column(String(50), nullable=True)
    petfinder_created_at = Column(DateTime(timezone=True), nullable=True)
    petfinder_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Physical
    breed_primary = Column(String(255), nullable=False)
    breed_secondary = Column(String(255), nullable=True)
    is_mixed = Column(Boolean, default=False, nullable=False)
    age_category = Column(String(20), nullable=False)
    age_years_approx = Column(Float, nullable=True)
    age_label = Column(String(50), nullable=True)
    age_range_label = Column(String(50), nullable=True)
    size = Column(String(20), nullable=False)
    weight_min = Column(Float, nullable=True)
    weight_max = Column(Float, nullable=True)
    weight_range_label = Column(String(50), nullable=True)
    gender = Column(String(20), nullable=False)
    color = Column(String(100), nullable=True)
    color_secondary = Column(String(100), nullable=True)
    color_tertiary = Column(String(100), nullable=True)
    coat_length = Column(String(50), nullable=True)
    declawed = Column(Boolean, nullable=True)
    species = Column(String(50), nullable=True)
    spayed_neutered = Column(Boolean, nullable=True)
    vaccinated = Column(Boolean, nullable=True)
    special_needs = Column(Boolean, default=False, nullable=False)
    special_needs_notes = Column(Text, nullable=True)
    birth_date = Column(DateTime(timezone=True), nullable=True)

    # Behavior
    house_trained = Column(Boolean, nullable=True)
    activity_level = Column(String(50), nullable=True)
    requires_fenced_yard = Column(Boolean, nullable=True)
    knows_basic_commands = Column(Boolean, nullable=True)
    behavior_other_animals = Column(Text, nullable=True)
    good_with_kids = Column(Boolean, nullable=True)
    good_with_dogs = Column(Boolean, nullable=True)
    good_with_cats = Column(Boolean, nullable=True)
    good_with_other_animals = Column(Boolean, nullable=True)
    personality_traits = Column(JSONB, default=list, nullable=False)

    # Location
    location_id = Column(String(36), nullable=True)
    location_name = Column(String(255), nullable=True)
    location_type = Column(String(100), nullable=True)
    location_contact_name = Column(String(255), nullable=True)
    location_email = Column(String(255), nullable=True)
    location_phone = Column(String(50), nullable=True)
    is_appt_only = Column(Boolean, nullable=True)
    is_map_hidden = Column(Boolean, nullable=True)
    is_public_location = Column(Boolean, nullable=True)
    private_address = Column(Boolean, nullable=True)
    location_street = Column(Text, nullable=True)
    location_street2 = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip = Column(String(20), nullable=True)
    country = Column(String(10), nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    # Organization
    shelter_name = Column(String(255), nullable=True)
    org_id = Column(String(36), nullable=True)
    org_type = Column(String(100), nullable=True)
    org_custom_url_alias = Column(String(255), nullable=True)
    org_website = Column(Text, nullable=True)
    org_social_urls = Column(JSONB, default=list, nullable=False)
    org_mission_statement = Column(Text, nullable=True)
    org_onsite_vet = Column(Boolean, nullable=True)
    org_medical_care_provided = Column(Text, nullable=True)
    org_supports_rehome = Column(Boolean, nullable=True)
    org_spay_neuter_policy = Column(Text, nullable=True)
    org_special_services = Column(JSONB, default=list, nullable=False)
    org_adoption_url = Column(Text, nullable=True)
    org_adoption_fee_min = Column(Float, nullable=True)
    org_adoption_fee_max = Column(Float, nullable=True)
    org_annual_adoptions = Column(Float, nullable=True)
    org_annual_intake = Column(Float, nullable=True)
    org_foster_count = Column(Float, nullable=True)
    org_employee_count = Column(Float, nullable=True)
    org_volunteer_count = Column(Float, nullable=True)
    org_display_id = Column(String(50), nullable=True)
    org_animal_id = Column(String(100), nullable=True)

    # Contact
    contact_id = Column(String(36), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_first_name = Column(String(255), nullable=True)
    contact_last_name = Column(String(255), nullable=True)
    contact_phone = Column(String(50), nullable=True)

    # Media
    photos = Column(JSONB, default=list, nullable=False)
    media_records = Column(JSONB, default=list, nullable=False)

    # Listing content
    description = Column(Text, nullable=True)
    extended_description = Column(Text, nullable=True)
    petfinder_notes = Column(Text, nullable=True)
    tags = Column(JSONB, default=list, nullable=False)
    petfinder_url = Column(Text, nullable=True)
    sponsor_a_pet_url = Column(Text, nullable=True)

    # Adoption / status
    status = Column(String(20), default="available", nullable=False)
    adoption_fee = Column(Float, nullable=True)
    adoption_fee_waived = Column(Boolean, nullable=True)
    display_adoption_fee = Column(Boolean, nullable=True)
    adoption_date = Column(DateTime(timezone=True), nullable=True)
    adoption_status_change_date = Column(DateTime(timezone=True), nullable=True)
    intake_date = Column(DateTime(timezone=True), nullable=True)
    intake_type = Column(String(50), nullable=True)
    transfer_date = Column(DateTime(timezone=True), nullable=True)
    transfer_from_org_id = Column(String(36), nullable=True)
    listed_at = Column(DateTime(timezone=True), nullable=True)

    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_updated_at = Column(DateTime(timezone=True), nullable=False)
    detail_scraped_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete — set by mark_deleted(), never by the scraper.
    # Dogs are never hard-deleted; set deleted_at to hide from active queries.
    # Valid reasons: "erroneous" | "delisted_by_source" | "duplicate" | "manual"
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deletion_reason = Column(String(50), nullable=True)


# ---------------------------------------------------------------------------
# Raw scrape storage
# ---------------------------------------------------------------------------

class RawScrape(Base):
    """
    Append-only record of the exact JSON payload received from each source,
    saved before any normalization happens.

    If a scraper bug silently drops or mis-maps a field, the raw blob lets
    you re-process historical data without re-scraping the site.
    Also useful for schema drift detection: if PetFinder restructures their
    __NEXT_DATA__, a diff of consecutive raw blobs for the same dog shows
    exactly what changed.

    One row per scrape run per dog — intentionally append-only.
    """
    __tablename__ = "raw_scrapes"
    __table_args__ = (
        Index("ix_raw_scrapes_source_id", "source", "source_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    source_url = Column(Text, nullable=False)
    scraped_at = Column(DateTime(timezone=True), nullable=False)
    raw_json = Column(JSONB, nullable=False)


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

    dog_profile_id references dog_profiles.id. The FK constraint (ON DELETE RESTRICT)
    is enforced at the DB level via the Alembic migration, not declared in the ORM model.
    """
    __tablename__ = "dog_profile_history"
    __table_args__ = (
        Index("ix_dog_profile_history_profile_id", "dog_profile_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # DB-level FK to dog_profiles.id enforced via Alembic migration (ON DELETE RESTRICT).
    # Declared here without ForeignKey() so the constraint lives in the migration layer,
    # not scattered across ORM models — consistent with how all schema constraints are managed.
    dog_profile_id = Column(String(36), nullable=False)
    source = Column(String(50), nullable=False)
    source_id = Column(String(255), nullable=False)
    # When this snapshot was taken — equals the main row's last_updated_at at
    # the moment of archiving, so you can reconstruct "what was true at time T".
    archived_at = Column(DateTime(timezone=True), nullable=False)
    # Live fields — same types as dog_profiles
    status = Column(String(20), nullable=False)
    photos = Column(JSONB, nullable=False)
    media_records = Column(JSONB, nullable=False)
    description = Column(Text, nullable=True)
    extended_description = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=False)
    personality_traits = Column(JSONB, nullable=False)
    good_with_dogs = Column(Boolean, nullable=True)
    good_with_cats = Column(Boolean, nullable=True)
    good_with_kids = Column(Boolean, nullable=True)
    good_with_other_animals = Column(Boolean, nullable=True)
    house_trained = Column(Boolean, nullable=True)
    activity_level = Column(String(50), nullable=True)
    requires_fenced_yard = Column(Boolean, nullable=True)
    vaccinated = Column(Boolean, nullable=True)
    adoption_fee = Column(Float, nullable=True)
    adoption_fee_waived = Column(Boolean, nullable=True)
    shelter_name = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(10), nullable=True)
    zip = Column(String(20), nullable=True)
    listed_at = Column(DateTime(timezone=True), nullable=True)
