"""create_dog_features

Creates the dog_features Feature Store table with Phase 1 ordinal encodings:
size_enc, age_category_enc, coat_type_enc. One row per dog (1:1 with dog_profiles).
FK to dog_profiles.id declared here so it's enforced at the DB level.

Revision ID: a1f2e3d4c5b6
Revises: e5g7c1d36f48
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1f2e3d4c5b6'
down_revision: Union[str, Sequence[str], None] = 'e5g7c1d36f48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dog_features',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dog_profile_id', sa.String(36), nullable=False),
        sa.Column('size_enc', sa.SmallInteger(), nullable=True),
        sa.Column('age_category_enc', sa.SmallInteger(), nullable=True),
        sa.Column('coat_type_enc', sa.SmallInteger(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_unique_constraint(
        'uq_dog_features_dog_profile_id',
        'dog_features', ['dog_profile_id'],
    )

    op.create_foreign_key(
        'fk_dog_features_dog_profile_id',
        'dog_features', 'dog_profiles',
        ['dog_profile_id'], ['id'],
        ondelete='CASCADE',
    )

    op.create_index(
        'ix_dog_features_dog_profile_id',
        'dog_features', ['dog_profile_id'],
    )


def downgrade() -> None:
    op.drop_table('dog_features')
