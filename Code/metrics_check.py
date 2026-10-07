import pandas as pd
import re
from datetime import datetime
from numbers import Number

from typing import Literal, Any
from pydantic import BaseModel, Field


from Code.metrics import SourceMetrics, TableMetrics
from Code.logger import create_logger


logger = create_logger(__name__)

class CheckResult(BaseModel):
    table_name: str
    status: Literal["OK", "WARNING", "ERROR"]
    comment: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now())
    primary_collected_at: datetime | None = None
    replica_collected_at: datetime | None = None
    lag_seconds: float | None = None
    primary_update_age_seconds: float | None = None
    replica_update_age_seconds: float | None = None
    primary_row_count: int | None = None
    replica_row_count: int | None = None
    row_diff: int | None = None
    primary_max_datetime: Any = None
    replica_max_datetime: Any = None
    primary_max_datetime_raw: Any = None
    replica_max_datetime_raw: Any = None
    primary_last_update_raw: Any = None
    replica_last_update_raw: Any = None
    primary_last_update: Any = None
    replica_last_update: Any = None


class MetricsComparser():
    '''
    Основной класс проверки метрик по таблицам
    '''
    def __init__(self, metrics: list[TableMetrics]):
        self.metrics = metrics

    @staticmethod
    def _parse_datetime(value) -> pd.Timestamp:
        """Дату с поясом переводит в системное время, дату без пояса оставляет как есть."""
        if isinstance(value, Number):
            raise ValueError("Числовая дата не поддерживается: передайте datetime или строку даты")
        if isinstance(value, str):
            # ДД.ММ.ГГГГ приводим к ISO, сохраняя время и смещение.
            value = re.sub(
                r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?=$|[ T])",
                lambda match: f"{match[3]}-{match[2].zfill(2)}-{match[1].zfill(2)}",
                value.strip(),
            )
        try:
            value = pd.to_datetime(value, errors="coerce")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Некорректная дата") from error
        if not isinstance(value, pd.Timestamp) or pd.isna(value):
            raise ValueError("Дата отсутствует или имеет некорректный формат")
        if value.tzinfo is not None:
            # Системный пояс для конкретной даты, включая сезонное смещение.
            local_zone = value.to_pydatetime().astimezone().tzinfo
            value = value.tz_convert(local_zone).tz_localize(None)
        return value

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
        lag_seconds = None
        dates = {}
        ages = {}

        if strategy.row_count:
            if primary.row_count is None or replica.row_count is None:
                status = "ERROR"
                comment = "Не вернулось количество строк по таблицам"
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
            try:
                dates["primary_max_datetime"] = self._parse_datetime(primary.max_datetime)
                dates["replica_max_datetime"] = self._parse_datetime(replica.max_datetime)
                lag_seconds = (dates["primary_max_datetime"] - dates["replica_max_datetime"]).total_seconds()
                lag_rule = lag_seconds <= rules.max_lag_seconds
            except ValueError as error:
                status = "ERROR"
                comment = f"Ошибка дат для проверки отставания: {error}"
                logger.error("%s: %s", table_name, comment)
                comments.append(comment)
            else:
                if not lag_rule:
                    status = "ERROR"
                    comment = f"Отставание реплики превышает {rules.max_lag_seconds} секунд"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)

        if strategy.last_update:
            checked_updates = 0
            for key, label, source in (("primary", "Основная таблица", primary), ("replica", "Реплика", replica)):
                if source.update_datetime is None or (
                    pd.api.types.is_scalar(source.update_datetime) and pd.isna(source.update_datetime)
                ):
                    continue
                checked_updates += 1
                try:
                    update_date = self._parse_datetime(source.update_datetime)
                    dates[f"{key}_last_update"] = update_date
                    age = (self._parse_datetime(source.collected_at) - update_date).total_seconds()
                    ages[f"{key}_update_age_seconds"] = age
                    update_rule = age <= rules.last_update_seconds
                except ValueError as error:
                    status = "ERROR"
                    comment = f"{label}: ошибка даты обновления: {error}"
                    logger.error("%s: %s", table_name, comment)
                    comments.append(comment)
                else:
                    if not update_rule:
                        status = "ERROR"
                        comment = f"{label} не обновлялась последние {rules.last_update_seconds} секунд"
                        logger.error("%s: %s", table_name, comment)
                        comments.append(comment)

            if not checked_updates:
                if status != "ERROR":
                    status = "WARNING"
                comment = f"Нет значений последнего обновления у таблиц {table_name}"
                logger.warning("%s: %s", table_name, comment)
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
            primary_collected_at=primary.collected_at,
            replica_collected_at=replica.collected_at,
            lag_seconds=lag_seconds,
            primary_max_datetime_raw=primary.max_datetime,
            replica_max_datetime_raw=replica.max_datetime,
            primary_last_update_raw=primary.update_datetime,
            replica_last_update_raw=replica.update_datetime,
            **dates,
            **ages
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
    from Code.load_env import load_env

    load_env()
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

