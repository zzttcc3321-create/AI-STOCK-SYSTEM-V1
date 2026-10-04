import base64
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from evidence import ArchiveIdentity, ArchiveWriter, ArchiveError
from evidence.archive_contracts import bundle_hash
from providers import ProviderStatus, TdxInformationProvider, TdxLocalHttpProvider
from providers.tushare import HTTPTransport, RawHTTPResponse, TushareMCPProvider
from test_tushare_provider import bar
from test_tdx_realtime import FakeLocal
from providers.tdx_information import MCPResponse
from test_tdx_information import MockTransport, query, TEST_ENV, Clock


class ArchiveProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'archive'
        self.writer = ArchiveWriter(self.root)
        self.context = dict(system_run_id='run', collection_task_id='collection', request_id='request', evidence_id='evidence')
        self.at = lambda: datetime(2026, 10, 4, tzinfo=timezone.utc)

    def tushare(self, **changes):
        transport = MagicMock()
        transport.fetch_daily.return_value = {
            'bars': [bar()], 'expected_trade_dates': ['20260930'],
            'raw_parts': {'daily': b'{ "data": [] }\r\n', 'adj_factor': b'\xff\x80', 'trade_cal': b''},
            'raw_reference': 'tushare://daily/601518.SH/20260901/20260930', **changes}
        provider = TushareMCPProvider(transport, environ={'TUSHARE_TOKEN': 'SYNTHETIC_TOKEN'}, archive_writer=self.writer)
        result = provider.fetch(dict(data_kind='historical_daily', stock_id='601518.SH',
                                     date_from='20260901', date_to='20260930', **self.context))
        return provider, result, transport

    def test_tushare_three_exact_parts_and_canonical_uri(self):
        provider, result, transport = self.tushare()
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(result.raw_reference, ArchiveIdentity(**self.context).uri)
        read = self.writer.reader.read(result.raw_reference)
        self.assertEqual(read['raw_parts'], transport.fetch_daily.return_value['raw_parts'])
        self.assertEqual(result.payload['raw_response_hash'], bundle_hash(read['metadata']['parts']))
        self.assertTrue(provider.validate_contract(result).valid)
        self.assertEqual(result.payload['source_reference'], transport.fetch_daily.return_value['raw_reference'])

    def test_tushare_requires_exact_parts_never_reencodes_mapping(self):
        for parts in (None, {'daily': b'{}'}, {'daily': {}, 'adj_factor': b'{}', 'trade_cal': b'{}'}):
            _, result, _ = self.tushare(raw_parts=parts)
            self.assertEqual(result.status, ProviderStatus.UNAVAILABLE)
        self.assertFalse(list(self.root.rglob('*.gz')))

    def test_tushare_uri_context_mismatch_fails_closed(self):
        _, result, _ = self.tushare(raw_reference='tushare://daily/WRONG/20260901/20260930')
        self.assertEqual(result.status, ProviderStatus.UNAVAILABLE)
        self.assertFalse(self.root.exists())

    def test_tushare_token_echo_blocked(self):
        _, result, _ = self.tushare(raw_parts={'daily': b'SYNTHETIC_TOKEN', 'adj_factor': b'{}', 'trade_cal': b'{}'})
        self.assertEqual(result.status, ProviderStatus.UNAVAILABLE)
        self.assertFalse(self.root.exists())

    def test_http_transport_keeps_exact_bytes_before_json_projection(self):
        body = b' { "code": 0, "data": {"items": []} } \r\n'
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = body
        with patch('providers.tushare.http.client.HTTPSConnection', return_value=connection):
            response = HTTPTransport()._post({'token': 'SYNTHETIC_TOKEN'}, 1, 2)
        self.assertIsInstance(response, RawHTTPResponse)
        self.assertEqual(response.raw_bytes, body)
        self.assertEqual(response['code'], 0)
        connection.close.assert_called_once()

    def test_http_daily_retains_three_wire_parts(self):
        responses = [RawHTTPResponse({'code': 0, 'data': {'fields': [], 'items': []}}, bytes([n])) for n in (1, 2, 3)]
        with patch.object(HTTPTransport, '_post', side_effect=responses):
            result = HTTPTransport().fetch_daily('SYNTHETIC_TOKEN', 'MOCK', '20260901', '20260930',
                                                connect_timeout=1, read_timeout=2)
        self.assertEqual(result['raw_parts'], {'daily': b'\x01', 'adj_factor': b'\x02', 'trade_cal': b'\x03'})

    def test_tdx_local_canonical_uri_and_source_reference(self):
        transport = FakeLocal()
        provider = TdxLocalHttpProvider(transport, environ={}, clock=self.at, archive_writer=self.writer)
        result = provider.fetch(dict(stock_id='601518.SH', data_kind='realtime_quote', **self.context))
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertEqual(result.raw_reference, ArchiveIdentity(**self.context).uri)
        read = self.writer.reader.read(result.raw_reference)
        self.assertEqual(read['metadata']['source_reference'], 'tdx-local/run/collection/request/raw.json')
        self.assertEqual(read['raw_parts']['response'], base64.b64decode(result.payload['raw_response_b64']))
        self.assertTrue(provider.validate_contract(result).valid)

    def test_tdx_local_invalid_binary_and_empty_retained_exactly(self):
        for n, body in enumerate((b'\xff\x00', b'', b'broken JSON')):
            provider = TdxLocalHttpProvider(FakeLocal(raw=body), environ={}, clock=self.at, archive_writer=self.writer)
            result = provider.fetch(dict(stock_id='601518.SH', data_kind='realtime_quote',
                                         **{**self.context, 'evidence_id': 'e' + str(n)}))
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertEqual(self.writer.reader.read(result.raw_reference)['raw_parts']['response'], body)

    def test_tdx_local_archive_failure_cannot_return_available(self):
        provider = TdxLocalHttpProvider(FakeLocal(), environ={}, clock=self.at, archive_writer=ArchiveWriter(self.root, quota_bytes=0))
        result = provider.fetch(dict(stock_id='601518.SH', data_kind='realtime_quote', **self.context))
        self.assertNotEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertIsNone(result.raw_reference)

    def test_tdx_information_shared_writer_and_r6(self):
        transport = MockTransport()
        provider = TdxInformationProvider(transport, evidence_root=self.root, archive_writer=self.writer,
                                          environ=TEST_ENV, clock=Clock())
        result = provider.fetch(query())
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        for entry, body in zip(result.payload['evidence'], transport.raw_bodies):
            read = self.writer.reader.read(entry['record']['raw_response_path'])
            self.assertEqual(read['raw_parts']['response'], body)
            self.assertEqual(read['metadata']['source'], 'WORKBUDDY_TDX_MCP')
        for body in (b'TEST_KEY_ONLY', b'"\\u0054EST_KEY_ONLY"'):
            transport.hook = lambda *args, body=body: MCPResponse(200, {}, body)
            other = TdxInformationProvider(transport, evidence_root=self.root, environ=TEST_ENV, clock=Clock())
            before = len(list(self.root.rglob('*.gz')))
            rejected = other.fetch(query())
            self.assertEqual(rejected.status, ProviderStatus.VALIDATION_FAILED)
            self.assertEqual(before, len(list(self.root.rglob('*.gz'))))

    def test_no_fourth_source(self):
        with self.assertRaises(ValueError):
            self.writer.write(identity=ArchiveIdentity(**self.context), source='FOURTH_SOURCE', raw_parts={'response': b'{}'},
                endpoint_or_tool='x', request_params={}, request_timestamp='2026-10-04T00:00:00+00:00')

    def test_tushare_parse_failure_empty_and_nonutf_wire_are_archived(self):
        for n, body in enumerate((b'', b'\xff\x80', b'broken JSON')):
            connection = MagicMock()
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read.return_value = body
            provider = TushareMCPProvider(HTTPTransport(), environ={'TUSHARE_TOKEN': 'SYNTHETIC_TOKEN'}, archive_writer=self.writer)
            with patch('providers.tushare.http.client.HTTPSConnection', return_value=connection):
                result = provider.fetch(dict(data_kind='historical_daily', stock_id='601518.SH',
                    date_from='20260901', date_to='20260930', **{**self.context, 'evidence_id': 'fail' + str(n)}))
            self.assertEqual(result.status, ProviderStatus.UNAVAILABLE)
            self.assertEqual(self.writer.reader.read(result.raw_reference)['raw_parts'], {'daily': body})

    def test_tushare_second_part_failure_keeps_prior_exact_part(self):
        from providers.tushare import RawHTTPFailure
        daily = RawHTTPResponse({'code': 0, 'data': {'fields': [], 'items': []}}, b' {"code":0} ')
        provider = TushareMCPProvider(HTTPTransport(), environ={'TUSHARE_TOKEN': 'SYNTHETIC_TOKEN'}, archive_writer=self.writer)
        with patch.object(HTTPTransport, '_post', side_effect=[daily, RawHTTPFailure(b'')]):
            result = provider.fetch(dict(data_kind='historical_daily', stock_id='601518.SH',
                date_from='20260901', date_to='20260930', **self.context))
        self.assertEqual(result.status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(self.writer.reader.read(result.raw_reference)['raw_parts'], {'daily': daily.raw_bytes, 'adj_factor': b''})

    def test_context_invalid_is_rejected_before_tushare_http(self):
        transport = MagicMock()
        provider = TushareMCPProvider(transport, environ={'TUSHARE_TOKEN': 'SYNTHETIC_TOKEN'}, archive_writer=self.writer)
        result = provider.fetch(dict(data_kind='historical_daily', stock_id='601518.SH', date_from='20260901',
            date_to='20260930', **{**self.context, 'request_id': '../escape'}))
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        transport.fetch_daily.assert_not_called()
