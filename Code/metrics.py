from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from Code.config import SourceConfig, TableConfig, load_config
from Code.db import ClickHouseConn, DBcon, MySQLConn
from Code.sql_builder import build_column_exists_query, build_metrics_query


class SourceMetrics(BaseModel):
    """
    Метрики одной таблицы в одной базе.
    """
    model_config = ConfigDict(extra='forbid')

    source_type: str
    database: str
    table: str
    row_count: int | None = None
    max_datetime: Any = None


class TableMetrics(BaseModel):
    """
    Метрики primary и replica для одной настройки таблицы.
    """
    table_name: str
    primary: SourceMetrics
    replica: SourceMetrics


class MetricCollector:
    """
    Собирает метрики через существующие классы подключений из Code.db.
    """
    def __init__(self):
        self._connections: dict[tuple[str, str], DBcon] = {}

    def _get_connection(self, source: SourceConfig) -> DBcon:
        connection_key = (source.type, source.database)

        if connection_key in self._connections:
            return self._connections[connection_key]

        if source.type == "mysql":
            connection = MySQLConn(dbname=source.database)
        elif source.type == "clickhouse":
            connection = ClickHouseConn(dbname=source.database)
        else:
            raise ValueError(f"\nНеподдерживаемый тип источника: {source.type}")

        self._connections[connection_key] = connection
        return connection

    def collect_source_metrics(self, source: SourceConfig) -> SourceMetrics:
        connection = self._get_connection(source)
        self._check_datetime_column_exists(source, connection)

        query = build_metrics_query(source)
        result = connection.query(query)

        if result is None:
            raise RuntimeError(f"\nНе удалось получить метрики для {source.full_name}")

        if result.empty:
            raise RuntimeError(f"\nПустой результат метрик для {source.full_name}")

        row = result.iloc[0].to_dict()

        return SourceMetrics(
            source_type=source.type,
            database=source.database,
            table=source.table,
            row_count=row.get("row_count"),
            max_datetime=row.get("max_datetime"),
        )

    def collect_table_metrics(self, table: TableConfig) -> TableMetrics:
        return TableMetrics(
            table_name=table.name,
            primary=self.collect_source_metrics(table.primary),
            replica=self.collect_source_metrics(table.replica),
        )

    def collect_all(self, tables: list[TableConfig]) -> list[TableMetrics]:
        return [self.collect_table_metrics(table) for table in tables]

    def close_all(self) -> None:
        for connection in self._connections.values():
            connection.close()

        self._connections.clear()

    @staticmethod
    def _check_datetime_column_exists(source: SourceConfig, connection: DBcon) -> None:
        if not source.datetime_column:
            return

        query, params = build_column_exists_query(source)
        result = connection.query(query, params=params)

        if result is None:
            raise RuntimeError(
                f"\nНе удалось проверить колонку {source.datetime_column} для {source.full_name}"
            )

        if result.empty:
            raise RuntimeError(
                f"\nПустой результат проверки колонки {source.datetime_column} для {source.full_name}"
            )
        exists = result.iloc[0].to_dict().get("column_exists")
        column_exists = None if exists is None or pd.isna(exists) else exists

        if not column_exists:
            raise ValueError(
                f"\nКолонка {source.datetime_column} не найдена в таблице {source.full_name}"
            )


if __name__ == "__main__":
    config = load_config()
    collector = MetricCollector()

    try:
        metrics = collector.collect_all(config.tables)
        for table_metrics in metrics:
            print(table_metrics.table_name)
            for table in [table_metrics.primary,table_metrics.replica]:
                print(table)
    finally:
        collector.close_all()
