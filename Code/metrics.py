from typing import Any
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from Code.config import SourceConfig, TableConfig,StrategyConfig,RulesConfig,load_config
from Code.db import ClickHouseConn, DBcon, MySQLConn
from Code.logger import create_logger
from Code.sql_builder import build_column_exists_query, build_metrics_query
import pandas as pd

logger = create_logger(__name__)


class MetricCollectionError(RuntimeError):
    """
    Ошибка сбора метрик с путём к полю или блоку YAML-конфига.
    """
    def __init__(self, config_path: str, message: str):
        self.config_path = config_path
        self.message = message.strip()
        super().__init__(f"\nYAML {config_path}: {self.message}")


class SourceMetrics(BaseModel):
    """
    Метрики одной таблицы в одной базе.
    """
    model_config = ConfigDict(extra='forbid')

    source_type: str
    database: str
    table: str
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now()
    )
    row_count: int | None = None
    max_datetime: Any = None
    update_datetime: Any = None


class TableMetrics(BaseModel):
    """
    Метрики primary и replica для одной настройки таблицы.
    Вместе с правилами проверки
    """
    model_config = ConfigDict(extra="forbid")
    
    table_name: str
    primary:  SourceMetrics
    replica:  SourceMetrics
    strategy: StrategyConfig
    rules:    RulesConfig


class MetricCollector:
    """
    Собирает метрики через существующие классы подключений из Code.db.
    """
    def __init__(self,
        msq_conn: MySQLConn | None = None,
        click_conn: ClickHouseConn | None = None):

        self.mysql_conn = msq_conn
        self.clickhouse_conn = click_conn
        self._connections: dict[str, DBcon] = {}
        self.errors: dict[str, str] = {}

    def _get_connection(self, source: SourceConfig) -> DBcon:
        connection_key = source.type

        if connection_key in self._connections:
            return self._connections[connection_key]

        if source.type == "mysql":
            if self.mysql_conn is not None:
                connection = self.mysql_conn
            else:
                connection = MySQLConn()

        elif source.type == "clickhouse":
            if self.clickhouse_conn is not None:
                connection = self.clickhouse_conn
            else:
                connection = ClickHouseConn()
        else:
            raise ValueError(f"\nНеподдерживаемый тип источника: {source.type}")

        self._connections[connection_key] = connection
        return connection

    def collect_source_metrics(self, source: SourceConfig,
                               strategy: StrategyConfig | None = None) -> SourceMetrics:

        strategy = strategy if strategy is not None else StrategyConfig()
        if strategy.lag_check and not source.datetime_column:
            raise MetricCollectionError("datetime_column", "Не задана колонка для включённой проверки отставания")

        query = build_metrics_query(source, strategy)
        if query is None:
            return SourceMetrics(source_type=source.type, database=source.database, table=source.table)

        connection = self._get_connection(source)
        if strategy.lag_check:
            self._check_datetime_column_exists(source, connection)
        collected_at = datetime.now()
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
            collected_at=collected_at,
            row_count=row.get("row_count"),
            max_datetime=row.get("max_datetime"),
            update_datetime=row.get("update_datetime")
        )

    def collect_table_metrics(self, table: TableConfig) -> TableMetrics:
        sources = {}
        for source_name in ("primary", "replica"):
            source = getattr(table, source_name)
            try:
                sources[source_name] = self.collect_source_metrics(source, table.strategy)
            except MetricCollectionError as error:
                raise MetricCollectionError(
                    f"{source_name}.{error.config_path}", error.message
                ) from error
            except Exception as error:
                raise MetricCollectionError(source_name, str(error)) from error

        return TableMetrics(
            table_name=table.name,
            primary=sources["primary"],
            replica=sources["replica"],
            strategy=table.strategy,
            rules=table.rules,
        )

    def collect_all(self, tables: list[TableConfig]) -> list[TableMetrics]:
        """
        Собирает метрики всех таблиц, продолжая обход при ошибках.
        Ошибки последнего запуска сохраняются в errors по имени таблицы.
        """
        metrics = []
        self.errors.clear()

        for index, table in enumerate(tables):
            try:
                table_metrics = self.collect_table_metrics(table)
            except Exception as error:
                config_path = f"tables[{index}]"
                if isinstance(error, MetricCollectionError):
                    config_path = f"{config_path}.{error.config_path}"
                    message = error.message
                else:
                    message = str(error).strip()
                comment = f"YAML {config_path}: {message}"
                self.errors[table.name] = comment
                logger.error("%s: ошибка сбора метрик: %s", table.name, comment)
                continue

            metrics.append(table_metrics)

        return metrics

    def close_all(self) -> None:
        """Закрывает только созданные коллектором подключения."""
        for connection in self._connections.values():
            if connection is self.mysql_conn or connection is self.clickhouse_conn:
                continue
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
            raise MetricCollectionError(
                "datetime_column",
                f"Колонка {source.datetime_column} не найдена в таблице {source.full_name}"
            )


if __name__ == "__main__":
    from Code.load_env import load_env

    load_env()
    config = load_config()
    collector = MetricCollector()

    try:
        metrics = collector.collect_all(config.tables)
        for table_metrics in metrics:
            print('\n',table_metrics.table_name)
            for table in [table_metrics.primary,table_metrics.replica]:
                print(table)
            print (table_metrics.strategy)
            print (table_metrics.rules)
    finally:
        collector.close_all()
