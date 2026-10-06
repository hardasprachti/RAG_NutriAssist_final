"""conversations.owner_id: an anonymous per-browser owner, so one visitor cannot list or open another's chats

Existing rows keep a NULL owner. They belong to nobody, so no client can list or open them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06 19:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('conversations') as batch:
        batch.add_column(sa.Column('owner_id', sa.Uuid(), nullable=True))
        batch.create_index('ix_conversations_owner_updated', ['owner_id', 'updated_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('conversations') as batch:
        batch.drop_index('ix_conversations_owner_updated')
        batch.drop_column('owner_id')
