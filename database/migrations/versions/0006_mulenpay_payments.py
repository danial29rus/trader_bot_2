"""store MulenPay payment details"""
from alembic import op
import sqlalchemy as sa


revision = "0006_mulenpay_payments"
down_revision = "0005_game_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("provider_payment_id", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("payment_url", sa.Text(), nullable=True))
    op.add_column("payments", sa.Column("provider_status", sa.Integer(), nullable=True))
    op.add_column("payments", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_payments_provider_payment_id", "payments", ["provider_payment_id"])


def downgrade() -> None:
    op.drop_constraint("uq_payments_provider_payment_id", "payments", type_="unique")
    op.drop_column("payments", "last_checked_at")
    op.drop_column("payments", "provider_status")
    op.drop_column("payments", "payment_url")
    op.drop_column("payments", "provider_payment_id")
