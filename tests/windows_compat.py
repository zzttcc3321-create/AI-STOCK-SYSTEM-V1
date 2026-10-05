"""Windows-only test seams that preserve filesystem security assertions."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch


@contextmanager
def simulated_directory_fsync():
    """Simulate successful directory fsync only inside Windows tests.

    Return the unpatched production method so fail-closed tests can exercise it
    explicitly while the surrounding test continues to use this seam.
    """
    from evidence import ArchiveWriter

    production_sync_dir = ArchiveWriter._sync_dir
    if os.name != "nt":
        yield production_sync_dir
        return
    with patch.object(ArchiveWriter, "_sync_dir", autospec=True):
        yield production_sync_dir


def use_simulated_directory_fsync(test_case):
    """Enter the durability seam for one unittest.TestCase lifecycle."""
    context = simulated_directory_fsync()
    production_sync_dir = context.__enter__()
    test_case.addCleanup(context.__exit__, None, None, None)
    return production_sync_dir


def symlink_privilege_missing(exc):
    """Return true only for Windows' unprivileged symlink error (1314)."""
    return os.name == "nt" and getattr(exc, "winerror", None) == 1314


@contextmanager
def marked_reparse_point(path):
    """Expose an existing test path as a reparse point through lstat()."""
    path = Path(path)
    real_lstat = Path.lstat

    def lstat(candidate, *args, **kwargs):
        info = real_lstat(candidate, *args, **kwargs)
        if candidate == path:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=(getattr(info, "st_file_attributes", 0) |
                                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
            )
        return info

    with patch.object(Path, "lstat", new=lstat):
        yield
