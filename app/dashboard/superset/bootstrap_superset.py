from __future__ import annotations

import json
import os
import time
from pathlib import Path

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
    ("public", "disclosure_public_daily_kpi_pretty"),
    ("public", "disclosure_public_weekly_kpi_pretty"),
    ("public", "disclosure_public_monthly_kpi_pretty"),
    ("public", "disclosure_investor_revenue_dimension_pretty"),
    ("public", "disclosure_investor_supplier_term_pretty"),
]
DEFAULT_DASHBOARD_TITLE = "David Transparent Supermarket - Trust Dashboard"
DEFAULT_DASHBOARD_SLUG = "david-transparent-supermarket-story"
LEGACY_DASHBOARD_SLUG = "transparent-company-default-story"
TEMPLATE_OUT = Path(
    os.getenv("SUPERSET_TEMPLATE_OUT", "/app/superset_home/david_transparent_supermarket_superset_template.json")
)


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


def _adhoc_sum(column_name: str, label: str) -> dict:
    return {
        "expressionType": "SIMPLE",
        "aggregate": "SUM",
        "column": {"column_name": column_name, "type": "DOUBLE PRECISION"},
        "sqlExpression": None,
        "label": label,
        "hasCustomLabel": True,
        "optionName": f"metric_{column_name}",
    }


def _line_form_data(dataset_id: int, metric_column: str, metric_label: str, y_axis_format: str) -> dict:
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "line",
        "granularity_sqla": "period_start_date",
        "time_grain_sqla": "P1D",
        "metrics": [_adhoc_sum(metric_column, metric_label)],
        "groupby": [],
        "row_limit": 5000,
        "order_desc": False,
        "show_legend": False,
        "x_axis_showminmax": False,
        "y_axis_format": y_axis_format,
    }


def _bar_form_data(dataset_id: int, metric_column: str, metric_label: str, y_axis_format: str) -> dict:
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "dist_bar",
        "groupby": ["period_start_date"],
        "columns": [],
        "metrics": [_adhoc_sum(metric_column, metric_label)],
        "row_limit": 5000,
        "order_desc": False,
        "y_axis_format": y_axis_format,
        "show_legend": False,
        "rotate_x_labels": True,
    }


def _rose_form_data(dataset_id: int, metric_column: str, group_by: str, metric_label: str) -> dict:
    return {
        "datasource": f"{dataset_id}__table",
        "viz_type": "rose",
        "granularity_sqla": "period_start_date",
        "time_grain_sqla": "P1M",
        "groupby": [group_by],
        "metrics": [_adhoc_sum(metric_column, metric_label)],
        "row_limit": 5000,
        "order_desc": False,
        "show_legend": True,
        "y_axis_format": ",.2f",
    }


