import sqlite3
from contextlib import contextmanager

from signaldesk.settings import ROOT, database_path


@contextmanager
def connect(*, readonly=False):
    path = database_path()
    if readonly:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        yield connection
        if not readonly:
            connection.commit()
    except BaseException:
        if not readonly:
            connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with connect() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript((ROOT / "signaldesk/schema.sql").read_text())


def insert(connection, table: str, row: dict):
    # Only application-owned identifiers are passed; every value is parameterized.
    fields = ",".join(row)
    placeholders = ",".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO {table} ({fields}) VALUES ({placeholders})", tuple(row.values())
    )


def active_version(connection, at: str | None = None) -> str:
    from signaldesk.settings import stamp

    row = connection.execute(
        "SELECT model_version FROM deployments WHERE deployed_at<=? "
        "ORDER BY deployed_at DESC,id DESC LIMIT 1",
        (at or stamp(),),
    ).fetchone()
    if not row:
        raise RuntimeError("No deployment found. Run python -m signaldesk.cli bootstrap")
    return row[0]
