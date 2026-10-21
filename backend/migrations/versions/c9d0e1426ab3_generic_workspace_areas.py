"""Generic areas and retirement of specialized integrations; retain user files."""
from alembic import op
from app.workspace_migration import generalize_workspace
revision='c9d0e1426ab3'
down_revision='b8e50f324a19'
branch_labels=None
depends_on=None

def upgrade():
    generalize_workspace(op.get_bind())

def downgrade():
    # Data transformation is intentionally retained: restoring removed credentials
    # or resuming old work would violate the current authorization boundary.
    pass
