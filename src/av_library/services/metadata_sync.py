"""Provider-neutral identity binding and all-or-nothing metadata sync."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from av_library.db.database import Database
from av_library.db.models import (
    Actress,
    ActressMovie,
    ActressSource,
    Movie,
    MovieSource,
    ProviderRow,
    SyncHistory,
    SyncItem,
)
from av_library.parsing.codes import CodeParser
from av_library.providers.contracts import MovieMetadata, MovieProvider, ProviderActress
from av_library.services.actresses import ValidationError
from av_library.services.matching import MatchService

logger = logging.getLogger("av_library.metadata")


class SyncError(RuntimeError):
    pass


class SyncCancelled(Exception):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class SyncProgress:
    pages: int
    items: int


class MetadataService:
    def __init__(self, database: Database):
        self.database = database

    def bind(self, actress_id: int, provider: MovieProvider, candidate: ProviderActress) -> None:
        if candidate.provider_id != provider.id or not candidate.external_id.strip():
            raise ValidationError("来源身份与当前数据源不一致。")
        try:
            with self.database.sessions.begin() as session:
                if session.get(Actress, actress_id) is None:
                    raise ValidationError("女优记录不存在。")
                other = session.scalar(
                    select(ActressSource).where(
                        ActressSource.provider_id == provider.id,
                        ActressSource.external_id == candidate.external_id,
                        ActressSource.actress_id != actress_id,
                    )
                )
                if other:
                    raise ValidationError("该来源身份已绑定到另一位女优。")
                self._provider_row(session, provider)
                binding = session.get(ActressSource, (actress_id, provider.id))
                if binding is None:
                    binding = ActressSource(
                        actress_id=actress_id, provider_id=provider.id, confirmed_at=now_utc()
                    )
                    session.add(binding)
                binding.external_id = candidate.external_id
        except IntegrityError as error:
            raise ValidationError("来源身份已被使用，请检查女优绑定。") from error

    def bindings(self, actress_id: int) -> list[tuple[ActressSource, ProviderRow]]:
        with self.database.sessions() as session:
            return list(
                session.execute(
                    select(ActressSource, ProviderRow)
                    .join(ProviderRow, ProviderRow.id == ActressSource.provider_id)
                    .where(ActressSource.actress_id == actress_id)
                )
            )

    def movies(self, actress_id: int) -> list[Movie]:
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(Movie)
                    .join(ActressMovie)
                    .where(ActressMovie.actress_id == actress_id)
                    .order_by(Movie.release_date.desc().nulls_last(), Movie.code)
                )
            )

    def latest(self, actress_id: int) -> SyncHistory | None:
        with self.database.sessions() as session:
            return session.scalar(
                select(SyncHistory)
                .where(SyncHistory.actress_id == actress_id)
                .order_by(SyncHistory.id.desc())
                .limit(1)
            )

    def counts(self) -> dict[int, int]:
        with self.database.sessions() as session:
            return {
                actress_id: count
                for actress_id, count in session.execute(
                    select(ActressMovie.actress_id, func.count(ActressMovie.movie_id)).group_by(
                        ActressMovie.actress_id
                    )
                )
            }

    def _provider_row(self, session, provider: MovieProvider):
        row = session.get(ProviderRow, provider.id)
        if row is None:
            row = ProviderRow(id=provider.id)
            session.add(row)
        row.name = provider.display_name
        row.coverage_description = provider.coverage_description

    def sync(
        self,
        actress_id: int,
        provider: MovieProvider,
        cancel: Event | None = None,
        progress: Callable[[SyncProgress], None] = lambda _progress: None,
    ) -> SyncHistory:
        cancel = cancel or Event()
        with self.database.sessions.begin() as session:
            actress = session.get(Actress, actress_id)
            binding = session.get(ActressSource, (actress_id, provider.id))
            if actress is None or binding is None:
                raise ValidationError("请先确认并绑定该来源的女优身份。")
            external_id = binding.external_id
            self._provider_row(session, provider)
            history = SyncHistory(
                actress_id=actress_id,
                provider_id=provider.id,
                started_at=now_utc(),
                status="running",
                is_baseline=actress.last_synced_at is None,
                coverage_complete=False,
                pages_received=0,
                items_received=0,
                added_count=0,
                updated_count=0,
            )
            session.add(history)
            session.flush()
            history_id = history.id

        items: list[MovieMetadata] = []
        pages = 0
        cursor = None
        visited = set()
        try:
            while True:
                if cancel.is_set():
                    raise SyncCancelled()
                if cursor in visited or pages >= 500:
                    raise SyncError("来源分页重复或超过上限，未应用本次数据。")
                visited.add(cursor)
                page = provider.list_movies(external_id, cursor)
                pages += 1
                items.extend(page.items)
                progress(SyncProgress(pages, len(items)))
                if page.warnings:
                    raise SyncError("来源部分作品无法识别：" + "；".join(page.warnings[:3]))
                if page.next_cursor is None:
                    if not page.complete:
                        raise SyncError("来源未确认分页完整，未应用本次数据。")
                    if page.total_hint is not None and len(items) != page.total_hint:
                        raise SyncError("来源报告的总数与返回条数不一致，未应用本次数据。")
                    break
                cursor = page.next_cursor
            if cancel.is_set():
                raise SyncCancelled()
            normalized: list[tuple[MovieMetadata, str]] = []
            parser = CodeParser()
            for item in items:
                if item.provider_id != provider.id or not item.external_id:
                    raise SyncError("来源返回了无效的作品身份。")
                try:
                    code = parser.normalize_manual(item.code)
                except ValueError as error:
                    raise SyncError(f"无法识别作品番号：{item.code[:40]}") from error
                normalized.append((item, code))
            with self.database.sessions.begin() as session:
                history = session.get(SyncHistory, history_id)
                actress = session.get(Actress, actress_id)
                binding = session.get(ActressSource, (actress_id, provider.id))
                if actress is None or binding is None or binding.external_id != external_id:
                    raise SyncError("同步期间女优来源身份已修改，未应用结果。")
                seen_source: dict[str, str] = {}
                for item, code in normalized:
                    earlier = seen_source.get(item.external_id)
                    if earlier and earlier != code:
                        raise SyncError("来源同一作品 ID 对应不同番号，未应用结果。")
                    seen_source[item.external_id] = code
                finished = now_utc()
                for item, code in normalized:
                    if cancel.is_set():
                        raise SyncCancelled()
                    source = session.scalar(
                        select(MovieSource).where(
                            MovieSource.provider_id == provider.id,
                            MovieSource.external_id == item.external_id,
                        )
                    )
                    if source is not None:
                        movie = session.get(Movie, source.movie_id)
                        if movie.code != code:
                            raise SyncError("来源作品 ID 对应的番号已变化，请人工检查。")
                    else:
                        movie = session.scalar(
                            select(Movie).where(
                                Movie.namespace == "jp_standard", Movie.code == code
                            )
                        )
                        if movie is None:
                            movie = Movie(
                                namespace="jp_standard",
                                code=code,
                                is_compilation=item.is_compilation,
                                is_solo=item.is_solo,
                                first_seen_time=finished,
                                last_update_time=finished,
                            )
                            session.add(movie)
                            session.flush()
                        source = MovieSource(
                            movie_id=movie.id,
                            provider_id=provider.id,
                            external_id=item.external_id,
                            raw_code=item.code,
                            detail_url=item.detail_url,
                            last_seen_time=finished,
                        )
                        session.add(source)
                    updated = self._merge_metadata(movie, item, provider.id)
                    if updated:
                        movie.last_update_time = finished
                        history.updated_count += 1
                    source.last_seen_time = finished
                    source.detail_url = item.detail_url or source.detail_url
                    source.raw_code = item.code
                    relation = session.get(ActressMovie, (actress_id, movie.id))
                    if relation is None:
                        relation = ActressMovie(
                            actress_id=actress_id,
                            movie_id=movie.id,
                            first_seen_time=finished,
                            is_ignored=False,
                        )
                        session.add(relation)
                        history.added_count += 1
                        change = "added"
                    else:
                        change = "updated" if updated else "unchanged"
                    if session.get(SyncItem, (history_id, movie.id)) is None:
                        session.add(
                            SyncItem(sync_id=history_id, movie_id=movie.id, change_type=change)
                        )
                if cancel.is_set():
                    raise SyncCancelled()
                history.status, history.coverage_complete = "success", True
                history.pages_received, history.items_received = pages, len(items)
                history.finished_at = finished
                actress.last_synced_at = finished
                binding.last_success_at = finished
                session.flush()
                MatchService.reconcile_session(session, actress_id)
            logger.info(
                "Metadata sync finished actress_id=%s provider=%s items=%s added=%s",
                actress_id,
                provider.id,
                len(items),
                history.added_count,
            )
            return history
        except SyncCancelled:
            return self._finish_failure(history_id, "cancelled", pages, len(items), None)
        except Exception as error:  # noqa: BLE001 -- sync boundary must record all failures
            logger.error(
                "Metadata sync failed actress_id=%s provider=%s error=%s",
                actress_id,
                provider.id,
                type(error).__name__,
            )
            # Do not include provider exception URLs: API credentials may be query parameters.
            safe = (
                str(error)
                if isinstance(error, SyncError)
                else "来源请求或数据处理失败；请检查网络、来源配置与输入文件。"
            )
            self._finish_failure(
                history_id, "partial" if pages else "failed", pages, len(items), safe
            )
            raise SyncError(safe) from None

    @staticmethod
    def _merge_metadata(movie: Movie, item: MovieMetadata, provider_id: str) -> bool:
        changed = False
        provenance = dict(movie.field_provenance or {})
        for field in (
            "title",
            "japanese_title",
            "release_date",
            "manufacturer",
            "publisher",
            "series",
            "cover_url",
            "detail_url",
        ):
            current = getattr(movie, field)
            proposed = getattr(item, field)
            if proposed and (not current or provenance.get(field) == provider_id):
                if current == proposed:
                    continue
                setattr(movie, field, proposed)
                provenance[field] = provider_id
                changed = True
        if item.is_compilation and not movie.is_compilation:
            movie.is_compilation = True
            provenance["is_compilation"] = provider_id
            changed = True
        if not item.is_solo and movie.is_solo:
            movie.is_solo = False
            provenance["is_solo"] = provider_id
            changed = True
        if changed:
            movie.field_provenance = provenance
        return changed

    def _finish_failure(
        self, history_id: int, status: str, pages: int, count: int, error: str | None
    ) -> SyncHistory:
        with self.database.sessions.begin() as session:
            history = session.get(SyncHistory, history_id)
            history.status = status
            history.finished_at = now_utc()
            history.pages_received, history.items_received = pages, count
            history.error_summary = error
        return history
