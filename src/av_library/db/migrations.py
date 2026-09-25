"""Append-only migrations; do not use create_all as an upgrade mechanism."""

from sqlalchemy import Connection, text

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """CREATE TABLE actresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL CHECK(length(trim(name)) > 0),
            name_key TEXT NOT NULL UNIQUE,
            japanese_name TEXT NOT NULL DEFAULT '',
            aliases JSON NOT NULL DEFAULT '[]',
            folder_path TEXT NOT NULL,
            folder_key TEXT NOT NULL UNIQUE,
            avatar_path TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            last_synced_at DATETIME,
            last_scanned_at DATETIME
        )""",
            """CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value JSON NOT NULL
        )""",
            """INSERT INTO settings(key, value) VALUES
            ('scan_recursive', 'true'),
            ('read_ffprobe', 'false'),
            ('video_extensions', '[".mp4",".mkv",".avi",".mov",".wmv",".ts",".m2ts",".flv",".webm"]'),
            ('update_interval_hours', '0')""",
        ),
    ),
    (
        2,
        (
            """CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
                root_path TEXT NOT NULL,
                recursive BOOLEAN NOT NULL,
                extensions JSON NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed','cancelled')),
                started_at DATETIME NOT NULL,
                finished_at DATETIME,
                files_seen INTEGER NOT NULL DEFAULT 0,
                skipped_links INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT
            )""",
            """CREATE TABLE local_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                path_key TEXT NOT NULL,
                scan_root_key TEXT NOT NULL,
                filename TEXT NOT NULL,
                normalized_code TEXT,
                manual_code TEXT,
                candidates JSON NOT NULL,
                parser_rule_id TEXT,
                parse_status TEXT NOT NULL CHECK(parse_status IN ('recognized','unrecognized','ambiguous')),
                is_present BOOLEAN NOT NULL DEFAULT 1,
                first_seen_time DATETIME NOT NULL,
                last_seen_time DATETIME NOT NULL,
                last_seen_scan_id INTEGER REFERENCES scan_history(id) ON DELETE SET NULL,
                UNIQUE(actress_id, path_key)
            )""",
            "CREATE INDEX ix_local_files_actress ON local_files(actress_id, is_present)",
            "CREATE INDEX ix_local_files_code ON local_files(normalized_code)",
            "DELETE FROM settings WHERE key = 'read_ffprobe'",
        ),
    ),
    (
        3,
        (
            """CREATE TABLE providers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                coverage_description TEXT NOT NULL DEFAULT ''
            )""",
            """CREATE TABLE actress_sources (
                actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
                provider_id TEXT NOT NULL REFERENCES providers(id),
                external_id TEXT NOT NULL,
                confirmed_at DATETIME NOT NULL,
                last_success_at DATETIME,
                PRIMARY KEY(actress_id, provider_id),
                UNIQUE(provider_id, external_id)
            )""",
            """CREATE TABLE movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL DEFAULT 'jp_standard',
                code TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                japanese_title TEXT NOT NULL DEFAULT '',
                release_date DATE,
                manufacturer TEXT,
                publisher TEXT,
                series TEXT,
                cover_url TEXT,
                detail_url TEXT,
                field_provenance JSON NOT NULL DEFAULT '{}',
                first_seen_time DATETIME NOT NULL,
                last_update_time DATETIME NOT NULL,
                UNIQUE(namespace, code)
            )""",
            "CREATE INDEX ix_movies_release ON movies(release_date DESC)",
            """CREATE TABLE actress_movies (
                actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
                movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                is_ignored BOOLEAN NOT NULL DEFAULT 0,
                first_seen_time DATETIME NOT NULL,
                PRIMARY KEY(actress_id, movie_id)
            )""",
            """CREATE TABLE movie_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                provider_id TEXT NOT NULL REFERENCES providers(id),
                external_id TEXT NOT NULL,
                raw_code TEXT NOT NULL,
                detail_url TEXT,
                last_seen_time DATETIME NOT NULL,
                UNIQUE(provider_id, external_id)
            )""",
            """CREATE TABLE sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
                provider_id TEXT NOT NULL REFERENCES providers(id),
                started_at DATETIME NOT NULL,
                finished_at DATETIME,
                status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed','cancelled')),
                is_baseline BOOLEAN NOT NULL,
                coverage_complete BOOLEAN NOT NULL DEFAULT 0,
                pages_received INTEGER NOT NULL DEFAULT 0,
                items_received INTEGER NOT NULL DEFAULT 0,
                added_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT
            )""",
            """CREATE TABLE sync_items (
                sync_id INTEGER NOT NULL REFERENCES sync_history(id) ON DELETE CASCADE,
                movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                change_type TEXT NOT NULL CHECK(change_type IN ('added','updated','unchanged')),
                PRIMARY KEY(sync_id, movie_id)
            )""",
            "CREATE INDEX ix_actress_movies_movie ON actress_movies(movie_id)",
            "CREATE INDEX ix_movie_sources_movie ON movie_sources(movie_id)",
            "CREATE INDEX ix_sync_history_actress ON sync_history(actress_id, id DESC)",
            "CREATE INDEX ix_sync_items_movie ON sync_items(movie_id)",
        ),
    ),
    (
        4,
        (
            """CREATE TABLE movie_matches (
                local_file_id INTEGER PRIMARY KEY REFERENCES local_files(id) ON DELETE CASCADE,
                movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
                method TEXT NOT NULL CHECK(method IN ('exact','normalized','manual')),
                matched_at DATETIME NOT NULL
            )""",
            "CREATE INDEX ix_movie_matches_movie ON movie_matches(movie_id)",
        ),
    ),
    (
        5,
        (
            "ALTER TABLE movies ADD COLUMN is_compilation BOOLEAN NOT NULL DEFAULT 0",
            """UPDATE movies SET is_compilation = 1
               WHERE upper(title) LIKE '%BEST%'
                  OR title LIKE '%ベスト%'
                  OR title LIKE '%総集編%'
                  OR title LIKE '%コンプリート%'
                  OR upper(title) LIKE '%COLLECTION%'
                  OR title LIKE '%合集%'
                  OR title LIKE '%精选%'
                  OR title LIKE '%总集%'""",
        ),
    ),
    (
        6,
        (
            "ALTER TABLE movies ADD COLUMN is_solo BOOLEAN NOT NULL DEFAULT 1",
        ),
    ),
)


def migrate(connection: Connection) -> None:
    connection.execute(
        text("""CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )""")
    )
    applied = list(
        connection.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars()
    )
    expected = [version for version, _ in MIGRATIONS]
    if applied != expected[: len(applied)]:
        raise RuntimeError("数据库版本不兼容，请使用创建此数据库的软件版本。")
    for version, statements in MIGRATIONS:
        if version not in applied:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(
                text("INSERT INTO schema_migrations(version) VALUES (:version)"),
                {"version": version},
            )
