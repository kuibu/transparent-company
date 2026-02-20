from __future__ import annotations

import json
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
DEFAULT_DASHBOARD_TITLE = "Transparent Company - Default Story"
DEFAULT_DASHBOARD_SLUG = "transparent-company-default-story"


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


def wait_for_relations(sqlalchemy_uri: str, relations: list[tuple[str, str]], retries: int = 60, interval_seconds: int = 2) -> None:
    engine = create_engine(sqlalchemy_uri, pool_pre_ping=True)
    last_missing: list[tuple[str, str]] = []

    for _ in range(retries):
        missing: list[tuple[str, str]] = []
        with engine.connect() as conn:
            for schema, relation in relations:
                found = conn.execute(
                    text(
                        """
                        select (
                            exists (
                                select 1
                                from information_schema.tables
                                where table_schema = :schema and table_name = :relation
                            )
                            or exists (
                                select 1
                                from information_schema.views
                                where table_schema = :schema and table_name = :relation
                            )
                        )
                        """
                    ),
                    {"schema": schema, "relation": relation},
                ).scalar()
                if not found:
                    missing.append((schema, relation))
        if not missing:
            return
        last_missing = missing
        time.sleep(interval_seconds)

    raise RuntimeError(f"analytics relations are not ready: {last_missing}")


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


def ensure_dataset(db_session, sqla_table_model, database, schema: str, table_name: str):
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

    try:
        dataset.fetch_metadata()
        db_session.commit()
    except Exception:
        db_session.rollback()

    return dataset


def _simple_filter(subject: str, comparator: str) -> dict:
    return {
        "clause": "WHERE",
        "expressionType": "SIMPLE",
        "subject": subject,
        "operator": "==",
        "comparator": comparator,
    }


def _table_form_data(dataset_id: int, columns: list[str], metric_key: str) -> dict:
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "table",
        "all_columns": columns,
        "adhoc_filters": [_simple_filter("metric_key", metric_key)],
        "row_limit": 5000,
        "server_pagination": False,
        "order_desc": True,
    }


def ensure_chart(db_session, slice_model, dataset, slice_name: str, form_data: dict, description: str):
    chart = (
        db_session.query(slice_model)
        .filter(slice_model.slice_name == slice_name)
        .filter(slice_model.datasource_type == "table")
        .filter(slice_model.datasource_id == dataset.id)
        .one_or_none()
    )
    if chart is None:
        chart = slice_model(
            slice_name=slice_name,
            datasource_id=dataset.id,
            datasource_type="table",
            datasource_name=f"{dataset.schema}.{dataset.table_name}",
            viz_type="table",
            description=description,
        )

    chart.viz_type = "table"
    chart.params = json.dumps(form_data, separators=(",", ":"), ensure_ascii=True)
    chart.query_context = None
    chart.datasource_name = f"{dataset.schema}.{dataset.table_name}"
    chart.description = description

    db_session.add(chart)
    db_session.commit()
    return chart


def _build_dashboard_layout(charts) -> str:
    layout = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"id": "ROOT_ID", "type": "ROOT", "children": ["GRID_ID"], "meta": {}},
        "GRID_ID": {
            "id": "GRID_ID",
            "type": "GRID",
            "children": ["ROW-1"],
            "parents": ["ROOT_ID"],
            "meta": {},
        },
        "ROW-1": {
            "id": "ROW-1",
            "type": "ROW",
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        },
    }

    count = max(1, len(charts))
    width = max(3, 12 // count)

    for idx, chart in enumerate(charts, start=1):
        chart_id = f"CHART-{idx}"
        layout["ROW-1"]["children"].append(chart_id)
        layout[chart_id] = {
            "id": chart_id,
            "type": "CHART",
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID", "ROW-1"],
            "meta": {"chartId": chart.id, "height": 50, "width": width},
        }

    return json.dumps(layout, separators=(",", ":"), ensure_ascii=True)


def ensure_dashboard(db_session, dashboard_model, charts) -> None:
    dashboard = db_session.query(dashboard_model).filter_by(slug=DEFAULT_DASHBOARD_SLUG).one_or_none()
    if dashboard is None:
        dashboard = dashboard_model(
            dashboard_title=DEFAULT_DASHBOARD_TITLE,
            slug=DEFAULT_DASHBOARD_SLUG,
            published=True,
        )

    dashboard.dashboard_title = DEFAULT_DASHBOARD_TITLE
    dashboard.slug = DEFAULT_DASHBOARD_SLUG
    dashboard.published = True
    dashboard.position_json = _build_dashboard_layout(charts)
    dashboard.json_metadata = json.dumps({"color_scheme": "supersetColors"}, separators=(",", ":"), ensure_ascii=True)
    dashboard.slices = charts

    db_session.add(dashboard)
    db_session.commit()


def ensure_default_assets(db_session, datasets: dict[tuple[str, str], object], slice_model, dashboard_model) -> None:
    public_daily = datasets[("public", "disclosure_public_daily")]
    investor_grouped = datasets[("public", "disclosure_investor_grouped")]

    chart_daily_revenue = ensure_chart(
        db_session,
        slice_model=slice_model,
        dataset=public_daily,
        slice_name="Public Daily Revenue",
        form_data=_table_form_data(public_daily.id, ["period_day", "value"], metric_key="revenue_cents"),
        description="Public disclosure: daily revenue in cents",
    )
    chart_refund_rate = ensure_chart(
        db_session,
        slice_model=slice_model,
        dataset=public_daily,
        slice_name="Public Daily Refund Rate (bps)",
        form_data=_table_form_data(public_daily.id, ["period_day", "value"], metric_key="refund_rate_bps"),
        description="Public disclosure: daily refund rate in basis points",
    )
    chart_investor_mix = ensure_chart(
        db_session,
        slice_model=slice_model,
        dataset=investor_grouped,
        slice_name="Investor Revenue Mix (Channel + SKU)",
        form_data=_table_form_data(
            investor_grouped.id,
            ["period_day", "channel", "sku", "value"],
            metric_key="revenue_cents",
        ),
        description="Investor disclosure: grouped revenue by channel and SKU",
    )

    ensure_dashboard(
        db_session,
        dashboard_model=dashboard_model,
        charts=[chart_daily_revenue, chart_refund_rate, chart_investor_mix],
    )


def main() -> None:
    wait_for_analytics_db(ANALYTICS_DB_URI)
    wait_for_relations(ANALYTICS_DB_URI, DATASETS)

    app = create_app()
    with app.app_context():
        from superset.connectors.sqla.models import SqlaTable
        from superset.extensions import db
        from superset.models.core import Database
        from superset.models.dashboard import Dashboard
        from superset.models.slice import Slice

        database = ensure_database(db.session, Database)
        dataset_map: dict[tuple[str, str], object] = {}
        for schema, table_name in DATASETS:
            dataset_map[(schema, table_name)] = ensure_dataset(
                db.session,
                SqlaTable,
                database,
                schema=schema,
                table_name=table_name,
            )

        ensure_default_assets(
            db_session=db.session,
            datasets=dataset_map,
            slice_model=Slice,
            dashboard_model=Dashboard,
        )


if __name__ == "__main__":
    main()
