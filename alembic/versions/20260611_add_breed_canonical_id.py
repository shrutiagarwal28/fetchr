"""add_breed_canonical_id

Adds breed_canonical_id to dog_profiles as a FK to petfinder_breeds.id.
Backfills existing rows by exact name match on breed_primary — 100% coverage
confirmed on current data since both come from PetFinder's own taxonomy.

Revision ID: d4f6b0c25e37
Revises: c3e5a9b14d26
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4f6b0c25e37'
down_revision: Union[str, Sequence[str], None] = 'c3e5a9b14d26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dog_profiles',
        sa.Column('breed_canonical_id', sa.Integer(), nullable=True),
    )

    # DISTINCT ON (name) ORDER BY name, id picks the lowest id deterministically
    # if petfinder_breeds ever has two rows with the same display name.
    op.execute("""
        UPDATE dog_profiles dp
        SET breed_canonical_id = sub.id
        FROM (
            SELECT DISTINCT ON (name) id, name
            FROM petfinder_breeds
            ORDER BY name, id
        ) sub
        WHERE sub.name = dp.breed_primary
    """)

    op.create_foreign_key(
        'fk_dog_profiles_breed_canonical_id',
        'dog_profiles', 'petfinder_breeds',
        ['breed_canonical_id'], ['id'],
    )

    op.create_index(
        'ix_dog_profiles_breed_canonical_id',
        'dog_profiles', ['breed_canonical_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_dog_profiles_breed_canonical_id', table_name='dog_profiles')
    op.drop_constraint('fk_dog_profiles_breed_canonical_id', 'dog_profiles', type_='foreignkey')
    op.drop_column('dog_profiles', 'breed_canonical_id')
