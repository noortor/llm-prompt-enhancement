import json
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, SCHEMA_PATH


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = _connect()
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def db_exists() -> bool:
    return DB_PATH.exists()


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row is not None else None


def rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def dumps(obj) -> str:
    return json.dumps(obj)


def loads(s: str):
    return json.loads(s) if s else []
