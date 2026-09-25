"""Conservative filename-to-catalog matching and actress-specific collection statistics."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from av_library.db.database import Database
from av_library.db.models import (
    Actress,
    ActressMovie,
    LocalFile,
    Movie,
    MovieMatch,
    SyncHistory,
)
from av_library.scanner.files import path_key
from av_library.services.actresses import ValidationError


@dataclass(frozen=True)
class MovieView:
    movie: Movie
    is_ignored: bool
    local_paths: tuple[str, ...]

    @property
    def is_compilation(self) -> bool:
        return bool(self.movie.is_compilation)

    @property
    def is_solo(self) -> bool:
        return bool(self.movie.is_solo)

    @property
    def status(self) -> str:
        if self.is_compilation:
            return "compilation"
        if not self.is_solo:
            return "multi_actress"
        if self.is_ignored:
            return "ignored"
        return "collected" if self.local_paths else "missing"


@dataclass(frozen=True)
class CollectionSummary:
    total: int
    ignored: int
    collected: int
    missing: int
    completion_percent: float | None
    latest: MovieView | None
    new_count: int
    scanned: bool
    compilations: int = 0
    multi_actress: int = 0

    @property
    def needed(self) -> int:
        return self.total - self.ignored


class MatchService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def reconcile_session(session: Session, actress_id: int) -> None:
        actress = session.get(Actress, actress_id)
        if actress is None:
            raise ValidationError("女优记录不存在。")
        movies = {
            movie.code: movie
            for movie in session.scalars(
                select(Movie)
                .join(ActressMovie, ActressMovie.movie_id == Movie.id)
                .where(ActressMovie.actress_id == actress_id, Movie.namespace == "jp_standard")
            )
        }
        root = path_key(actress.folder_path)
        now = datetime.now(UTC).replace(tzinfo=None)
        existing_matches = {
            match.local_file_id: match
            for match in session.scalars(
                select(MovieMatch)
                .join(LocalFile, LocalFile.id == MovieMatch.local_file_id)
                .where(LocalFile.actress_id == actress_id)
            )
        }
        for local in session.scalars(select(LocalFile).where(LocalFile.actress_id == actress_id)):
            existing = existing_matches.get(local.id)
            movie = (
                movies.get(local.effective_code)
                if local.is_present and local.scan_root_key == root and local.effective_code
                else None
            )
            if movie is None:
                if existing is not None:
                    session.delete(existing)
                continue
            method = (
                "manual"
                if local.manual_code
                else "exact"
                if Path(local.filename).stem.casefold() == movie.code.casefold()
                else "normalized"
            )
            if existing is None:
                session.add(
                    MovieMatch(
                        local_file_id=local.id,
                        movie_id=movie.id,
                        method=method,
                        matched_at=now,
                    )
                )
            elif existing.movie_id != movie.id or existing.method != method:
                existing.movie_id, existing.method, existing.matched_at = movie.id, method, now

    def reconcile(self, actress_id: int) -> None:
        with self.database.sessions.begin() as session:
            self.reconcile_session(session, actress_id)

    def set_ignored(self, actress_id: int, movie_id: int, ignored: bool) -> None:
        with self.database.sessions.begin() as session:
            relation = session.get(ActressMovie, (actress_id, movie_id))
            if relation is None:
                raise ValidationError("作品不属于该女优的作品库，请刷新。")
            relation.is_ignored = ignored

    def views(self, actress_id: int) -> list[MovieView]:
        with self.database.sessions() as session:
            if session.get(Actress, actress_id) is None:
                raise ValidationError("女优记录不存在。")
            relations = list(
                session.execute(
                    select(Movie, ActressMovie.is_ignored)
                    .join(ActressMovie, ActressMovie.movie_id == Movie.id)
                    .where(ActressMovie.actress_id == actress_id)
                    .order_by(Movie.release_date.desc().nulls_last(), Movie.code)
                )
            )
            ids = {movie.id for movie, _ignored in relations}
            paths: dict[int, list[str]] = {movie_id: [] for movie_id in ids}
            if ids:
                for match, local, owner in session.execute(
                    select(MovieMatch, LocalFile, Actress)
                    .join(LocalFile, LocalFile.id == MovieMatch.local_file_id)
                    .join(Actress, Actress.id == LocalFile.actress_id)
                    .where(MovieMatch.movie_id.in_(ids), LocalFile.is_present.is_(True))
                ):
                    if local.scan_root_key == path_key(owner.folder_path):
                        paths[match.movie_id].append(local.path)
            return [
                MovieView(movie, bool(ignored), tuple(paths[movie.id]))
                for movie, ignored in relations
            ]

    def summary(self, actress_id: int, views: list[MovieView] | None = None) -> CollectionSummary:
        views = self.views(actress_id) if views is None else views
        compilations = sum(view.is_compilation for view in views)
        multi_actress = sum(not view.is_solo for view in views if not view.is_compilation)
        eligible = [view for view in views if not view.is_compilation and view.is_solo]
        ignored = sum(view.is_ignored for view in eligible)
        collected = sum(view.status == "collected" for view in eligible)
        missing = len(eligible) - ignored - collected
        needed = len(eligible) - ignored
        latest = eligible[0] if eligible else None
        with self.database.sessions() as session:
            actress = session.get(Actress, actress_id)
            if actress is None:
                raise ValidationError("女优记录不存在。")
            recent = session.scalar(
                select(SyncHistory)
                .where(SyncHistory.actress_id == actress_id, SyncHistory.status == "success")
                .order_by(SyncHistory.id.desc())
                .limit(1)
            )
            new_count = recent.added_count if recent and not recent.is_baseline else 0
        return CollectionSummary(
            len(eligible),
            ignored,
            collected,
            missing,
            round(collected * 100 / needed, 1) if needed else None,
            latest,
            new_count,
            actress.last_scanned_at is not None,
            compilations,
            multi_actress,
        )

    def file_matches(self, actress_id: int) -> dict[int, MovieMatch]:
        with self.database.sessions() as session:
            return {
                match.local_file_id: match
                for match in session.scalars(
                    select(MovieMatch)
                    .join(LocalFile, LocalFile.id == MovieMatch.local_file_id)
                    .where(LocalFile.actress_id == actress_id)
                )
            }
