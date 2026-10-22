
"""MySQL connection helper for background ingestion tasks."""

import pymysql
from pymysql.cursors import DictCursor

from .settings import (
    DATABASE_HOST,
    DATABASE_PORT,
    DATABASE_USER,
    DATABASE_PASSWORD,
    DATABASE_NAME,
)


def get_connection():
    """Open a MySQL connection using the worker's environment."""

    if not DATABASE_PASSWORD:
        raise RuntimeError(
            "DATABASE_PASSWORD is not configured"
        )

    return pymysql.connect(
        host=DATABASE_HOST,
        port=DATABASE_PORT,
        user=DATABASE_USER,
        password=DATABASE_PASSWORD,
        database=DATABASE_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
    )
