"""Persistent AI model choice and immutable model provenance."""
from alembic import op
import sqlalchemy as sa
revision='b1931eaa0294'
down_revision='792f5146bd79'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('users',sa.Column('ai_model',sa.String(240),nullable=False,server_default='local'))
    op.add_column('agent_jobs',sa.Column('model',sa.String(240),nullable=False,server_default='local'))
    op.add_column('ai_messages',sa.Column('model',sa.String(240),nullable=False,server_default='local'))
    op.execute("UPDATE ai_messages SET model='local' WHERE mode='openclaw'")

def downgrade():
    op.drop_column('ai_messages','model')
    op.drop_column('agent_jobs','model')
    op.drop_column('users','ai_model')
