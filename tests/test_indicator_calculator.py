from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from analysis.indicator_calculator import IndicatorCalculator, IndicatorError
from evidence import ArchiveIdentity, ArchiveWriter, EvidenceBuilder
from normalizer import DataNormalizer
from persistence.connection import configure
from persistence.migration import apply_migration
from persistence.repository import SQLitePersistenceRepository, evidence_row
from test_normalizer import daily_bar, envelope, UNITS
from windows_compat import use_simulated_directory_fsync


def daily_evidence(writer, db, *, count=60, direction=1, zero_volume=False, adjustment='UNADJUSTED', identifier='EV-DAILY'):
    bars = []
    for i in range(count):
        close = 100 + direction * i
        bars.append(daily_bar(trade_date=(date(2026, 1, 1) + timedelta(days=i)).strftime('%Y%m%d'),
            open=close, high=close, low=close, close=close, pre_close=close,
            vol=0 if zero_volume else 100 if i < count-1 else 115, adjust_type=adjustment))
    raw = json.dumps({'bars': bars}).encode()
    identity = ArchiveIdentity('run', 'daily', identifier, 'raw-' + identifier)
    receipt = writer.write(identity=identity, evidence_id='RAW-' + identifier, source='TUSHARE_MCP',
        raw_parts={'response': raw}, endpoint_or_tool='daily', request_params={'stock_id': '601518.SH'},
        request_timestamp='2026-09-30T17:00:00Z')
    env = replace(envelope(payload={'bars': bars}), raw_reference=identity.uri)
    normalized = DataNormalizer().normalize(env, units=UNITS)
    record = EvidenceBuilder().build(fetch_result=env, endpoint_or_tool='daily', request_params=receipt['record'].request_params,
        system_run_id='run', collection_task_id='daily', normalization_result=normalized,
        raw_response_hash=receipt['record'].raw_response_hash, raw_reference_id=identity.evidence_id,
        evidence_id=identifier)
    writer.append_normalized(identity.uri, normalized_data=normalized.canonical_data,
        normalization_rule=record.normalization_rule, validation_result=record.validation_result)
    writer.append_evidence(record)
    repo = SQLitePersistenceRepository(db, archive_reader=writer.reader, enable_real_provider_evidence=True)
    repo._provider_guard((record,))
    repo._operation([('evidence_records', evidence_row(record, record.request_timestamp))])
    return record, normalized


class IndicatorCalculatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        use_simulated_directory_fsync(self)
        self.writer = ArchiveWriter(Path(temporary.name) / 'archive')
        self.db = configure(sqlite3.connect(':memory:'))
        self.addCleanup(self.db.close)
        apply_migration(self.db)
        self.calculator = IndicatorCalculator(self.db, archive_reader=self.writer.reader)

    def test_db_to_known_indicators_and_evidence_without_writes(self):
        record, _ = daily_evidence(self.writer, self.db)
        before = self.db.total_changes
        result = self.calculator.calculate(record.evidence_id)
        self.assertEqual([result[k] for k in ('ma5', 'ma20', 'ma60')], [157.0, 149.5, 129.5])
        self.assertEqual(result['volume_change'], .15)
        self.assertEqual(result['trend'], 'UP')
        self.assertEqual(result['DATA_STATUS'], 'PASS')
        self.assertEqual(result['evidence_id'], record.evidence_id)
        self.assertEqual(result['normalized_data_hash'], record.normalized_data_hash)
        self.assertEqual(self.db.total_changes, before)
        json.dumps(result, allow_nan=False)

    def test_down_and_flat_trends(self):
        for direction, expected in ((-1, 'DOWN'), (0, 'SIDEWAYS')):
            identifier='EV-' + expected
            daily_evidence(self.writer, self.db, direction=direction, identifier=identifier)
            self.assertEqual(self.calculator.calculate(identifier)['trend'], expected)

    def test_insufficient_history_and_zero_denominator_remain_partial(self):
        daily_evidence(self.writer, self.db, count=5, zero_volume=True)
        result = self.calculator.calculate('EV-DAILY')
        self.assertIsNotNone(result['ma5'])
        self.assertIsNone(result['ma20'])
        self.assertIsNone(result['ma60'])
        self.assertIsNone(result['volume_change'])
        self.assertEqual(result['trend'], 'UNKNOWN')
        self.assertEqual(result['DATA_STATUS'], 'PARTIAL')

    def test_unknown_adjustment_never_produces_trend(self):
        daily_evidence(self.writer, self.db, adjustment=None)
        result = self.calculator.calculate('EV-DAILY')
        self.assertIsNone(result['ma5'])
        self.assertEqual(result['trend'], 'UNKNOWN')
        self.assertIn('ADJUSTMENT_UNVERIFIED', result['limitations'])

    def test_missing_evidence_or_corrupt_raw_blocks_calculation(self):
        with self.assertRaises(IndicatorError):
            self.calculator.calculate('absent')
        record, _ = daily_evidence(self.writer, self.db)
        (self.writer.resolver.resolve(record.raw_response_path) / 'response.raw.gz').write_bytes(b'corrupt')
        with self.assertRaises(IndicatorError):
            self.calculator.calculate(record.evidence_id)

    def test_only_new_evidence_table_is_read(self):
        daily_evidence(self.writer, self.db)
        tables = []
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                tables.append(table)
                return sqlite3.SQLITE_OK if table == 'evidence_records' else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(authorize)
        self.assertEqual(self.calculator.calculate('EV-DAILY')['trend'], 'UP')
        self.assertEqual(set(tables), {'evidence_records'})
