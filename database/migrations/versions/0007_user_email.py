"""store customer email for payment receipts"""
from alembic import op
import sqlalchemy as sa

revision = "0007_user_email"
down_revision = "0006_mulenpay_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "email")
