"""Initial application tables."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_sessions_owner", "login_sessions", ["owner"])
    op.create_index("ix_login_sessions_expires", "login_sessions", ["expires"])
    op.create_table(
        "matrices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("latest", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "name"),
    )
    op.create_table(
        "matrix_versions",
        sa.Column("matrix_id", sa.String(32), sa.ForeignKey("matrices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "history",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("matrix_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed", sa.Integer(), nullable=False),
        sa.Column("label", sa.LargeBinary()),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(("matrix_id", "version"), ("matrix_versions.matrix_id", "matrix_versions.number")),
    )
    op.create_index("ix_history_owner_created", "history", ["owner", "created"])
    op.create_index("ix_history_created", "history", ["created"])


def downgrade():
    for table in ("history", "matrix_versions", "matrices", "login_sessions", "users"):
        op.drop_table(table)
