"""saved game accounts for one-tap repeat purchases"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_game_accounts"
down_revision = "0004_catalog_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("last_product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "category_id", name="uq_game_account"),
    )
    op.create_index("ix_game_accounts_user_id", "game_accounts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_game_accounts_user_id", "game_accounts")
    op.drop_table("game_accounts")
