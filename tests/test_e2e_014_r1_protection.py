import unittest
from unittest.mock import patch

from tools.e2e_014_r1 import verify_protected_paths


class RuntimeClosureProtectionTests(unittest.TestCase):
    def test_approved_014_r2_contract_is_the_only_frozen_exception(self):
        path = 'docs/frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md'
        with patch('tools.e2e_014_r1.git', return_value=path) as git:
            self.assertEqual(verify_protected_paths('baseline'), [path])
        git.assert_called_once_with('diff', '--name-only', 'baseline', 'HEAD', '--',
                                    'migration', 'persistence/migration.py',
                                    'persistence/schema_v1_1.py', 'docs/frozen')

    def test_other_protected_changes_fail_even_alongside_approved_contract(self):
        for path in ('docs/frozen/EVIDENCE_PIPELINE_DESIGN_V1.0.md',
                     'docs/frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md.bak',
                     'docs/frozen/nested/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md',
                     'migration/new.sql', 'persistence/migration.py',
                     'persistence/schema_v1_1.py'):
            with self.subTest(path=path), patch('tools.e2e_014_r1.git', return_value=
                    'docs/frozen/EVIDENCE_ARCHIVE_SIDECAR_V1.0.md\n' + path):
                with self.assertRaisesRegex(AssertionError, 'Protected paths changed'):
                    verify_protected_paths('baseline')

    def test_no_protected_changes_has_no_exception(self):
        with patch('tools.e2e_014_r1.git', return_value=''):
            self.assertEqual(verify_protected_paths('baseline'), [])
