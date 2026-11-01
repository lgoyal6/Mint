"""Link chat threads to events.

Revision ID: 20260728_0016
Revises: 20260713_0015
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0016"
down_revision = "20260713_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_threads", sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_chat_threads_event_id_events",
        "chat_threads",
        "events",
        ["event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chat_threads_event_id", "chat_threads", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_threads_event_id", table_name="chat_threads")
    op.drop_constraint("fk_chat_threads_event_id_events", "chat_threads", type_="foreignkey")
    op.drop_column("chat_threads", "event_id")
