"""Explicit local runtime selectors; no automatic creation or migration."""
import importlib
import os

from evidence import ArchiveWriter
from persistence.connection import open_database
from .runtime import DashboardRuntime


def create_runtime():
    db_path = os.environ['V1_RUNTIME_DB_SELECTOR']
    archive_root = os.environ['V1_RUNTIME_ARCHIVE_ROOT']
    executor = None
    selector = os.environ.get('V1_RUNTIME_EXECUTOR_FACTORY')
    if selector:
        module, function = selector.split(':', 1)
        executor = getattr(importlib.import_module(module), function)()
    db = open_database(db_path)
    try:
        return DashboardRuntime(db, archive_writer=ArchiveWriter(archive_root), executor=executor)
    except BaseException:
        db.close()
        raise