def ensure_chart(db_session, slice_model, dataset, slice_name: str, viz_type: str, form_data: dict, description: str):
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
            viz_type=viz_type,
            description=description,
        )

    chart.viz_type = viz_type
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
            "children": [],
            "parents": ["ROOT_ID"],
            "meta": {},
        },
    }

    if not charts:
        return json.dumps(layout, separators=(",", ":"), ensure_ascii=True)

    row_chunks = [charts[idx : idx + 3] for idx in range(0, len(charts), 3)]
    for row_idx, row_charts in enumerate(row_chunks, start=1):
        row_id = f"ROW-{row_idx}"
        layout["GRID_ID"]["children"].append(row_id)
        layout[row_id] = {
            "id": row_id,
            "type": "ROW",
            "children": [],
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }

        width = max(3, 12 // max(1, len(row_charts)))
        for chart_idx, chart in enumerate(row_charts, start=1):
            chart_id = f"CHART-{row_idx}-{chart_idx}"
            layout[row_id]["children"].append(chart_id)
            layout[chart_id] = {
                "id": chart_id,
                "type": "CHART",
                "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {"chartId": chart.id, "height": 48, "width": width},
            }

    return json.dumps(layout, separators=(",", ":"), ensure_ascii=True)


def ensure_dashboard(db_session, dashboard_model, charts) -> None:
    dashboard = db_session.query(dashboard_model).filter_by(slug=DEFAULT_DASHBOARD_SLUG).one_or_none()
    legacy_dashboard = db_session.query(dashboard_model).filter_by(slug=LEGACY_DASHBOARD_SLUG).one_or_none()

    if dashboard is None and legacy_dashboard is not None:
        dashboard = legacy_dashboard
    elif dashboard is not None and legacy_dashboard is not None and legacy_dashboard.id != dashboard.id:
        db_session.delete(legacy_dashboard)
        db_session.commit()

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


def _template_payload() -> dict:
    return {
        "template_version": "2.0",
        "dashboard": {"title": DEFAULT_DASHBOARD_TITLE, "slug": DEFAULT_DASHBOARD_SLUG},
        "database": {"name": ANALYTICS_DB_NAME, "sqlalchemy_uri_env": "SUPERSET_ANALYTICS_DB_URI"},
        "datasets": [
            {"schema": "public", "table": "disclosure_public_daily_kpi_pretty"},
            {"schema": "public", "table": "disclosure_public_weekly_kpi_pretty"},
            {"schema": "public", "table": "disclosure_public_monthly_kpi_pretty"},
            {"schema": "public", "table": "disclosure_investor_revenue_dimension_pretty"},
            {"schema": "public", "table": "disclosure_investor_supplier_term_pretty"},
        ],
        "charts": [
            {"name": "Daily Revenue Trend (CNY)", "dataset": "public.disclosure_public_daily_kpi_pretty", "viz_type": "line", "metric": "revenue_yuan"},
            {"name": "Daily Net Operating Cashflow (CNY)", "dataset": "public.disclosure_public_daily_kpi_pretty", "viz_type": "line", "metric": "operating_cash_net_inflow_yuan"},
            {"name": "Daily Average Order Value (CNY)", "dataset": "public.disclosure_public_daily_kpi_pretty", "viz_type": "line", "metric": "avg_order_value_yuan"},
            {"name": "Weekly Repeat Purchase Rate (%)", "dataset": "public.disclosure_public_weekly_kpi_pretty", "viz_type": "line", "metric": "repeat_purchase_rate_pct"},
            {"name": "Weekly QC Fail Rate (%)", "dataset": "public.disclosure_public_weekly_kpi_pretty", "viz_type": "dist_bar", "metric": "qc_fail_rate_pct"},
            {"name": "Weekly Complaint Resolution Hours", "dataset": "public.disclosure_public_weekly_kpi_pretty", "viz_type": "line", "metric": "complaint_resolution_hours_avg"},
            {"name": "Monthly Inventory Turnover Days", "dataset": "public.disclosure_public_monthly_kpi_pretty", "viz_type": "dist_bar", "metric": "inventory_turnover_days"},
            {"name": "Monthly Slow-moving SKU Ratio (%)", "dataset": "public.disclosure_public_monthly_kpi_pretty", "viz_type": "line", "metric": "slow_moving_sku_ratio_pct"},
            {"name": "Promotion Phase Revenue Mix (CNY)", "dataset": "public.disclosure_investor_revenue_dimension_pretty", "viz_type": "rose", "metric": "revenue_yuan", "groupby": ["promotion_phase"]},
            {"name": "Supplier Payment Term Structure (CNY)", "dataset": "public.disclosure_investor_supplier_term_pretty", "viz_type": "rose", "metric": "settlement_yuan", "groupby": ["payment_term_bucket"]},
        ],
    }


def write_template_file() -> None:
    TEMPLATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE_OUT.write_text(json.dumps(_template_payload(), ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_default_assets(db_session, datasets: dict[tuple[str, str], object], slice_model, dashboard_model) -> None:
    daily = datasets[("public", "disclosure_public_daily_kpi_pretty")]
    weekly = datasets[("public", "disclosure_public_weekly_kpi_pretty")]
    monthly = datasets[("public", "disclosure_public_monthly_kpi_pretty")]
    investor_dim = datasets[("public", "disclosure_investor_revenue_dimension_pretty")]
    supplier_term = datasets[("public", "disclosure_investor_supplier_term_pretty")]

    charts = [
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=daily,
            slice_name="Daily Revenue Trend (CNY)",
            viz_type="line",
            form_data=_line_form_data(daily.id, "revenue_yuan", "Revenue (CNY)", ",.2f"),
            description="Public daily disclosure: revenue",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=daily,
            slice_name="Daily Net Operating Cashflow (CNY)",
            viz_type="line",
            form_data=_line_form_data(daily.id, "operating_cash_net_inflow_yuan", "Operating Cashflow (CNY)", ",.2f"),
            description="Public daily disclosure: operating cash net inflow",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=daily,
            slice_name="Daily Average Order Value (CNY)",
            viz_type="line",
            form_data=_line_form_data(daily.id, "avg_order_value_yuan", "Average Order Value (CNY)", ",.2f"),
            description="Public daily disclosure: average order value",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=weekly,
            slice_name="Weekly Repeat Purchase Rate (%)",
            viz_type="line",
            form_data=_line_form_data(weekly.id, "repeat_purchase_rate_pct", "Repeat Purchase Rate (%)", ".2f"),
            description="Public weekly disclosure: repeat purchase rate",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=weekly,
            slice_name="Weekly QC Fail Rate (%)",
            viz_type="dist_bar",
            form_data=_bar_form_data(weekly.id, "qc_fail_rate_pct", "QC Fail Rate (%)", ".2f"),
            description="Public weekly disclosure: quality check fail rate",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=weekly,
            slice_name="Weekly Complaint Resolution Hours",
            viz_type="line",
            form_data=_line_form_data(weekly.id, "complaint_resolution_hours_avg", "Complaint Resolution Hours", ".2f"),
            description="Public weekly disclosure: complaint resolution speed",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=monthly,
            slice_name="Monthly Inventory Turnover Days",
            viz_type="dist_bar",
            form_data=_bar_form_data(monthly.id, "inventory_turnover_days", "Inventory Turnover Days", ".2f"),
            description="Public monthly disclosure: inventory turnover days",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=monthly,
            slice_name="Monthly Slow-moving SKU Ratio (%)",
            viz_type="line",
            form_data=_line_form_data(monthly.id, "slow_moving_sku_ratio_pct", "Slow-moving SKU Ratio (%)", ".2f"),
            description="Public monthly disclosure: slow-moving sku ratio",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=investor_dim,
            slice_name="Promotion Phase Revenue Mix (CNY)",
            viz_type="rose",
            form_data=_rose_form_data(investor_dim.id, "revenue_yuan", "promotion_phase", "Revenue (CNY)"),
            description="Investor disclosure: revenue mix by promotion phase",
        ),
        ensure_chart(
            db_session,
            slice_model=slice_model,
            dataset=supplier_term,
            slice_name="Supplier Payment Term Structure (CNY)",
            viz_type="rose",
            form_data=_rose_form_data(supplier_term.id, "settlement_yuan", "payment_term_bucket", "Settlement (CNY)"),
            description="Supplier payment term structure",
        ),
    ]

    ensure_dashboard(
        db_session,
        dashboard_model=dashboard_model,
        charts=charts,
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
        write_template_file()


if __name__ == "__main__":
    main()
