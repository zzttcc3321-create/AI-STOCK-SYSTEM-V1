import unittest
from evidence.archive_contracts import (ArchiveError, ArchiveIdentity, canonical_json,
    strict_json, sanitized, bundle_hash, sha256, json_hash)


class ArchiveContractTests(unittest.TestCase):
    def test_uri_exact(self):
        identity = ArchiveIdentity('run', 'collection', 'request', 'evidence')
        self.assertEqual(identity.uri, 'archive://v1/run/collection/request/evidence')
        self.assertEqual(ArchiveIdentity.parse(identity.uri), identity)

    def test_bad_uri_and_traversal(self):
        for value in ('/tmp/raw', 'tdx-local/run/task/request/raw.json', 'tushare://daily/x/y/z',
                      'archive://v2/r/c/q/e', 'archive://v1/r/c/q', 'archive://v1/r/c/q/e/',
                      'archive://v1/r/c/q/..', 'archive://v1/r/c/q/%2e%2e',
                      'archive://v1/r/c/q/e?x=1', 'archive://v1/r/c/q/e#x',
                      'archive://v1/r/c/q/a\\b', 'archive://v1/r/c/q/é', None):
            with self.subTest(uri=value), self.assertRaises(ArchiveError):
                ArchiveIdentity.parse(value)

    def test_identifiers_are_bounded_strict_text(self):
        for value in ('', '.', '..', 'a/b', 'a:b', 'a' * 101, 123, True, ' a'):
            with self.subTest(value=value), self.assertRaises(ArchiveError):
                ArchiveIdentity(value, 'c', 'q', 'e')

    def test_canonical_json(self):
        self.assertEqual(canonical_json({'z': '中文', 'a': [True, None]}),
                         '{"a":[true,null],"z":"中文"}'.encode())

    def test_duplicate_keys_at_any_depth(self):
        for data in (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}'):
            with self.assertRaisesRegex(ArchiveError, 'DUPLICATE'):
                strict_json(data)

    def test_nonfinite_and_non_json_types(self):
        for data in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            with self.assertRaises(ArchiveError):
                strict_json(data)
        for value in (float('nan'), float('inf'), object(), b'raw', {1: 'x'}):
            with self.assertRaises(ArchiveError):
                canonical_json(value)

    def test_sensitive_request_fields_removed(self):
        value = {'stock_id': 'mock', 'API-Key': 'x', 'nested': [{'Authorization': 'x',
                 'access_token': 'x', 'Cookie': 'x', 'sessionId': 'x', 'safe': 2}]}
        self.assertEqual(sanitized(value), {'stock_id': 'mock', 'nested': [{'safe': 2}]})
        self.assertIn('API-Key', value)

    def test_bundle_hash_named_ordered_and_length_delimited(self):
        a = dict(name='a', raw_size=1, raw_sha256=sha256(b'x'))
        b = dict(name='b', raw_size=2, raw_sha256=sha256(b'yz'))
        self.assertEqual(bundle_hash([a]), sha256(b'x'))
        self.assertEqual(bundle_hash([a, b]), json_hash([a, b]))
        self.assertNotEqual(bundle_hash([a, b]), bundle_hash([b, a]))
        self.assertNotEqual(bundle_hash([a, b]), sha256(b'xyz'))
