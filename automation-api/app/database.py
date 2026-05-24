from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateColumn

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_local = None


def get_engine():
    global _engine, _session_local
    if _engine is None:
        url = get_settings().database_url
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url.endswith(":memory:"):
                kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **kwargs)
        _enable_sqlite_foreign_keys(_engine, url)
        _session_local = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _engine


def reset_engine_for_tests(database_url: str = "sqlite+pysqlite:///:memory:") -> None:
    global _engine, _session_local
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        pool_pre_ping=True,
    )
    _enable_sqlite_foreign_keys(_engine, database_url)
    _session_local = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _enable_sqlite_foreign_keys(engine, database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover - exercised by DB setup
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_db() -> None:
    from . import models  # noqa: F401
    from .services.capability_gap_service import ensure_capability_gap_defaults
    from .services.task_version_service import ensure_task_config_version_baseline

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    ensure_additive_schema_columns(engine)
    with Session(get_engine()) as session:
        ensure_task_config_version_baseline(session)
        ensure_capability_gap_defaults(session)


def ensure_additive_schema_columns(engine) -> None:
    """Apply small additive schema compatibility fixes for existing local DBs."""
    required = {
        "capability_proposals": ["implementation_spec"],
        "capability_implementation_plans": ["compiled_plan"],
        "capability_implementation_runs": ["artifacts", "stage_results", "post_deploy_results"],
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table_name, column_names in required.items():
        if table_name not in existing_tables:
            continue
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name in column_names:
            if column_name in existing_columns or column_name not in table.c:
                continue
            column_sql = str(CreateColumn(table.c[column_name]).compile(dialect=engine.dialect))
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))


def get_session() -> Generator[Session, None, None]:
    if _session_local is None:
        get_engine()
    assert _session_local is not None
    session = _session_local()
    try:
        yield session
    finally:
        session.close()
