"""initial schema: conversations, messages, retrieved_chunks, document_metadata,
failure_logs, evaluation_results (Architecture §9)

Revision ID: 0001
Revises: 
Create Date: 2026-10-05 23:57:47.122590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('conversations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('document_metadata',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('document_name', sa.String(length=255), nullable=False),
    sa.Column('publisher', sa.String(length=255), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('retrieval_date', sa.Date(), nullable=True),
    sa.Column('total_chunks', sa.Integer(), nullable=False),
    sa.Column('embedding_model', sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_url')
    )
    op.create_table('evaluation_results',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('expected_document', sa.String(length=255), nullable=True),
    sa.Column('expected_section', sa.String(length=255), nullable=True),
    sa.Column('hit_at_k', sa.Boolean(), nullable=True),
    sa.Column('k_value', sa.Integer(), nullable=True),
    sa.Column('retrieval_score', sa.Float(), nullable=True),
    sa.Column('citation_valid', sa.Boolean(), nullable=True),
    sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('messages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('conversation_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('structured_response', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_conversation_created', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_table('failure_logs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.Uuid(), nullable=True),
    sa.Column('failure_category', sa.String(length=64), nullable=False),
    sa.Column('user_question', sa.Text(), nullable=False),
    sa.Column('model_response', sa.Text(), nullable=True),
    sa.Column('retrieved_chunks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('error_description', sa.Text(), nullable=True),
    sa.Column('model_info', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_failure_logs_failure_category'), 'failure_logs', ['failure_category'], unique=False)
    op.create_index(op.f('ix_failure_logs_message_id'), 'failure_logs', ['message_id'], unique=False)
    op.create_table('retrieved_chunks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.Uuid(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('chunk_id', sa.String(length=255), nullable=False),
    sa.Column('document_name', sa.String(length=255), nullable=False),
    sa.Column('publisher', sa.String(length=255), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('section', sa.String(length=255), nullable=True),
    sa.Column('chunk_text', sa.Text(), nullable=False),
    sa.Column('similarity_score', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_retrieved_chunks_message_id'), 'retrieved_chunks', ['message_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_retrieved_chunks_message_id'), table_name='retrieved_chunks')
    op.drop_table('retrieved_chunks')
    op.drop_index(op.f('ix_failure_logs_message_id'), table_name='failure_logs')
    op.drop_index(op.f('ix_failure_logs_failure_category'), table_name='failure_logs')
    op.drop_table('failure_logs')
    op.drop_index('ix_messages_conversation_created', table_name='messages')
    op.drop_table('messages')
    op.drop_table('evaluation_results')
    op.drop_table('document_metadata')
    op.drop_table('conversations')
