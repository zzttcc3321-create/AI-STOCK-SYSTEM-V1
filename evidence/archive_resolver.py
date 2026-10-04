"""Deterministic URI mapping within an explicitly configured private root."""
import os
from pathlib import Path
import stat

from .archive_contracts import ArchiveIdentity, ArchiveError, require


class ArchiveResolver:
    def __init__(self, root):
        root = Path(root)
        if not root.is_absolute():
            root = Path.cwd() / root
        # Keep '..' until every original ancestor has passed lstat checks.
        self.safe(root)
        self.root = Path(os.path.realpath(root))

    def safe(self, path):
        path = Path(path)
        # Inspect all ancestors including the configured root; resolve() alone
        # would silently bless a root supplied through a symlink.
        for item in reversed((path, *path.parents)):
            try:
                info = item.lstat()
                mode = info.st_mode
            except FileNotFoundError:
                continue
            require(not stat.S_ISLNK(mode) and not (getattr(info, "st_file_attributes", 0) &
                    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)), "SYMLINK_ESCAPE_REJECTED")
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), "SPECIAL_FILE_REJECTED")
        return path

    def resolve(self, uri, *, expected=None):
        identity = ArchiveIdentity.parse(uri)
        if expected is not None:
            require(identity == expected, "ARCHIVE_CONTEXT_MISMATCH")
        return self.safe(self.root.joinpath("v1", *identity.components))

    def file(self, directory, name):
        require(Path(name).name == name and name not in {".", ".."}, "ARCHIVE_FILENAME_INVALID")
        return self.safe(directory / name)

    def open_read(self, path):
        self.safe(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            os.close(fd)
            raise ArchiveError("SPECIAL_FILE_REJECTED")
        return os.fdopen(fd, "rb")
