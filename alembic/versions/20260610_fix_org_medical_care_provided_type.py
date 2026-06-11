"""fix_org_medical_care_provided_type

Changes org_medical_care_provided from BOOLEAN to TEXT.
PetFinder's medicalCareProvided field is a free-text description
(e.g. "Puppies receive vaccinations before traveling"), not a Yes/No flag.

Revision ID: c3e5a9b14d26
Revises: b2d4f8a03c15
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3e5a9b14d26'
down_revision: Union[str, Sequence[str], None] = 'b2d4f8a03c15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'dog_profiles',
        'org_medical_care_provided',
        type_=sa.Text(),
        existing_type=sa.Boolean(),
        postgresql_using='org_medical_care_provided::text',
    )


def downgrade() -> None:
    op.alter_column(
        'dog_profiles',
        'org_medical_care_provided',
        type_=sa.Boolean(),
        existing_type=sa.Text(),
        postgresql_using="org_medical_care_provided::boolean",
    )
