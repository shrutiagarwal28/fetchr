"""Add breed_group to dog_features (Phase 4).

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a1b2
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dog_features',
        sa.Column('breed_group', sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dog_features', 'breed_group')
