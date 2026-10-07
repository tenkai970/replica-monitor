import os
import runpy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from openpyxl import load_workbook
from pydantic import ValidationError

from Code.config import DatabaseConfig, SourceConfig, TableConfig, StrategyConfig, RulesConfig
from Code.db import ClickHouseConn, MySQLConn
from Code.monitor import run_checks
from Code.metrics import SourceMetrics, TableMetrics, MetricCollector
from Code.metrics_check import MetricsComparser


class MonitorRunTests(unittest.TestCase):
    def setUp(self):
        for name in ('Code.metrics_check.logger', 'Code.metrics.logger', 'Code.monitor.logger', 'Code.report.logger'):
            patcher = patch(name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def source(self, **kwargs):
        return SourceMetrics(source_type='mysql', database='db', table='t', **kwargs)

    def compare(self, primary, replica, **flags):
        return MetricsComparser([]).compare_metrics(TableMetrics(
            table_name='test', primary=primary, replica=replica,
            strategy=StrategyConfig(row_count=False, **flags), rules=RulesConfig(last_update_seconds=60)))

    def test_freshness_uses_each_query_time(self):
        primary = self.source(update_datetime='2020-01-01T00:00:00',
                              collected_at=datetime(2020, 1, 1, 0, 1))
        replica = self.source(update_datetime='2020-01-01T01:00:00',
                              collected_at=datetime(2020, 1, 1, 1, 1))
        result = self.compare(primary, replica, last_update=True)
        self.assertEqual(result.status, 'OK')
        self.assertEqual(result.primary_update_age_seconds, 60)
        self.assertEqual(result.replica_update_age_seconds, 60)
        primary.collected_at = datetime(2020, 1, 1, 0, 1, 1)
        self.assertEqual(self.compare(primary, replica, last_update=True).status, 'ERROR')

    def test_aware_dates_use_system_timezone(self):
        primary = self.source(max_datetime='2099-01-01T03:00:00+03:00')
        local = datetime.fromisoformat(primary.max_datetime).astimezone().replace(tzinfo=None)
        replica = self.source(max_datetime=local)
        result = self.compare(primary, replica, lag_check=True)
        self.assertEqual(result.status, 'OK')
        self.assertEqual(result.lag_seconds, 0)
        self.assertEqual(result.primary_max_datetime_raw, primary.max_datetime)

    def test_local_update_age(self):
        primary = self.source(update_datetime='2026-10-07T13:21:08+03:00',
                              collected_at=datetime(2026, 10, 7, 13, 27, 37))
        result = self.compare(primary, self.source(), last_update=True)
        local_update = datetime.fromisoformat(primary.update_datetime).astimezone().replace(tzinfo=None)
        self.assertEqual(result.primary_update_age_seconds, (primary.collected_at - local_update).total_seconds())
        self.assertEqual(result.primary_last_update_raw, primary.update_datetime)

    def test_collection_time_is_captured_before_query(self):
        connection = Mock()
        before = datetime.now()
        during = []
        def query(sql):
            during.append(datetime.now())
            return pd.DataFrame([{'row_count': 2}])
        connection.query.side_effect = query
        source = SourceConfig(type='mysql', database='db', table='t')
        result = MetricCollector(msq_conn=connection).collect_source_metrics(source)
        self.assertLessEqual(before, result.collected_at)
        self.assertLessEqual(result.collected_at, during[0])

    def test_driver_errors_retain_cause(self):
        with patch.object(ClickHouseConn, 'connect', return_value=Mock()):
            ch = ClickHouseConn()
        error = RuntimeError('unknown column written_at')
        ch.conn.query_df.side_effect = error
        with self.assertRaisesRegex(RuntimeError, 'unknown column written_at') as raised:
            ch.query('SELECT 1')
        self.assertIs(raised.exception.__cause__, error)
        with patch.object(MySQLConn, 'connect', return_value=Mock()):
            mysql = MySQLConn()
        with patch('Code.db.pd.read_sql', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'unknown column written_at'):
                mysql.query('SELECT 1')

    def test_results_errors_and_excel_share_data(self):
        source = SourceConfig(type='mysql', database='db', table='t')
        tables = [TableConfig(name=name, primary=source, replica=source, rules=RulesConfig())
                  for name in ('failed', 'healthy')]
        config = DatabaseConfig(tables=tables)
        connection = Mock()
        connection.query.side_effect = [RuntimeError('permission denied'),
                                       pd.DataFrame([{'row_count': 2}]), pd.DataFrame([{'row_count': 2}])]
        with tempfile.TemporaryDirectory() as directory:
            with patch('Code.monitor.get_config_path', return_value=Path('unused.yaml')), \
                 patch('Code.monitor.load_config', return_value=config), \
                 patch.dict(os.environ, {'OUTPUT_': directory}):
                run = run_checks(mysql_conn=connection, create_xlsx=True, open_folder=False)
            self.assertEqual([r.status for r in run.results], ['ERROR', 'OK'])
            self.assertIn('permission denied', run.results[0].comment)
            self.assertIn('tables[0].primary', run.results[0].comment)
            self.assertEqual(run.rows[0]['comment'], run.results[0].comment)
            self.assertEqual(run.rows[1]['checked_at'], run.results[1].checked_at)
            workbook = load_workbook(run.report_path)
            try:
                self.assertEqual(workbook.active['E2'].value, run.results[0].comment)
                self.assertEqual(workbook.active['F3'].value, run.rows[1]['metrics'])
            finally:
                workbook.close()
            connection.close.assert_not_called()

    def test_entrypoint_loads_environment_first(self):
        calls = []
        with patch('Code.load_env.load_env', side_effect=lambda: calls.append('env')), \
             patch('Code.monitor.run_checks', side_effect=lambda **kw: calls.append('main')):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
        self.assertEqual(calls, ['env', 'main'])

    def test_notebook_gets_results_without_export(self):
        source = SourceConfig(type='mysql', database='db', table='t')
        table = TableConfig(name='disabled', primary=source, replica=source,
                            strategy=StrategyConfig(row_count=False), rules=RulesConfig())
        with patch('Code.monitor.get_config_path', return_value=Path('unused.yaml')), \
             patch('Code.monitor.load_config', return_value=DatabaseConfig(tables=[table])), \
             patch('Code.monitor.export_report') as export, \
             patch('Code.metrics.MySQLConn') as connect:
            run = run_checks()
        self.assertEqual(run.results[0].status, 'WARNING')
        self.assertIsNone(run.report_path)
        export.assert_not_called()
        connect.assert_not_called()

    def test_connection_failure_retains_driver_message(self):
        env = {f'CLICKHOUSE_{key}': value for key, value in
               (('HOST', 'unused'), ('PORT', '8123'), ('USER', 'test'), ('PASSWORD', 'test'))}
        error = RuntimeError('connection refused')
        with patch.dict(os.environ, env), patch('Code.db.logger'), \
             patch('Code.db.clickhouse_connect.get_client', side_effect=error):
            with self.assertRaisesRegex(ConnectionError, 'connection refused') as raised:
                ClickHouseConn()
        self.assertIs(raised.exception.__cause__, error)


if __name__ == '__main__':
    unittest.main()
