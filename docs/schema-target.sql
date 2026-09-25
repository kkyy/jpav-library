-- Full target design, NOT a migration. Execute only in an empty scratch database.
-- Later phases append versioned migrations to the real Phase 1 database.
PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE actresses (
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
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value JSON NOT NULL);

CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    config_json JSON NOT NULL DEFAULT '{}', -- Never store API secrets here.
    coverage_description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE actress_sources (
    actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    external_id TEXT NOT NULL,
    confirmed_at DATETIME NOT NULL,
    last_success_at DATETIME,
    PRIMARY KEY(actress_id, provider_id),
    UNIQUE(provider_id, external_id)
);

CREATE TABLE movies (
    id INTEGER PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'jp_standard',
    code TEXT NOT NULL CHECK(length(trim(code)) > 0),
    title TEXT NOT NULL DEFAULT '',
    japanese_title TEXT NOT NULL DEFAULT '',
    release_date DATE,
    manufacturer TEXT,
    publisher TEXT,
    series TEXT,
    cover_url TEXT,
    cover_cache_path TEXT,
    detail_url TEXT,
    primary_provider_id TEXT REFERENCES providers(id),
    field_provenance JSON NOT NULL DEFAULT '{}',
    locked_fields JSON NOT NULL DEFAULT '[]',
    metadata_conflict INTEGER NOT NULL DEFAULT 0 CHECK(metadata_conflict IN (0, 1)),
    first_seen_time DATETIME NOT NULL,
    last_update_time DATETIME NOT NULL,
    UNIQUE(namespace, code)
);
CREATE INDEX ix_movies_release ON movies(release_date DESC);

CREATE TABLE actress_movies (
    actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    is_ignored INTEGER NOT NULL DEFAULT 0 CHECK(is_ignored IN (0, 1)),
    ignore_reason TEXT,
    first_seen_time DATETIME NOT NULL,
    PRIMARY KEY(actress_id, movie_id)
);
CREATE INDEX ix_actress_movies_movie ON actress_movies(movie_id);

CREATE TABLE movie_sources (
    id INTEGER PRIMARY KEY,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    external_id TEXT NOT NULL,
    raw_code TEXT NOT NULL,
    detail_url TEXT,
    metadata_json JSON NOT NULL DEFAULT '{}',
    last_seen_time DATETIME NOT NULL,
    UNIQUE(provider_id, external_id)
);
CREATE INDEX ix_movie_sources_movie ON movie_sources(movie_id);

CREATE TABLE scan_history (
    id INTEGER PRIMARY KEY,
    actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
    root_path TEXT NOT NULL,
    recursive INTEGER NOT NULL CHECK(recursive IN (0, 1)),
    extensions JSON NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed','cancelled')),
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    files_seen INTEGER NOT NULL DEFAULT 0,
    skipped_links INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE local_files (
    id INTEGER PRIMARY KEY,
    actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    scan_root_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    -- Filename-only scanning: no content, size, filesystem timestamps or ffprobe.
    normalized_code TEXT,
    manual_code TEXT,
    parser_rule_id TEXT,
    candidates JSON NOT NULL DEFAULT '[]',
    parse_status TEXT NOT NULL CHECK(parse_status IN ('recognized','unrecognized','ambiguous')),
    is_present INTEGER NOT NULL DEFAULT 1 CHECK(is_present IN (0, 1)),
    last_seen_scan_id INTEGER REFERENCES scan_history(id) ON DELETE SET NULL,
    first_seen_time DATETIME NOT NULL,
    last_seen_time DATETIME NOT NULL,
    UNIQUE(actress_id, path_key)
);
CREATE INDEX ix_local_files_code ON local_files(normalized_code);

CREATE TABLE movie_matches (
    local_file_id INTEGER PRIMARY KEY REFERENCES local_files(id) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    method TEXT NOT NULL CHECK(method IN ('exact','normalized','manual')),
    matched_at DATETIME NOT NULL
);
CREATE INDEX ix_movie_matches_movie ON movie_matches(movie_id);

CREATE TABLE sync_history (
    id INTEGER PRIMARY KEY,
    actress_id INTEGER NOT NULL REFERENCES actresses(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed','cancelled')),
    is_baseline INTEGER NOT NULL CHECK(is_baseline IN (0, 1)),
    coverage_complete INTEGER NOT NULL DEFAULT 0 CHECK(coverage_complete IN (0, 1)),
    pages_received INTEGER NOT NULL DEFAULT 0,
    items_received INTEGER NOT NULL DEFAULT 0,
    added_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE sync_items (
    sync_id INTEGER NOT NULL REFERENCES sync_history(id) ON DELETE CASCADE,
    movie_id INTEGER NOT NULL REFERENCES movies(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL CHECK(change_type IN ('added','updated','unchanged')),
    PRIMARY KEY(sync_id, movie_id)
);

-- Counts are derived. A file belonging to a shared work is usable from either actress view.
-- is_present changes only after a successful scan of its declared scope.
CREATE VIEW actress_movie_status AS
SELECT am.actress_id, am.movie_id, am.is_ignored,
    EXISTS (
        SELECT 1 FROM movie_matches mm
        JOIN local_files lf ON lf.id = mm.local_file_id
        WHERE mm.movie_id = am.movie_id AND lf.is_present = 1
    ) AS is_collected,
    (SELECT count(*) FROM movie_matches mm
        JOIN local_files lf ON lf.id = mm.local_file_id
        WHERE mm.movie_id = am.movie_id AND lf.is_present = 1) AS local_file_count
FROM actress_movies am;
