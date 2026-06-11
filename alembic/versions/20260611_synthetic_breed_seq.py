"""create_synthetic_breed_id_seq

Creates a descending Postgres sequence that allocates synthetic negative IDs for
unknown breeds inserted into petfinder_breeds at scrape time. DB sequences are
atomic — nextval() is safe across concurrent sessions and never reuses a value,
even on transaction rollback — replacing the client-side min()-1 race.

Revision ID: e5g7c1d36f48
Revises: d4f6b0c25e37
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e5g7c1d36f48'
down_revision: Union[str, Sequence[str], None] = 'd4f6b0c25e37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE SEQUENCE synthetic_breed_id_seq "
        "START WITH -1 INCREMENT BY -1 "
        "MINVALUE -2147483648 NO MAXVALUE NO CYCLE"
    )
    # If the breed_canonical_id backfill already inserted synthetic rows via the
    # old min()-1 path, advance the sequence past them so the first nextval()
    # call lands below all existing negative IDs.
    op.execute("""
        SELECT setval(
            'synthetic_breed_id_seq',
            LEAST(COALESCE(MIN(id), 0), 0) - 1,
            false
        )
        FROM petfinder_breeds
        WHERE id < 0
    """)


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS synthetic_breed_id_seq")
