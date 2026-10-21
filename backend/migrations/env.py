from alembic import context
from app.database import engine, Base
config=context.config
if context.is_offline_mode():
    from app.config import settings
    context.configure(url=settings.database_url,target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    with engine.connect() as conn:
        context.configure(connection=conn,target_metadata=Base.metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
