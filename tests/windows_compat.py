"""Windows-only test seams that preserve filesystem security assertions."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch


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
