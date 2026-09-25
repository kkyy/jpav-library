from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Actress(Base):
    __tablename__ = "actresses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    name_key: Mapped[str] = mapped_column(String, unique=True)
    japanese_name: Mapped[str] = mapped_column(String, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    folder_path: Mapped[str] = mapped_column(String)
    folder_key: Mapped[str] = mapped_column(String, unique=True)
    avatar_path: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class ScanHistory(Base):
    __tablename__ = "scan_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    actress_id: Mapped[int] = mapped_column(ForeignKey("actresses.id", ondelete="CASCADE"))
    root_path: Mapped[str]
    recursive: Mapped[bool]
    extensions: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str]
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    files_seen: Mapped[int] = mapped_column(default=0)
    skipped_links: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str | None]


class LocalFile(Base):
    __tablename__ = "local_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    actress_id: Mapped[int] = mapped_column(ForeignKey("actresses.id", ondelete="CASCADE"))
    path: Mapped[str]
    path_key: Mapped[str]
    scan_root_key: Mapped[str]
    filename: Mapped[str]
    normalized_code: Mapped[str | None]
    manual_code: Mapped[str | None]
    candidates: Mapped[list[str]] = mapped_column(JSON)
    parser_rule_id: Mapped[str | None]
    parse_status: Mapped[str]
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_time: Mapped[datetime]
    last_seen_time: Mapped[datetime]
    last_seen_scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_history.id", ondelete="SET NULL")
    )

    @property
    def effective_code(self) -> str | None:
        return self.manual_code or self.normalized_code


class ProviderRow(Base):
    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    coverage_description: Mapped[str]


class ActressSource(Base):
    __tablename__ = "actress_sources"
    actress_id: Mapped[int] = mapped_column(
        ForeignKey("actresses.id", ondelete="CASCADE"), primary_key=True
    )
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"), primary_key=True)
    external_id: Mapped[str]
    confirmed_at: Mapped[datetime]
    last_success_at: Mapped[datetime | None]


class Movie(Base):
    __tablename__ = "movies"
    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(default="jp_standard")
    code: Mapped[str]
    title: Mapped[str] = mapped_column(default="")
    japanese_title: Mapped[str] = mapped_column(default="")
    release_date: Mapped[date | None] = mapped_column(Date)
    manufacturer: Mapped[str | None]
    publisher: Mapped[str | None]
    series: Mapped[str | None]
    cover_url: Mapped[str | None]
    detail_url: Mapped[str | None]
    is_compilation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_solo: Mapped[bool] = mapped_column(Boolean, default=True)
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    first_seen_time: Mapped[datetime]
    last_update_time: Mapped[datetime]


class ActressMovie(Base):
    __tablename__ = "actress_movies"
    actress_id: Mapped[int] = mapped_column(
        ForeignKey("actresses.id", ondelete="CASCADE"), primary_key=True
    )
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True
    )
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_time: Mapped[datetime]


class MovieSource(Base):
    __tablename__ = "movie_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    external_id: Mapped[str]
    raw_code: Mapped[str]
    detail_url: Mapped[str | None]
    last_seen_time: Mapped[datetime]


class SyncHistory(Base):
    __tablename__ = "sync_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    actress_id: Mapped[int] = mapped_column(ForeignKey("actresses.id", ondelete="CASCADE"))
    provider_id: Mapped[str] = mapped_column(ForeignKey("providers.id"))
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str]
    is_baseline: Mapped[bool]
    coverage_complete: Mapped[bool]
    pages_received: Mapped[int]
    items_received: Mapped[int]
    added_count: Mapped[int]
    updated_count: Mapped[int]
    error_summary: Mapped[str | None]


class SyncItem(Base):
    __tablename__ = "sync_items"
    sync_id: Mapped[int] = mapped_column(
        ForeignKey("sync_history.id", ondelete="CASCADE"), primary_key=True
    )
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True
    )
    change_type: Mapped[str]


class MovieMatch(Base):
    __tablename__ = "movie_matches"

    local_file_id: Mapped[int] = mapped_column(
        ForeignKey("local_files.id", ondelete="CASCADE"), primary_key=True
    )
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    method: Mapped[str]
    matched_at: Mapped[datetime]
