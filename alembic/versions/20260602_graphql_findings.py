"""graphql_findings

Adds columns and tables from the PetFinder GraphQL exploration (2026-06-02):

dog_profiles:
  - record_status, petfinder_created_at, petfinder_updated_at  (meta block from SearchAnimal card)
  - org_animal_id  (shelter's internal kennel ID, per-dog)
  - detail_scraped_at  (stamped by detail scraper on successful page visit)

New tables:
  - petfinder_breeds       canonical breed taxonomy, seeded from petfinder_breeds.json
  - breed_supply_snapshots time-series of PetFinder-wide breed supply counts from SearchAnimal facets
  - urls_to_visit          queue written by search scraper, consumed by detail scraper

Revision ID: a1c3e7f92b04
Revises: 9d6b0f02f6f3
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1c3e7f92b04'
down_revision: Union[str, Sequence[str], None] = '9d6b0f02f6f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # dog_profiles — 5 new columns
    # ------------------------------------------------------------------

    # PetFinder's own backend timestamps from the SearchAnimal card meta block.
    # More reliable than our last_updated_at for change detection because they
    # come from PetFinder's system, not our scraper clock.
    op.add_column('dog_profiles',
        sa.Column('record_status', sa.String(50), nullable=True))
    op.add_column('dog_profiles',
        sa.Column('petfinder_created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('dog_profiles',
        sa.Column('petfinder_updated_at', sa.DateTime(timezone=True), nullable=True))

    # Shelter's own internal kennel ID for this dog (e.g. "SSRD-A-2483").
    # Per-dog field — distinct from org_id which identifies the shelter.
    op.add_column('dog_profiles',
        sa.Column('org_animal_id', sa.String(100), nullable=True))

    # Stamped by the detail scraper on a successful page visit.
    # Null means deep fields (extended_description, full org bio, etc.) have
    # never been populated. The search scraper never touches this column.
    op.add_column('dog_profiles',
        sa.Column('detail_scraped_at', sa.DateTime(timezone=True), nullable=True))

    # Index on petfinder_updated_at — the search scraper filters by this to
    # decide whether to queue a dog for a detail visit.
    op.create_index('ix_dog_profiles_petfinder_updated_at',
        'dog_profiles', ['petfinder_updated_at'])

    # ------------------------------------------------------------------
    # petfinder_breeds — canonical breed taxonomy (309 rows, seeded once)
    # ------------------------------------------------------------------
    op.create_table(
        'petfinder_breeds',
        sa.Column('id', sa.Integer(), primary_key=True),  # PetFinder's own integer ID
        sa.Column('alternate_id', sa.String(100), nullable=False),  # URL slug for SearchAnimal filters
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('seeded_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('alternate_id', name='uq_petfinder_breeds_alternate_id'),
    )

    # ------------------------------------------------------------------
    # breed_supply_snapshots — time-series of PetFinder-wide breed counts
    # ------------------------------------------------------------------
    op.create_table(
        'breed_supply_snapshots',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('snapped_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('breed_name', sa.String(255), nullable=False),
        sa.Column('breed_alt_id', sa.String(100), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
    )
    op.create_index('ix_breed_supply_snapshots_snapped_at',
        'breed_supply_snapshots', ['snapped_at'])
    op.create_index('ix_breed_supply_snapshots_breed_alt_id_snapped_at',
        'breed_supply_snapshots', ['breed_alt_id', 'snapped_at'])

    # ------------------------------------------------------------------
    # urls_to_visit — queue between search scraper and detail scraper
    # ------------------------------------------------------------------
    op.create_table(
        'urls_to_visit',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('source_id', sa.String(255), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=False),
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
        # 'new' — dog not yet in dog_profiles
        # 'updated' — petfinder_updated_at advanced since last detail visit
        sa.Column('reason', sa.String(50), nullable=False),
        sa.UniqueConstraint('source', 'source_id', name='uq_urls_to_visit_source_source_id'),
    )
    # Detail scraper reads this queue ordered by queued_at
    op.create_index('ix_urls_to_visit_source_queued_at',
        'urls_to_visit', ['source', 'queued_at'])


def downgrade() -> None:
    op.drop_table('urls_to_visit')

    op.drop_index('ix_breed_supply_snapshots_breed_alt_id_snapped_at',
        table_name='breed_supply_snapshots')
    op.drop_index('ix_breed_supply_snapshots_snapped_at',
        table_name='breed_supply_snapshots')
    op.drop_table('breed_supply_snapshots')

    op.drop_table('petfinder_breeds')

    op.drop_index('ix_dog_profiles_petfinder_updated_at', table_name='dog_profiles')
    op.drop_column('dog_profiles', 'detail_scraped_at')
    op.drop_column('dog_profiles', 'org_animal_id')
    op.drop_column('dog_profiles', 'petfinder_updated_at')
    op.drop_column('dog_profiles', 'petfinder_created_at')
    op.drop_column('dog_profiles', 'record_status')
