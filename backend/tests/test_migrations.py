from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from integrations.db_client import create_db_engine
from models.db_models import Base
from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "conversations",
    "messages",
    "retrieved_chunks",
    "document_metadata",
    "failure_logs",
    "evaluation_results",
}


def _tables(url: str) -> set[str]:
    engine = create_db_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_on_fresh_database_creates_all_tables(db_url):
    assert _tables(db_url) == set()
    command.upgrade(alembic_config(db_url), "head")
    assert _tables(db_url) == EXPECTED_TABLES


def test_models_and_migrations_are_in_sync(migrated_url):
    engine = create_db_engine(migrated_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_again(migrated_url):
    cfg = alembic_config(migrated_url)
    command.downgrade(cfg, "base")
    assert _tables(migrated_url) == set()
    command.upgrade(cfg, "head")
    assert _tables(migrated_url) == EXPECTED_TABLES


def test_upgrade_is_idempotent(migrated_url):
    command.upgrade(alembic_config(migrated_url), "head")  # already at head: no-op
    assert _tables(migrated_url) == EXPECTED_TABLES
