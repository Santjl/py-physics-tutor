"""add is_active to questionnaires

Revision ID: 0005_add_questionnaire_is_active
Revises: 0004_add_attempt_feedback
Branch Labels: None
Depends on: None
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_add_questionnaire_is_active"
down_revision = "0004_add_attempt_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "questionnaires",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("questionnaires", "is_active")
