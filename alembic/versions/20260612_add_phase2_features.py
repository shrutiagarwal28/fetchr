"""add_phase2_features

Adds six tristate boolean columns to dog_features (Phase 2).
Encoding: 1=yes, 0=no, -1=unknown. Null in dog_profiles means untested,
which is meaningfully different from false — -1 preserves that distinction.

Revision ID: b2c3d4e5f6a1
Revises: a1f2e3d4c5b6
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b2c3d4e5f6a1'
down_revision: Union[str, Sequence[str], None] = 'a1f2e3d4c5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = [
    'good_with_kids_enc',
    'good_with_dogs_enc',
    'good_with_cats_enc',
    'house_trained_enc',
    'vaccinated_enc',
    'spayed_neutered_enc',
]


def upgrade() -> None:
    for col in _NEW_COLUMNS:
        op.add_column('dog_features', sa.Column(col, sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    for col in reversed(_NEW_COLUMNS):
        op.drop_column('dog_features', col)
