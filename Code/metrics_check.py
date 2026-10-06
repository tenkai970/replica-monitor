import pandas as pd
import time

from typing import Literal, Any
from pydantic import BaseModel, ConfigDict


from Code.metrics import SourceMetrics, TableMetrics
from Code.logger import create_logger


logger = create_logger(__name__)

class CheckResult(BaseModel):
    table_name: str
    status: Literal["OK", "WARNING", "ERROR"]
    comment: str
    primary_row_count: int | None = None
    replica_row_count: int | None = None
    row_diff: int | None = None
    primary_max_datetime: Any = None
    replica_max_datetime: Any = None


class MetricsComparser():
    def __init__(self, metrics: list[TableMetrics]):
        self.metrics = metrics

    @staticmethod
    def _check_rows(
            primary:SourceMetrics,
            replica:SourceMetrics,
            max_row_diff: int
        ):
        """
        Проверка мэтча строк реплики и оригинала (max_row_diff)
        """
        replica_rows = replica.row_count
        primary_rows = primary.row_count
        diff = abs(replica_rows - primary_rows)
        if diff > max_row_diff:
            return False
        else:
            return True

    @staticmethod
    def _check_tables_datetime(
            primary:SourceMetrics,
            replica:SourceMetrics,
            max_lag_seconds: int
        ):
        """
        Проверка мэтча по общему столбцу даты (max_lag_seconds)
        """
        primary_dt = pd.to_datetime(primary.max_datetime, utc=True)
        replica_dt = pd.to_datetime(replica.max_datetime, utc=True)
        lag = (primary_dt - replica_dt).total_seconds()
        if lag > max_lag_seconds:
            return False
        else:
            return True

    @staticmethod
    def _check_table_update(
            table: SourceMetrics,
            last_update_seconds: int
        ):
        """
        Проверка на обновление таблицы за последние n-секунд (last_update_seconds)
        """
        table_dt = pd.to_datetime(table.update_datetime, utc=True).timestamp()
        now_sec = time.time()
        min_level = now_sec - last_update_seconds
        if min_level > table_dt:
            return False
        else:
            return True
    
    def compare_metrics(self, table_metrics: TableMetrics) -> CheckResult:
        """
        Проверяет одну пару таблиц по включённым стратегиям.
        Rules задаёт допустимые погрешности, включая нулевые.
        """
        table_name = table_metrics.table_name
        primary    = table_metrics.primary
        replica    = table_metrics.replica
        strategy   = table_metrics.strategy
        rules      = table_metrics.rules

        comments = []
        status = "OK"
        row_diff = None

        if strategy.row_count:
            if primary.row_count is None or replica.row_count is None:
                status = "ERROR"
                comment = "Не удалось проверить количество строк: нет метрик"
                logger.error("%s: %s", table_name, comment)
                comments.append(comment)
            else:
                row_diff = abs(primary.row_count - replica.row_count)
                rows_rule = self._check_rows(primary, replica, rules.max_row_diff)
                if not rows_rule:
                    status = "ERROR"
                    comment = f"Разница в количестве строк {row_diff} превышает {rules.max_row_diff}"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)

        if strategy.lag_check:
            if pd.isna(primary.max_datetime) or pd.isna(replica.max_datetime):
                status = "ERROR"
                comment = "Не удалось проверить отставание: нет даты в метриках"
                logger.error("%s: %s", table_name, comment)
                comments.append(comment)
            else:
                lag_rule = self._check_tables_datetime(
                    primary, replica, rules.max_lag_seconds
                )
                if not lag_rule:
                    status = "ERROR"
                    comment = f"Отставание реплики превышает {rules.max_lag_seconds} секунд"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)

        if strategy.last_update:
            if not pd.isna(primary.update_datetime):
                primary_update_rule = self._check_table_update(
                    primary, rules.last_update_seconds
                )
                if not primary_update_rule:
                    status = "ERROR"
                    comment = f"Основная таблица не обновлялась последние {rules.last_update_seconds} секунд"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)

            if pd.isna(replica.update_datetime):
                status = "ERROR"
                comment = "Не удалось проверить обновление: нет даты в метриках реплики"
                logger.error("%s: %s", table_name, comment)
                comments.append(comment)
            else:
                update_rule = self._check_table_update(
                    replica, rules.last_update_seconds
                )
                if not update_rule:
                    status = "ERROR"
                    comment = f"Реплика не обновлялась последние {rules.last_update_seconds} секунд"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)

        if not (strategy.row_count or strategy.lag_check or strategy.last_update):
            status = "WARNING"
            comment = "Проверки для таблицы не включены"
            logger.warning("%s: %s", table_name, comment)
            comments.append(comment)
        elif not comments:
            comment = "Все включённые проверки пройдены"
            logger.info("%s: %s", table_name, comment)
            comments.append(comment)

        return CheckResult(
            table_name=table_name,
            status=status,
            comment="; ".join(comments),
            primary_row_count=primary.row_count,
            replica_row_count=replica.row_count,
            row_diff=row_diff,
            primary_max_datetime=primary.max_datetime,
            replica_max_datetime=replica.max_datetime,
        )

    
    def compare_all(self) -> list[CheckResult]:

        if self.metrics is None:
            raise TypeError(f"\nНе переданы метаданные по таблицам")

        results = []
        for table_metrics in self.metrics:
            results.append(self.compare_metrics(table_metrics))

        return results


if __name__ == "__main__":
    from Code.config import load_config
    from Code.metrics import MetricCollector

    config = load_config()
    collector = MetricCollector()

    try:
        metrics = collector.collect_all(config.tables)
        comparser = MetricsComparser(metrics)
        results = comparser.compare_all()

        for result in results:
            print('\n', result.table_name)
            print(result)

        logger.info(
            "Проверено таблиц: %s из %s. Ошибок сбора: %s",
            len(results), len(config.tables), len(collector.errors)
        )
    finally:
        collector.close_all()

