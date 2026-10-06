"""Alembic environment.

The database URL is taken from the ``sqlalchemy.url`` option when set (tests and
one-off runs), otherwise from DATABASE_URL via the app settings.
"""

from alembic import context

from config import get_settings
from integrations.db_client import create_db_engine, normalize_database_url
from models.db_models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return normalize_database_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_db_engine(_database_url())
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
