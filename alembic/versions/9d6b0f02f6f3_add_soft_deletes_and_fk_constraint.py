"""add_soft_deletes_and_fk_constraint

Revision ID: 9d6b0f02f6f3
Revises: 4b3514badafe
Create Date: 2026-05-31 00:29:17.788373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d6b0f02f6f3'
down_revision: Union[str, Sequence[str], None] = '4b3514badafe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('dog_profiles',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('dog_profiles',
        sa.Column('deletion_reason', sa.String(50), nullable=True))
    op.create_index('ix_dog_profiles_deleted_at', 'dog_profiles', ['deleted_at'])

    # ON DELETE RESTRICT: Postgres blocks hard deletes of a dog_profiles row
    # while history rows reference it. Soft deletes never issue DELETE, so
    # this is a safety net against accidental hard deletes.
    op.create_foreign_key(
        'fk_history_dog_profile_id',
        'dog_profile_history', 'dog_profiles',
        ['dog_profile_id'], ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_history_dog_profile_id', 'dog_profile_history',
                       type_='foreignkey')
    op.drop_index('ix_dog_profiles_deleted_at', table_name='dog_profiles')
    op.drop_column('dog_profiles', 'deletion_reason')
    op.drop_column('dog_profiles', 'deleted_at')
