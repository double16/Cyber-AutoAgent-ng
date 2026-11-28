"""Regression checks for test-owned SQLite connection cleanup."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.conftest import close_sqlite_connections_after_test


@pytest.mark.parametrize("explicitly_closed", [True, False])
def test_cleanup_closes_leaked_connections_and_accepts_closed_connections(explicitly_closed):
    with pytest.MonkeyPatch.context() as patch:
        cleanup = close_sqlite_connections_after_test.__wrapped__(patch)
        next(cleanup)
        connection = sqlite3.connect(":memory:")
        if explicitly_closed:
            connection.close()
        with pytest.raises(StopIteration):
            next(cleanup)
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_cleanup_accepts_connections_already_closed_by_their_worker_thread():
    with pytest.MonkeyPatch.context() as patch:
        cleanup = close_sqlite_connections_after_test.__wrapped__(patch)
        next(cleanup)
        def worker():
            connection = sqlite3.connect(":memory:")
            connection.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(worker).result()
        with pytest.raises(StopIteration):
            next(cleanup)
