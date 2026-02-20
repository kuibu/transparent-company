from __future__ import annotations

import os
import time

from sqlalchemy import create_engine, text

from superset.app import create_app


ANALYTICS_DB_NAME = os.getenv("SUPERSET_ANALYTICS_DB_NAME", "TransparentCompanyPG")
ANALYTICS_DB_URI = os.getenv(
    "SUPERSET_ANALYTICS_DB_URI",
    "postgresql+psycopg2://tc:tc@postgres:5432/tc",
)
DATASETS = [
    ("public", "disclosure_runs"),
    ("public", "disclosure_metrics"),
    ("public", "disclosure_grouped_metrics"),
    ("public", "disclosure_public_daily"),
    ("public", "disclosure_investor_grouped"),
]


def wait_for_analytics_db(sqlalchemy_uri: str, retries: int = 30, interval_seconds: int = 2) -> None:
    engine = create_engine(sqlalchemy_uri, pool_pre_ping=True)
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("select 1"))
            return
        except Exception as exc:  # pragma: no cover - startup-time resilience
            last_error = exc
            time.sleep(interval_seconds)
    raise RuntimeError(f"analytics database is not reachable: {last_error!r}")


def ensure_database(db_session, database_model):
    database = db_session.query(database_model).filter_by(database_name=ANALYTICS_DB_NAME).one_or_none()
    if database is None:
        database = database_model(
            database_name=ANALYTICS_DB_NAME,
            sqlalchemy_uri=ANALYTICS_DB_URI,
            expose_in_sqllab=True,
        )
        db_session.add(database)
    else:
        database.sqlalchemy_uri = ANALYTICS_DB_URI
        database.expose_in_sqllab = True
    db_session.commit()
    return database


def ensure_dataset(db_session, sqla_table_model, database, schema: str, table_name: str) -> None:
    dataset = (
        db_session.query(sqla_table_model)
        .filter(sqla_table_model.database_id == database.id)
        .filter(sqla_table_model.schema == schema)
        .filter(sqla_table_model.table_name == table_name)
        .one_or_none()
    )
    if dataset is None:
        dataset = sqla_table_model(table_name=table_name, schema=schema, database=database)
        db_session.add(dataset)
        db_session.commit()

    # Metadata refresh can fail if source tables/views are not ready yet.
    # Keep bootstrap idempotent and non-fatal; user can refresh later in UI.
    try:
        dataset.fetch_metadata()
        db_session.commit()
    except Exception:
        db_session.rollback()


def main() -> None:
    wait_for_analytics_db(ANALYTICS_DB_URI)
    app = create_app()
    with app.app_context():
        from superset.connectors.sqla.models import SqlaTable
        from superset.extensions import db
        from superset.models.core import Database

        database = ensure_database(db.session, Database)
        for schema, table_name in DATASETS:
            ensure_dataset(db.session, SqlaTable, database, schema=schema, table_name=table_name)


if __name__ == "__main__":
    main()
