"""Persistent agent activity and opt-in bounded area reviews."""
from alembic import op
import sqlalchemy as sa
revision='f836b910e235'
down_revision='d735a809d124'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('agent_jobs',sa.Column('events',sa.JSON(),nullable=False,server_default='[]'))
    op.add_column('agent_jobs',sa.Column('context_sources',sa.JSON(),nullable=False,server_default='[]'))
    op.create_table('agent_schedules',sa.Column('user_id',sa.String(36),sa.ForeignKey('users.id'),primary_key=True),sa.Column('agent_id',sa.String(30),primary_key=True),sa.Column('enabled',sa.Boolean(),nullable=False),sa.Column('interval_minutes',sa.Integer(),nullable=False),sa.Column('next_run',sa.String(40),nullable=False),sa.Column('thread_id',sa.String(36),nullable=False))
def downgrade():
    op.drop_table('agent_schedules')
    op.drop_column('agent_jobs','context_sources')
    op.drop_column('agent_jobs','events')
