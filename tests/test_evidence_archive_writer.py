from contextlib import nullcontext
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evidence import ArchiveWriter, ArchiveReader, ArchiveIdentity, ArchiveError
from evidence.archive_contracts import MAX_RAW_BYTES, SIDECAR_NAME, sha256, plain
from windows_compat import marked_reparse_point, symlink_privilege_missing

AT = '2026-10-04T08:00:00+08:00'
ID = ArchiveIdentity('run', 'collection', 'request', 'evidence')


def write(writer, *, identity=ID, parts=None, **kwargs):
    values = dict(identity=identity, source='TDX_OFFICIAL_LOCAL_HTTP',
        raw_parts={'response': b'\xff\x00binary\x80'} if parts is None else parts,
        endpoint_or_tool='get_market_snapshot', request_params={'stock_id': 'MOCK'}, request_timestamp=AT)
    values.update(kwargs)
    return writer.write(**values)


class ArchiveWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'archive'
        self.writer = ArchiveWriter(self.root)
        self.reader = self.writer.reader

    def test_roundtrip_binary_nonutf_empty_and_single(self):
        for n, raw in enumerate((b'\x00\xff\xfe\x80', b'', b' \n {"x":1} \r\n')):
            identity = replace(ID, evidence_id='e' + str(n))
            receipt = write(self.writer, identity=identity, parts={'response': raw})
            result = self.reader.read(identity.uri)
            self.assertEqual(result['raw_parts'], {'response': raw})
            self.assertEqual(receipt['record'].raw_response_hash, sha256(raw))
            self.assertTrue((self.writer.resolver.resolve(identity.uri) / 'response.raw.gz').is_file())

    def test_multipart_exact(self):
        raw = {'daily': b'{  "x":1}\n', 'adj_factor': b'\xff', 'trade_cal': b''}
        write(self.writer, parts=raw)
        self.assertEqual(self.reader.read(ID.uri)['raw_parts'], raw)

    def test_idempotent_and_conflict(self):
        write(self.writer)
        directory = self.writer.resolver.resolve(ID.uri)
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir()}
        self.assertEqual(write(self.writer)['status'], 'IDEMPOTENT_NOOP')
        self.assertEqual(before, {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir()})
        for change in (dict(parts={'response': b'other'}), dict(endpoint_or_tool='other'),
                       dict(request_params={'stock_id': 'other'})):
            with self.subTest(change=change), self.assertRaisesRegex(ArchiveError, 'IDENTITY_CONFLICT'):
                write(self.writer, **change)

    def test_sensitive_fields_not_stored(self):
        write(self.writer, request_params={'token': 'SYNTHETIC_ONLY', 'nested': {'API_KEY': 'SYNTHETIC_ONLY'}})
        self.assertNotIn(b'SYNTHETIC_ONLY', (self.writer.resolver.resolve(ID.uri) / SIDECAR_NAME).read_bytes())

    def test_echo_rejected_before_any_disk_write(self):
        for data in (b'SYNTHETIC_ONLY', b'"SYNTHETIC_ONLY"',
                     b'"\\u0053YNTHETIC_ONLY"'):
            with self.subTest(data=data), self.assertRaisesRegex(ArchiveError, 'SENSITIVE_RESPONSE_ECHO'):
                write(self.writer, parts={'response': data}, secrets=('SYNTHETIC_ONLY',))
            self.assertFalse(self.root.exists())

    def test_secret_in_innocent_metadata_field_rejected(self):
        with self.assertRaisesRegex(ArchiveError, 'SENSITIVE_RESPONSE_ECHO'):
            write(self.writer, request_params={'q': 'SYNTHETIC_ONLY'}, secrets=('SYNTHETIC_ONLY',))
        self.assertFalse(self.root.exists())

    def test_invalid_raw_parts_and_memory_bound(self):
        for parts in ({}, {'x': 'not bytes'}, {'../x': b'x'},
                      {str(i): b'' for i in range(9)}, {'x': b'x' * (MAX_RAW_BYTES + 1)}):
            with self.subTest(keys=list(parts)), self.assertRaises(ArchiveError):
                write(self.writer, parts=parts)
        self.assertFalse(self.root.exists())

    def test_source_context_mismatch(self):
        with self.assertRaisesRegex(ArchiveError, 'TDX_SOURCE_CONTEXT'):
            write(self.writer, source_reference='tdx-local/other/collection/request/raw.json')
        with self.assertRaisesRegex(ArchiveError, 'TUSHARE_SOURCE_CONTEXT'):
            write(self.writer, source='TUSHARE_MCP', source_reference='tushare://daily/wrong/20261001/20261002',
                  request_params=dict(stock_id='MOCK', date_from='20261001', date_to='20261002'))
        self.assertFalse(self.root.exists())

    def test_raw_and_sidecar_write_fail_closed(self):
        original = self.writer._write_file
        for failure_name in ('response.raw.gz', SIDECAR_NAME):
            def fail(path, data):
                if path.name == failure_name:
                    raise OSError('injected write failure')
                return original(path, data)
            with self.subTest(name=failure_name), patch.object(self.writer, '_write_file', side_effect=fail):
                with self.assertRaises(OSError):
                    write(self.writer)
            self.assertFalse(self.writer.resolver.resolve(ID.uri).exists())
            self.assertFalse(list(self.root.rglob('.pending-*')))

    def test_partial_write_fail_closed(self):
        real_open = Path.open
        class Partial:
            def __init__(self, path): self.file = real_open(path, 'xb')
            def __enter__(self): return self
            def __exit__(self, *args): self.file.close()
            def write(self, data): return self.file.write(data[:1])
            def fileno(self): return self.file.fileno()
        with patch('pathlib.Path.open', side_effect=lambda path, *args, **kw: Partial(path), autospec=True):
            with self.assertRaisesRegex(ArchiveError, 'PARTIAL_WRITE'):
                write(self.writer)
        self.assertFalse(self.writer.resolver.resolve(ID.uri).exists())

    def test_rename_and_fsync_failure_fail_closed(self):
        for target in ('evidence.archive_writer.os.rename', 'evidence.archive_writer.os.fsync'):
            with self.subTest(target=target), patch(target, side_effect=OSError('injected')):
                with self.assertRaises(OSError):
                    write(self.writer)
            self.assertFalse(self.writer.resolver.resolve(ID.uri).exists())
        self.root.mkdir(exist_ok=True)
        with patch('evidence.archive_writer.os.name', 'nt'), \
                patch('evidence.archive_writer.os.open') as opened:
            self.writer._sync_dir(self.root)
        opened.assert_not_called()

    def test_post_rename_fsync_leaves_only_uncommitted_orphan(self):
        original = self.writer._sync_dir
        def fail(path):
            if path == self.writer.resolver.resolve(ID.uri).parent and self.writer.resolver.resolve(ID.uri).exists():
                raise OSError('parent sync failed')
            original(path)
        with patch.object(self.writer, '_sync_dir', side_effect=fail):
            with self.assertRaises(OSError):
                write(self.writer)
        with self.assertRaisesRegex(ArchiveError, 'ORPHAN_INCOMPLETE'):
            self.reader.read(ID.uri)

    def test_quota_enforces_five_percent_and_lower_override(self):
        usage = lambda _: SimpleNamespace(total=100, free=1000000)
        for writer in (ArchiveWriter(self.root, disk_usage=usage, quota_bytes=999999),
                       ArchiveWriter(self.root, quota_bytes=0)):
            with self.assertRaisesRegex(ArchiveError, 'QUOTA_EXCEEDED'):
                write(writer)
            self.assertFalse(writer.resolver.resolve(ID.uri).exists())

    def test_quota_counts_orphans_and_existing_metadata(self):
        write(self.writer)
        existing = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        (self.root / '.orphan').write_bytes(b'x' * 10)
        writer = ArchiveWriter(self.root, quota_bytes=existing + 10)
        with self.assertRaisesRegex(ArchiveError, 'QUOTA_EXCEEDED'):
            writer.append_normalized(ID.uri, normalized_data={'x': 1}, normalization_rule=[], validation_result={"status": "PASS", "failures": []})

    def test_symlink_root_or_component_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            self.root.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                ArchiveWriter(self.root)
            self.root.unlink()
        except OSError as exc:
            if not symlink_privilege_missing(exc):
                raise
            self.root.mkdir()
            with marked_reparse_point(self.root), self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
                ArchiveWriter(self.root)
            self.root.rmdir()
        (self.root / 'v1').mkdir(parents=True)
        link = self.root / 'v1' / 'run'
        try:
            link.symlink_to(outside, target_is_directory=True)
            context = nullcontext()
        except OSError as exc:
            if not symlink_privilege_missing(exc):
                raise
            link.mkdir()
            context = marked_reparse_point(link)
        with context, self.assertRaisesRegex(ArchiveError, 'SYMLINK_ESCAPE'):
            write(self.writer)
        self.assertFalse(list(outside.iterdir()))

    def test_normalized_append_and_binding_append_only(self):
        write(self.writer)
        norm = self.writer.append_normalized(ID.uri, normalized_data={'price': 1}, normalization_rule=['NO_CONVERSION'],
                                             validation_result={'status': 'PASS', 'failures': []})
        self.assertEqual(self.reader.normalized(ID.uri, norm['normalized_data_hash']), norm)
        with self.assertRaisesRegex(ArchiveError, 'IDENTITY_CONFLICT'):
            self.writer.append_normalized(ID.uri, normalized_data={'price': 1}, normalization_rule=['OTHER'],
                                          validation_result={'status': 'FAIL', 'failures': []})
        binding = {'analysis_report_id': 'report', 'final_snapshot_hash': 'x'}
        self.assertEqual(self.writer.append_analysis_binding(ID.uri, binding=binding), 'APPENDED')
        self.assertEqual(self.writer.append_analysis_binding(ID.uri, binding=binding), 'IDEMPOTENT_NOOP')
        with self.assertRaisesRegex(ArchiveError, 'IDENTITY_CONFLICT'):
            self.writer.append_analysis_binding(ID.uri, binding={**binding, 'final_snapshot_hash': 'other'})
        self.reader.binding(ID.uri, 'report', binding)

    def test_append_sync_failure_does_not_commit_event(self):
        write(self.writer)
        with patch.object(self.writer, '_sync_dir', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.writer.append_analysis_binding(ID.uri, binding={'analysis_report_id': 'report'})
        self.assertFalse(list(self.root.rglob('binding-*')))
        self.reader.read(ID.uri)

    def test_busy_writer_does_not_wait_or_start_daemon(self):
        self.root.mkdir()
        (self.root / '.archive-write-lock').mkdir()
        with self.assertRaisesRegex(ArchiveError, 'WRITER_BUSY'):
            write(self.writer)

    def test_rename_and_link_errors_after_effect_leave_no_committed_metadata(self):
        rename = os.rename
        def ambiguous_rename(source, target):
            rename(source, target)
            raise OSError('injected post-rename error')
        with patch('evidence.archive_writer.os.rename', side_effect=ambiguous_rename):
            with self.assertRaises(OSError):
                write(self.writer)
        with self.assertRaisesRegex(ArchiveError, 'ORPHAN_INCOMPLETE'):
            self.reader.read(ID.uri)
        identity = replace(ID, evidence_id='append')
        write(self.writer, identity=identity)
        link = os.link
        def ambiguous_link(source, target):
            link(source, target)
            raise OSError('injected post-link error')
        with patch('evidence.archive_writer.os.link', side_effect=ambiguous_link):
            with self.assertRaises(OSError):
                self.writer.append_analysis_binding(identity.uri, binding={'analysis_report_id': 'report'})
        self.assertFalse(list(self.writer.resolver.resolve(identity.uri).glob('binding-*')))
        self.reader.read(identity.uri)
