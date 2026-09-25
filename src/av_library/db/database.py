from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import sessionmaker

from av_library.db.migrations import migrate


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(URL.create("sqlite", database=str(path)))

        @event.listens_for(self.engine, "connect")
        def configure_connection(connection, _record):
            # Explicit BEGIN makes DDL transactional on Python's SQLite driver.
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            connection.exec_driver_sql("BEGIN")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        with self.engine.begin() as connection:
            migrate(connection)

    def close(self) -> None:
        self.engine.dispose()
