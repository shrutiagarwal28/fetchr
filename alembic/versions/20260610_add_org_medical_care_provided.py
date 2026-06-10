"""add_org_medical_care_provided

Adds org_medical_care_provided to dog_profiles.

Sourced from animal._organization.medicalCareProvided in __NEXT_DATA__.
Indicates whether the shelter provides medical care — a shelter quality
signal for the adopter-matching model.

Revision ID: b2d4f8a03c15
Revises: a1c3e7f92b04
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2d4f8a03c15'
down_revision: Union[str, Sequence[str], None] = 'a1c3e7f92b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dog_profiles',
        sa.Column('org_medical_care_provided', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('dog_profiles', 'org_medical_care_provided')
