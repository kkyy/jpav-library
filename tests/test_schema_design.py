import sqlite3
from pathlib import Path


def test_target_schema_is_valid_and_has_no_fk_violations():
    schema = Path(__file__).resolve().parents[1] / "docs" / "schema-target.sql"
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM actress_movie_status").fetchall() == []
