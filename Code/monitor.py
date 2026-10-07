"""Один полный запуск мониторинга: конфиг, метрики, проверки, Excel."""
from dataclasses import dataclass
from pathlib import Path
import os

from Code.config import load_config, get_config_path
from Code.load_env import load_env
from Code.logger import create_logger
from Code.report import export_report

from Code.metrics import MetricCollector
from Code.metrics_check import MetricsComparser, CheckResult

from Code.db import ClickHouseConn, MySQLConn
logger = create_logger(__name__)


@dataclass
class RunResult:
    results: list[CheckResult]
    rows: list[dict]
    report_path: Path | None = None


def _metrics_summary(result: CheckResult, strategy) -> str:
    """Оформляет уже рассчитанные значения, ничего не пересчитывает."""
    lines = []
    if strategy.row_count:
        lines.append(f"Строк: основная — {result.primary_row_count}; реплика — {result.replica_row_count}")
        if result.row_diff is not None:
            lines.append(f"Разница: {result.row_diff} строк")
    if strategy.lag_check:
        for label, value in (("основной", result.primary_max_datetime_raw), ("реплики", result.replica_max_datetime_raw)):
            lines.append(f"Макс. дата {label}: {value if value is not None else 'нет данных'}")
        if result.lag_seconds is not None:
            lines.append(f"Отставание: {result.lag_seconds:.1f} сек.")
    if strategy.last_update:
        for key, label in (("primary", "основной"), ("replica", "реплики")):
            value = getattr(result, f"{key}_last_update_raw")
            age = getattr(result, f"{key}_update_age_seconds")
            if value is not None:
                lines.append(f"Обновление {label}: {value}")
            if age is not None:
                lines.append(f"Возраст обновления {label}: {age:.1f} сек.")
    for label, value in (("основной", result.primary_collected_at), ("реплики", result.replica_collected_at)):
        if value is not None:
            lines.append(f"Сбор {label}: {value.isoformat()}")
    return "\n".join(lines) if lines else "Нет метрик для включённых проверок"


def run_checks(
        open_folder: bool = True,
        create_xlsx: bool = False,
        mysql_conn: MySQLConn | None = None,
        clickhouse_conn: ClickHouseConn | None = None
        ) -> RunResult:
    """
    Возвращает результаты проверок, строки отчёта и необязательный путь Excel.
    Пресет выбирается через CONFIG_PATH / CONFIG_NAME, отчёт — через OUTPUT_.
    """

    config_path = get_config_path()
    output_dir = None
    if create_xlsx:
        if not os.getenv("OUTPUT_", "").strip():
            raise ValueError("\nНе задан обязательный параметр .env: OUTPUT_")
        output_dir = Path(os.path.expandvars(os.environ["OUTPUT_"])).expanduser().resolve()
    config = load_config(config_path)

    logger.info("Загружен конфиг %s. Таблиц: %s", config_path, len(config.tables))
    collector = MetricCollector(mysql_conn, clickhouse_conn)
    try:
        metrics = collector.collect_all(config.tables)
    finally:
        collector.close_all()

    metrics_by_name = {metric.table_name: metric for metric in metrics}
    comparser = MetricsComparser(metrics)
    rows = []
    results = []

    for index, table in enumerate(config.tables):
        metric = metrics_by_name.get(table.name)
        if metric is None:
            comment = collector.errors.get(table.name, f"YAML tables[{index}]: метрики таблицы не получены")
            result = CheckResult(table_name=table.name, status="ERROR", comment=comment)
        else:
            try:
                result = comparser.compare_metrics(metric)
            except Exception as error:
                comment = f"YAML tables[{index}]: ошибка проверки: {str(error).strip()}"
                logger.error("%s: %s", table.name, comment)
                result = CheckResult(table_name=table.name, status="ERROR", comment=comment)

        results.append(result)
        rows.append({
            "checked_at": result.checked_at,
            "table_name": table.name,
            "primary_database": table.primary.database,
            "primary_table": table.primary.table,
            "replica_database": table.replica.database,
            "replica_table": table.replica.table,
            "status": result.status,
            "comment": result.comment,
            "metrics": _metrics_summary(result, table.strategy),
            **table.strategy.model_dump(),
            **table.rules.model_dump(),
        })

    logger.info(
        "Результаты: OK — %s, WARNING — %s, ERROR — %s",
        sum(row["status"] == "OK" for row in rows),
        sum(row["status"] == "WARNING" for row in rows),
        sum(row["status"] == "ERROR" for row in rows),
    )
    report_path = export_report(rows, output_dir, open_folder=open_folder) if create_xlsx else None
    return RunResult(results=results, rows=rows, report_path=report_path)


if __name__ == "__main__":
    load_env()
    run_checks(create_xlsx=True)
