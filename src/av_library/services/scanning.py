import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from sqlalchemy import select

from av_library.db.database import Database
from av_library.db.models import Actress, LocalFile, ScanHistory, Setting
from av_library.parsing.codes import CodeParser
from av_library.scanner.files import ScanOptions, path_key, scan_filenames
from av_library.services.actresses import ValidationError
from av_library.services.matching import MatchService

logger = logging.getLogger("av_library.scanning")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ScanService:
    def __init__(self, database: Database):
        self.database = database

    def options(self) -> ScanOptions:
        with self.database.sessions() as session:
            recursive = session.get(Setting, "scan_recursive")
            extensions = session.get(Setting, "video_extensions")
            return ScanOptions(bool(recursive.value), tuple(extensions.value))

    def save_options(self, recursive: bool, extensions_text: str) -> ScanOptions:
        values = tuple(
            dict.fromkeys(
                "." + item.strip().lower().lstrip(".")
                for item in re.split(r"[\s,，;；]+", extensions_text.strip())
                if item.strip()
            )
        )
        if not values or any(not re.fullmatch(r"\.[a-z0-9]{1,10}", item) for item in values):
            raise ValidationError("扩展名格式无效，请输入 mp4, mkv 等，以逗号或空格分隔。")
        with self.database.sessions.begin() as session:
            session.get(Setting, "scan_recursive").value = recursive
            session.get(Setting, "video_extensions").value = list(values)
        return ScanOptions(recursive, values)

    def files(self, actress_id: int) -> list[LocalFile]:
        with self.database.sessions() as session:
            return list(
                session.scalars(
                    select(LocalFile)
                    .where(LocalFile.actress_id == actress_id)
                    .order_by(LocalFile.is_present.desc(), LocalFile.filename, LocalFile.id)
                )
            )

    def latest(self, actress_id: int) -> ScanHistory | None:
        with self.database.sessions() as session:
            return session.scalar(
                select(ScanHistory)
                .where(ScanHistory.actress_id == actress_id)
                .order_by(ScanHistory.id.desc())
                .limit(1)
            )

    def set_manual_code(self, file_id: int, code: str | None):
        try:
            normalized = CodeParser().normalize_manual(code) if code and code.strip() else None
        except ValueError as error:
            raise ValidationError(str(error)) from error
        with self.database.sessions.begin() as session:
            row = session.get(LocalFile, file_id)
            if row is None:
                raise ValidationError("文件记录不存在，请刷新。")
            row.manual_code = normalized
            session.flush()
            MatchService.reconcile_session(session, row.actress_id)

    def run(
        self,
        actress_id: int,
        options: ScanOptions,
        cancel: Event,
        progress: Callable[[int], None] = lambda _count: None,
    ) -> ScanHistory:
        with self.database.sessions.begin() as session:
            actress = session.get(Actress, actress_id)
            if actress is None:
                raise ValidationError("女优记录不存在。")
            root = actress.folder_path
            history = ScanHistory(
                actress_id=actress_id,
                root_path=root,
                recursive=options.recursive,
                extensions=list(options.extensions),
                status="running",
                started_at=utcnow(),
            )
            session.add(history)
            session.flush()
            history_id = history.id
        try:
            snapshot = scan_filenames(root, options, cancel, progress)
            with self.database.sessions.begin() as session:
                history = session.get(ScanHistory, history_id)
                actress = session.get(Actress, actress_id)
                if history is None or actress is None:
                    raise ValidationError("扫描期间女优资料被移除。")
                history.files_seen = len(snapshot.files)
                history.skipped_links = snapshot.skipped_links
                history.finished_at = utcnow()
                if snapshot.cancelled or cancel.is_set():
                    history.status = "cancelled"
                elif snapshot.error_count:
                    history.status = "partial" if snapshot.files else "failed"
                    history.error_summary = f"{snapshot.error_count} 处读取失败。\n" + "\n".join(
                        snapshot.errors
                    )
                elif path_key(actress.folder_path) != path_key(root):
                    history.status = "failed"
                    history.error_summary = "扫描期间目录已修改，结果未应用。"
                else:
                    # Only a fully enumerated scope may replace existing observations.
                    previous = {
                        row.path_key: row
                        for row in session.scalars(
                            select(LocalFile).where(LocalFile.actress_id == actress_id)
                        )
                    }
                    for row in previous.values():
                        in_scope = Path(row.path).suffix.lower() in options.extensions and (
                            options.recursive or path_key(Path(row.path).parent) == path_key(root)
                        )
                        if row.scan_root_key != path_key(root) or in_scope:
                            row.is_present = False
                    for item in snapshot.files:
                        if cancel.is_set():
                            # Throwing here rolls back the entire snapshot, including invalidations.
                            raise ScanCancelled()
                        key = path_key(item.path)
                        row = previous.get(key)
                        if row is None:
                            row = LocalFile(
                                actress_id=actress_id,
                                path_key=key,
                                first_seen_time=history.finished_at,
                            )
                            session.add(row)
                        row.path, row.filename = item.path, item.filename
                        row.scan_root_key = path_key(root)
                        row.normalized_code = item.parsed.code
                        row.candidates = list(item.parsed.candidates)
                        row.parser_rule_id = item.parsed.rule_id
                        row.parse_status = item.parsed.status
                        row.is_present = True
                        row.last_seen_time = history.finished_at
                        row.last_seen_scan_id = history_id
                    if cancel.is_set():
                        raise ScanCancelled()
                    history.status = "success"
                    actress.last_scanned_at = history.finished_at
                    session.flush()
                    MatchService.reconcile_session(session, actress_id)
            logger.info(
                "Scan finished actress_id=%s status=%s files=%s",
                actress_id,
                history.status,
                history.files_seen,
            )
            return history
        except ScanCancelled:
            return self._finish_failure(history_id, "cancelled", None)
        except Exception as error:
            # Record failure at this worker boundary, then let the UI surface it.
            logger.error("Scan failed actress_id=%s error=%s", actress_id, type(error).__name__)
            self._finish_failure(history_id, "failed", str(error))
            raise

    def _finish_failure(self, history_id: int, status: str, error: str | None) -> ScanHistory:
        with self.database.sessions.begin() as session:
            row = session.get(ScanHistory, history_id)
            if row is None:
                raise ValidationError("扫描记录已不存在。")
            row.status, row.error_summary, row.finished_at = status, error, utcnow()
        return row


class ScanCancelled(Exception):
    """Internal signal for atomic cancellation before committing a snapshot."""
