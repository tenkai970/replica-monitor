import pandas as pd

from typing import Literal, Any
from pydantic import BaseModel, ConfigDict


from Code.metrics import SourceMetrics, TableMetrics



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
        self.metrics: list[dict] = metrics

    def _check_rows(self,primary:SourceMetrics, replica:SourceMetrics, max_row_diff: int):
        """
        Проверка мэтча строк реплики и оригинала
        """
        replica_rows = replica.row_count
        primary_rows = primary.row_count
        diff = abs(replica_rows - primary_rows)
        if diff > max_row_diff:
            return False
        else:
            return True
        
    def _check_tables_datetime(self,primary:SourceMetrics, replica:SourceMetrics, max_lag_minutes: int):
        """
        Проверка мэтча строк реплики и оригинала
        """
        replica_datetime = replica.max_datetime
        primary_datetime = primary.max_datetime
        lag = abs(replica_rows - primary_rows)
        # if diff > max_row_diff:
        #     return False
        # else:
        #     return True
    
    def compare_metrics(self, table_metrics: TableMetrics):
        table_name = table_metrics.table_name
        primary    = table_metrics.primary
        replica    = table_metrics.replica
        strategy   = table_metrics.strategy
        rules      = table_metrics.rules

        rows_rule = self._check_rows(primary, replica, rules.max_row_diff)

    
    def compare_all(self) -> list[CheckResult]:

        if self.data is None:
            raise TypeError(f"\nНе переданы метаданные по таблицам")

        for table_metrics in self.metrics:
            # передача в compare_metrics

