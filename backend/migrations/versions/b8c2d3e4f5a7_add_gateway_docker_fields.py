"""Add managed, docker_project_name, config_dir fields to gateways.

Revision ID: b8c2d3e4f5a7
Revises: a9b1c2d3e4f7
Create Date: 2026-03-19 12:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b8c2d3e4f5a7"
down_revision = "a9b1c2d3e4f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add Docker management columns to gateways table."""
    op.add_column(
        "gateways",
        sa.Column(
            "managed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.alter_column("gateways", "managed", server_default=None)

    op.add_column(
        "gateways",
        sa.Column("docker_project_name", sa.String(), nullable=True),
    )
    op.add_column(
        "gateways",
        sa.Column("config_dir", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Remove Docker management columns from gateways table."""
    op.drop_column("gateways", "config_dir")
    op.drop_column("gateways", "docker_project_name")
    op.drop_column("gateways", "managed")
