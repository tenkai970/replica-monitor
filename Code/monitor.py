"""Один полный запуск мониторинга: конфиг, метрики, проверки, Excel."""
from dataclasses import dataclass
from pathlib import Path
import os

from Code.config import load_config, get_config_path
from Code.load_env import load_env
from Code.logger import create_logger
from Code.report import export_report

from Code.metrics import MetricCollector, MetricCollectionError
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
        conf_path: str = None,
        mysql_conn: MySQLConn | None = None,
        clickhouse_conn: ClickHouseConn | None = None,
        file_name: str | None = None
        ) -> None:
    """
    Возвращает результаты проверок, строки отчёта и необязательный путь Excel.
    Пресет выбирается через CONFIG_PATH / CONFIG_NAME, отчёт — через OUTPUT_.
    """

    config_path = conf_path if conf_path is not None else get_config_path()
    output_dir = None
    if create_xlsx:
        if not os.getenv("OUTPUT_", "").strip():
            raise ValueError("\nНе задан обязательный параметр .env: OUTPUT_")
        output_dir = Path(os.path.expandvars(os.environ["OUTPUT_"])).expanduser().resolve()
    config = load_config(config_path)

    logger.info("Загружен конфиг %s. Таблиц: %s", config_path, len(config.tables))
    collector = MetricCollector(mysql_conn, clickhouse_conn)
    comparser = MetricsComparser([])
    rows = []
    results = []
    connection_failed = False

    try:
        for index, table in enumerate(config.tables):
            try:
                metric = collector.collect_table_metrics(table)
                result = comparser.compare_metrics(metric)
            except Exception as error:
                if isinstance(error, ConnectionError):
                    connection_failed = True
                location = f"tables[{index}]"
                if isinstance(error, MetricCollectionError):
                    location += f".{error.config_path}"
                    message = error.message
                else:
                    message = str(error).strip()
                comment = f"YAML {location}: {message}"
                logger.error("%s: %s", table.name, comment)
                result = CheckResult(table_name=table.name, status="ERROR", comment=comment)
            logger.info("[%s/%s] %s: %s", index + 1, len(config.tables), table.name, result.status)

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
    
    finally:
        collector.close_all()

    logger.info(
        "Результаты: OK — %s, WARNING — %s, ERROR — %s",
        sum(row["status"] == "OK" for row in rows),
        sum(row["status"] == "WARNING" for row in rows),
        sum(row["status"] == "ERROR" for row in rows),
    )
    status_order = {"ERROR": 0, "WARNING": 1, "OK": 2}
    rows.sort(key=lambda row: row["comment"], reverse=True)
    rows.sort(key=lambda row: status_order[row["status"]])

    if create_xlsx and connection_failed:
        logger.warning("Excel не создан: не удалось подключиться к одной или нескольким базам данных")
    elif create_xlsx:
        export_report(rows, output_dir, open_folder=open_folder, file_name=file_name)
        # report_path =
        # RunResult(results=results, rows=rows, report_path=report_path)
    return None


if __name__ == "__main__":
    load_env()
    run_checks(create_xlsx=True)
