"""Один полный запуск мониторинга: конфиг, метрики, проверки, Excel."""
from datetime import datetime
from enum import nonmember
from pathlib import Path
import os
import pandas as pd

from Code.config import load_config
from Code.load_env import load_env
from Code.logger import create_logger
from Code.report import export_report

from Code.metrics import MetricCollector
from Code.metrics_check import MetricsComparser

from Code.db import ClickHouseConn, MySQLConn
logger = create_logger(__name__)


def _metrics_summary(metric, checked_at: datetime, ) -> str:
    """Готовит фактические значения только для включённых проверок."""
    if metric is None:
        return "Метрики не получены"

    def format_date(value):
        value = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(value):
            return "нет данных", None
        return value.strftime("%d.%m.%Y %H:%M:%S UTC"), value

    lines = []
    primary, replica = metric.primary, metric.replica
    if metric.strategy.row_count:
        p, r = primary.row_count, replica.row_count
        lines.append(f"Строк: основная — {p if p is not None else 'нет данных'}; реплика — {r if r is not None else 'нет данных'}")
        if p is not None and r is not None:
            lines.append(f"Разница: {abs(p - r)} строк")
    if metric.strategy.lag_check:
        p_text, p_date = format_date(primary.max_datetime)
        r_text, r_date = format_date(replica.max_datetime)
        lines.extend([f"Макс. дата основной: {p_text}", f"Макс. дата реплики: {r_text}"])
        if p_date is not None and r_date is not None:
            lines.append(f"Отставание: {(p_date - r_date).total_seconds():.1f} сек.")
    if metric.strategy.last_update:
        for label, source in (("основной", primary), ("реплики", replica)):
            if source is primary and pd.isna(source.update_datetime):
                continue
            date_text, update_date = format_date(source.update_datetime)
            lines.append(f"Обновление {label}: {date_text}")
            if update_date is not None:
                age = (pd.Timestamp(checked_at) - update_date).total_seconds()
                lines.append(f"Возраст обновления {label}: {age:.1f} сек.")
    return "\n".join(lines) if lines else "Проверки отключены"


def main(
        open_folder: bool = True,
        create_xlsx: bool = False,
        mysql_conn: MySQLConn = None,
        clickhouse_conn: ClickHouseConn = None
        ) -> "Path | None":
    """
    Выполняет проверки и возвращает путь к новому отчёту.
    CONFIG_PATH и OUTPUT_ берутся из окружения, загруженного из .env.
    """
    load_env()

    config_path = Path(os.path.expandvars(os.environ["CONFIG_PATH"])).resolve()
    output_dir = Path(os.path.expandvars(os.environ["OUTPUT_"])).resolve()
    config = load_config(config_path)

    # Результаты сбора и ошибки сопоставляются по имени настройки таблицы.
    names = set()
    for index, table in enumerate(config.tables):
        if table.name in names:
            raise ValueError(f"\nYAML tables[{index}].name: повторяющееся имя {table.name}")
        names.add(table.name)

    logger.info("Загружен конфиг %s. Таблиц: %s", config_path, len(config.tables))
    collector = MetricCollector(mysql_conn, clickhouse_conn)
    try:
        metrics = collector.collect_all(config.tables)
    finally:
        collector.close_all()

    metrics_by_name = {metric.table_name: metric for metric in metrics}
    comparser = MetricsComparser(metrics)
    rows = []

    for index, table in enumerate(config.tables):
        checked_at = datetime.now().astimezone()
        if table.name in collector.errors.keys():
            status = "ERROR"
            comment = collector.errors[table.name]
        elif table.name not in metrics_by_name:
            status = "ERROR"
            comment = f"YAML tables[{index}]: метрики таблицы не получены"
            logger.error("%s: %s", table.name, comment)
        else:
            try:
                result = comparser.compare_metrics(metrics_by_name[table.name])
                status = result.status
                comment = result.comment
            except Exception as error:
                status = "ERROR"
                comment = f"YAML tables[{index}]: ошибка проверки: {str(error).strip()}"
                logger.error("%s: %s", table.name, comment)

        rows.append({
            "checked_at": checked_at,
            "table_name": table.name,
            "primary_database": table.primary.database,
            "primary_table": table.primary.table,
            "replica_database": table.replica.database,
            "replica_table": table.replica.table,
            "status": status,
            "comment": comment,
            "metrics": _metrics_summary(metrics_by_name.get(table.name), checked_at),
            **table.strategy.model_dump(),
            **table.rules.model_dump(),
        })

    logger.info(
        "Результаты: OK — %s, WARNING — %s, ERROR — %s",
        sum(row["status"] == "OK" for row in rows),
        sum(row["status"] == "WARNING" for row in rows),
        sum(row["status"] == "ERROR" for row in rows),
    )
    if create_xlsx:
        return export_report(rows, output_dir, open_folder=open_folder)
    else:
        return None


if __name__ == "__main__":
    a = ClickHouseConn()
    b = MySQLConn()
    main(create_xlsx=False)
