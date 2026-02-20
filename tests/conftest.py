from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.persistence.pg as pg
from app.core.config import get_settings
from app.persistence.models import Base


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("db") / "test.sqlite"


@pytest.fixture(scope="session", autouse=True)
def configure_test_engine(test_db_path: Path):
    settings = get_settings()
    settings.anchor_mode = "fake"
    settings.receipt_backend = "local"
    settings.receipts_dir = test_db_path.parent / "receipts"

    engine = create_engine(
        f"sqlite+pysqlite:///{test_db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    pg.engine = engine
    pg.SessionLocal = TestSessionLocal

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(configure_test_engine):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def session(configure_test_engine):
    with pg.session_scope() as s:
        yield s
