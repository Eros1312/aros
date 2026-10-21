"""Bounded internal preparation execution and owner command idempotency."""
from alembic import op
import sqlalchemy as sa
revision='b8e50f324a19'
down_revision='a7c49e213f08'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('agent_jobs',sa.Column('command_key',sa.String(36),nullable=True))
    op.add_column('agent_jobs',sa.Column('execution',sa.JSON(),nullable=False,server_default='{}'))
    op.create_index('uq_agent_execution_command','agent_jobs',['user_id','command_key'],unique=True)

def downgrade():
    op.drop_index('uq_agent_execution_command',table_name='agent_jobs')
    op.drop_column('agent_jobs','execution');op.drop_column('agent_jobs','command_key')
