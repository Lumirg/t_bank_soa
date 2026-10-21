import os
import uuid
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def _param(value):
    if isinstance(value, str) and len(value) == 36 and value.count("-") == 4:
        try:
            return uuid.UUID(value)
        except ValueError:
            return value
    return value


class _Connection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=None):
        if params is not None:
            params = tuple(_param(value) for value in params)
        return self._connection.execute(query, params)


@contextmanager
def transaction():
    with pool().connection() as connection:
        with connection.transaction():
            yield _Connection(connection)
