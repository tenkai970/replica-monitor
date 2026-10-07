import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pydantic import ValidationError

from Code.config import DatabaseConfig, RulesConfig, SourceConfig, StrategyConfig, TableConfig

# Importing DB wrappers must not load a real .env or change cwd in these tests.
with patch("Code.load_env.load_env"):
    from Code.metrics import MetricCollector, SourceMetrics, TableMetrics
    from Code.metrics_check import MetricsComparser
from Code.sql_builder import build_metrics_query


class MetricsRulesTests(unittest.TestCase):
    def setUp(self):
        logger_patch = patch("Code.metrics_check.logger")
        logger_patch.start()
        self.addCleanup(logger_patch.stop)

    def source(self, **values):
        return SourceMetrics(source_type="mysql", database="db", table="t", **values)

    def compare(self, primary, replica, **flags):
        strategy = StrategyConfig(row_count=False, **flags)
        metric = TableMetrics(table_name="test", primary=primary, replica=replica,
                              strategy=strategy, rules=RulesConfig(last_update_seconds=60))
        return MetricsComparser([]).compare_metrics(metric)

    def test_bad_lag_dates_are_errors(self):
        for value in ("", "broken", None, pd.NaT, 1700000000, []):
            with self.subTest(value=value):
                result = self.compare(self.source(max_datetime=value),
                                      self.source(max_datetime="2020-01-01"), lag_check=True)
                self.assertEqual(result.status, "ERROR")

    def test_update_dates_and_optional_sources(self):
        now = pd.Timestamp.now() - pd.Timedelta(seconds=1)
        for primary, replica, expected in (
            (None, now, "OK"), (now, None, "OK"), (now, now, "OK"),
            ("", now, "ERROR"), (now, "broken", "ERROR"),
            ("2099-01-01", None, "OK"), (None, "2020-01-01", "ERROR"),
            (None, None, "WARNING"),
        ):
            with self.subTest(primary=primary, replica=replica):
                self.assertEqual(self.compare(self.source(update_datetime=primary),
                                              self.source(update_datetime=replica),
                                              last_update=True).status, expected)

    def test_warning_does_not_hide_row_error(self):
        metric = TableMetrics(table_name="test", primary=self.source(row_count=10),
                              replica=self.source(row_count=0),
                              strategy=StrategyConfig(last_update=True), rules=RulesConfig())
        self.assertEqual(MetricsComparser([]).compare_metrics(metric).status, "ERROR")

    def test_equal_dates_pass_zero_lag(self):
        self.assertEqual(self.compare(self.source(max_datetime="2020-01-01T03:00:00+03:00"),
                                      self.source(max_datetime="2020-01-01T00:00:00Z"),
                                      lag_check=True).status, "OK")

    def test_rules_reject_negative_allow_zero(self):
        for name in RulesConfig.model_fields:
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    RulesConfig(**{name: -1})
                self.assertEqual(getattr(RulesConfig(**{name: 0}), name), 0)

    def test_unique_names_in_model(self):
        source = SourceConfig(type="mysql", database="db", table="t")
        table = TableConfig(name="same", primary=source, replica=source, rules=RulesConfig())
        with self.assertRaisesRegex(ValidationError, r"tables\[1\].name"):
            DatabaseConfig(tables=[table, table])
        DatabaseConfig(tables=[table, table.model_copy(update={"name": "different"})])

    def test_query_respects_each_strategy(self):
        source = SourceConfig(type="clickhouse", database="db", table="t",
                              datetime_column="event_date", last_update_column="written_at")
        for flag, alias in (("row_count", "row_count"), ("lag_check", "max_datetime"),
                            ("last_update", "update_datetime")):
            flags = dict(row_count=False, lag_check=False, last_update=False)
            flags[flag] = True
            query = build_metrics_query(source, StrategyConfig(**flags))
            self.assertIn("AS " + alias, query)
            for other in {"row_count", "max_datetime", "update_datetime"} - {alias}:
                self.assertNotIn("AS " + other, query)

    def test_disabled_metrics_make_no_connection(self):
        source = SourceConfig(type="mysql", database="db", table="t",
                              datetime_column="invalid-name", last_update_column="invalid-name")
        collector = MetricCollector()
        with patch.object(collector, "_get_connection") as connect:
            result = collector.collect_source_metrics(source, StrategyConfig(row_count=False))
            connect.assert_not_called()
            self.assertIsNone(result.row_count)
        connection = Mock()
        connection.query.return_value = pd.DataFrame([{"row_count": 5}])
        collector = MetricCollector(msq_conn=connection)
        result = collector.collect_source_metrics(source, StrategyConfig())
        self.assertEqual(result.row_count, 5)
        connection.query.assert_called_once()
        self.assertNotIn("MAX", connection.query.call_args.args[0])

    def test_optional_update_without_column_makes_no_connection(self):
        source = SourceConfig(type="mysql", database="db", table="t")
        collector = MetricCollector()
        with patch.object(collector, "_get_connection") as connect:
            collector.collect_source_metrics(source, StrategyConfig(row_count=False, last_update=True))
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
