"""Consistent SQLite backup and validated, reversible database restore."""

import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from av_library.db.database import Database
from av_library.db.migrations import MIGRATIONS

logger = logging.getLogger("av_library.backups")


class BackupError(ValueError):
    pass


class BackupService:
    def __init__(self, database: Database, database_path: Path, backup_dir: Path):
        self.database = database
        self.database_path = database_path
        self.backup_dir = backup_dir

    @staticmethod
    def verify(path: Path) -> int:
        if not path.is_file():
            raise BackupError("备份文件不存在。")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("备份文件未通过 SQLite 完整性检查。")
                versions = [
                    version
                    for (version,) in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
        except (sqlite3.DatabaseError, OSError) as error:
            raise BackupError("不是可用的本软件数据库备份。") from error
        expected = [version for version, _statements in MIGRATIONS]
        if not versions or versions != expected[: len(versions)]:
            raise BackupError("备份数据库版本不兼容。")
        return versions[-1]

    def backup(self, target: Path) -> Path:
        if target.resolve() == self.database_path.resolve():
            raise BackupError("不能把备份写到当前数据库文件。")
        if not target.parent.is_dir():
            raise BackupError("备份目标目录不存在。")
        handle, name = tempfile.mkstemp(prefix="backup-", suffix=".sqlite3", dir=target.parent)
        os.close(handle)
        temporary = Path(name)
        try:
            with (
                closing(sqlite3.connect(self.database_path)) as source,
                closing(sqlite3.connect(temporary)) as destination,
            ):
                source.backup(destination)
            self.verify(temporary)
            os.replace(temporary, target)
            logger.info("Database backup completed")
            return target
        except (sqlite3.Error, OSError) as error:
            logger.error("Database backup failed: %s", type(error).__name__)
            raise BackupError("数据库备份失败，请检查目标目录与剩余空间。") from error
        finally:
            temporary.unlink(missing_ok=True)

    def restore(self, source: Path) -> Path:
        if source.resolve() == self.database_path.resolve():
            raise BackupError("请选择外部备份文件，而不是当前数据库。")
        self.verify(source)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        safety = self.backup_dir / f"pre-restore-{stamp}.sqlite3"
        self.backup(safety)
        handle, name = tempfile.mkstemp(
            prefix="restore-", suffix=".sqlite3", dir=self.database_path.parent
        )
        os.close(handle)
        staging = Path(name)
        try:
            with (
                closing(sqlite3.connect(source)) as input_db,
                closing(sqlite3.connect(staging)) as staged_db,
            ):
                input_db.backup(staged_db)
            self.verify(staging)
            self.database.close()
            os.replace(staging, self.database_path)
            try:
                self.database.initialize()
            except Exception:
                # Return to the safety snapshot if an older backup cannot migrate.
                with (
                    closing(sqlite3.connect(safety)) as safety_db,
                    closing(sqlite3.connect(staging)) as recovery_db,
                ):
                    safety_db.backup(recovery_db)
                self.database.close()
                os.replace(staging, self.database_path)
                self.database.initialize()
                raise
            logger.info("Database restore completed; safety backup retained")
            return safety
        except (sqlite3.Error, OSError) as error:
            logger.error("Database restore failed: %s", type(error).__name__)
            raise BackupError("数据库恢复失败；当前库已保留安全备份。") from error
        finally:
            staging.unlink(missing_ok=True)
