"""Connector revisions and recoverable synchronization leases."""
from alembic import op
import sqlalchemy as sa
revision='d735a809d124'
down_revision='c4620a819d23'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('connectors',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('connectors',sa.Column('sync_started_at',sa.String(40),nullable=True))
def downgrade():
    op.drop_column('connectors','sync_started_at')
    op.drop_column('connectors','revision')
