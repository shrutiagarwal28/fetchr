"""add_phase3_features

Adds age imputation columns to dog_features (Phase 3).
age_years_imputed is guaranteed non-null after the backfill script runs —
every dog gets either their real age or a category median substitute.
age_was_imputed flags which rows were imputed so the model can weight them
differently from directly observed ages.

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d4e5f6a1b2'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dog_features', sa.Column('age_years_imputed', sa.Float(), nullable=True))
    op.add_column('dog_features', sa.Column('age_was_imputed', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('dog_features', 'age_was_imputed')
    op.drop_column('dog_features', 'age_years_imputed')
