"""Durable runtime heartbeat, bounded inference leases and run origin."""
from alembic import op
import sqlalchemy as sa
revision='a7c49e213f08'
down_revision='f836b910e235'
branch_labels=None
depends_on=None

def upgrade():
    for name,kind,default in [('heartbeat_at',sa.String(40),None),('lease_id',sa.String(36),None),('attempt_count',sa.Integer(),'0'),('retry_after',sa.String(40),None),('is_review',sa.Boolean(),sa.false())]:
        op.add_column('agent_jobs',sa.Column(name,kind,nullable=default is None,server_default=default))
    op.execute(sa.text('UPDATE agent_jobs SET is_review = true WHERE EXISTS (SELECT 1 FROM agent_schedules s WHERE s.user_id = agent_jobs.user_id AND s.agent_id = agent_jobs.agent_id AND s.thread_id = agent_jobs.thread_id)'))
    op.add_column('automation_runs',sa.Column('origin',sa.String(20),nullable=False,server_default='scheduled'))
    op.create_table('worker_heartbeats',sa.Column('name',sa.String(40),primary_key=True),sa.Column('instance_id',sa.String(36),nullable=False),sa.Column('heartbeat_at',sa.String(40),nullable=False),sa.Column('last_cycle_at',sa.String(40)),sa.Column('last_error',sa.Text(),nullable=False,server_default=''))

def downgrade():
    op.drop_table('worker_heartbeats');op.drop_column('automation_runs','origin')
    for name in ['is_review','retry_after','attempt_count','lease_id','heartbeat_at']:op.drop_column('agent_jobs',name)
