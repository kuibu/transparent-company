from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.persistence.models import Base


def create_engine_from_url(url: str):
    return create_engine(url, future=True, pool_pre_ping=True)


settings = get_settings()
engine = create_engine_from_url(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _init_superset_views()


def _init_superset_views() -> None:
    # SQLite in tests doesn't support Postgres-style casting used in the views.
    if not engine.dialect.name.startswith("postgres"):
        return
    sql_file = Path(__file__).resolve().parents[1] / "dashboard" / "superset" / "disclosure_views.sql"
    if not sql_file.exists():
        return
    statements = [chunk.strip() for chunk in sql_file.read_text().split(";") if chunk.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session, None, None]:
    with session_scope() as session:
        yield session
