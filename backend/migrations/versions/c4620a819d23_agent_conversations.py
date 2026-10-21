"""Agent conversations and reviewable resource actions."""
from alembic import op
import sqlalchemy as sa
revision='c4620a819d23'
down_revision='b1931eaa0294'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('agent_jobs',sa.Column('thread_id',sa.String(36),nullable=False,server_default='main'))
    op.create_index('ix_agent_jobs_thread_id','agent_jobs',['thread_id'])
    op.add_column('agent_jobs',sa.Column('actions',sa.JSON(),nullable=False,server_default='[]'))
def downgrade():
    op.drop_column('agent_jobs','actions')
    op.drop_index('ix_agent_jobs_thread_id',table_name='agent_jobs')
    op.drop_column('agent_jobs','thread_id')
