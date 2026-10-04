import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evidence import ArchiveError, ArchiveIdentity, ArchiveResolver


class ArchiveResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / 'outside' / 'target'
        self.target.mkdir(parents=True)
        self.link = self.base / 'link'

    def test_symlink_root_rejected(self):
        self.link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaisesRegex(ArchiveError, '^SYMLINK_ESCAPE_REJECTED$'):
            ArchiveResolver(self.link)

    def test_symlink_intermediate_component_rejected(self):
        self.link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaisesRegex(ArchiveError, '^SYMLINK_ESCAPE_REJECTED$'):
            ArchiveResolver(self.link / 'archive')

    def test_symlink_before_parent_traversal_rejected(self):
        self.link.symlink_to(self.target, target_is_directory=True)
        (self.base / 'archive').mkdir()
        (self.target.parent / 'archive').mkdir()
        absolute = self.link / '..' / 'archive'
        self.assertNotEqual(os.path.abspath(absolute), os.path.realpath(absolute))
        for root in (absolute, Path('link') / '..' / 'archive'):
            with self.subTest(root=root), patch('pathlib.Path.cwd', return_value=self.base):
                with self.assertRaisesRegex(ArchiveError, '^SYMLINK_ESCAPE_REJECTED$'):
                    ArchiveResolver(root)

    def test_reparse_point_in_original_root_chain_rejected(self):
        junction = self.base / 'junction'
        junction.mkdir()
        lstat = Path.lstat
        def reparse_info(path):
            if path == junction:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return lstat(path)
        for root in (junction, junction / 'archive', junction / '..' / 'archive'):
            with self.subTest(root=root), patch.object(Path, 'lstat', new=reparse_info):
                with self.assertRaisesRegex(ArchiveError, '^SYMLINK_ESCAPE_REJECTED$'):
                    ArchiveResolver(root)

    def test_symlink_free_root_is_canonical_even_when_missing(self):
        (self.base / 'directory').mkdir()
        for name in ('directory', 'missing'):
            for root in (self.base / 'directory' / '..' / name,
                         Path('directory') / '..' / name):
                with self.subTest(root=root), patch('pathlib.Path.cwd', return_value=self.base), \
                        patch('os.getcwd', return_value=str(self.base)):
                    resolver = ArchiveResolver(root)
                self.assertEqual(resolver.root, self.base / name)

    def test_resolve_still_rejects_symlink_added_after_initialization(self):
        resolver = ArchiveResolver(self.base / 'archive')
        (resolver.root / 'v1').mkdir(parents=True)
        (resolver.root / 'v1' / 'run').symlink_to(self.target, target_is_directory=True)
        identity = ArchiveIdentity('run', 'collection', 'request', 'evidence')
        with self.assertRaisesRegex(ArchiveError, '^SYMLINK_ESCAPE_REJECTED$'):
            resolver.resolve(identity.uri)
