import os
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from av_library.db.database import Database
from av_library.db.models import Actress


class ValidationError(ValueError):
    """Actionable input error safe to display in the UI."""


@dataclass(frozen=True)
class ActressInput:
    name: str
    folder_path: str
    japanese_name: str = ""
    aliases: tuple[str, ...] = ()
    avatar_path: str | None = None


def name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_folder(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value or "\x00" in value:
        raise ValidationError("请选择或输入本地文件夹。")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValidationError("请使用绝对路径，例如 D:\\AV\\翼舞。")
    if os.name == "nt" and (any(char in value for char in '<>"|?*') or ":" in value[2:]):
        raise ValidationError("文件夹路径包含无效字符。")
    # Do not resolve symlinks or access the drive: disconnected disks can be saved.
    normalized = os.path.normpath(str(path))
    return normalized, os.path.normcase(normalized)


class ActressService:
    def __init__(self, database: Database):
        self.database = database

    def list(self, query: str = "") -> list[Actress]:
        with self.database.sessions() as session:
            rows = list(session.scalars(select(Actress).order_by(Actress.updated_at.desc())))
        key = name_key(query)
        return [
            row
            for row in rows
            if not key
            or any(key in name_key(value) for value in (row.name, row.japanese_name, *row.aliases))
        ]

    def get(self, actress_id: int) -> Actress:
        with self.database.sessions() as session:
            row = session.get(Actress, actress_id)
            if row is None:
                raise ValidationError("该女优记录已不存在，请刷新列表。")
            return row

    def set_japanese_name(self, actress_id: int, japanese_name: str) -> Actress:
        japanese_name = japanese_name.strip()
        if not japanese_name or len(japanese_name) > 100 or "\x00" in japanese_name:
            raise ValidationError("自动识别到的日文名无效。")
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.database.sessions.begin() as session:
            row = session.get(Actress, actress_id)
            if row is None:
                raise ValidationError("该女优记录已不存在，请刷新列表。")
            row.japanese_name = japanese_name
            row.updated_at = now
            session.add(row)
            session.flush()
        return row

    def save(self, data: ActressInput, actress_id: int | None = None) -> Actress:
        name = data.name.strip()
        if not name or len(name) > 100 or "\x00" in name:
            raise ValidationError("常用名不能为空，且不能超过 100 个字符。")
        folder, folder_key = normalize_folder(data.folder_path)
        alias_map = {name_key(alias): alias.strip() for alias in data.aliases if alias.strip()}
        alias_map.pop(name_key(name), None)
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            with self.database.sessions.begin() as session:
                row = (
                    session.get(Actress, actress_id)
                    if actress_id is not None
                    else Actress(created_at=now)
                )
                if row is None:
                    raise ValidationError("该女优记录已不存在，请刷新列表。")
                row.name = name
                row.name_key = name_key(name)
                row.japanese_name = data.japanese_name.strip()
                row.aliases = list(alias_map.values())
                if actress_id is not None and row.folder_key != folder_key:
                    row.last_scanned_at = None
                row.folder_path = folder
                row.folder_key = folder_key
                row.avatar_path = data.avatar_path or None
                row.updated_at = now
                session.add(row)
                session.flush()
            return row
        except IntegrityError as error:
            raise ValidationError("该常用名或本地目录已被使用，请编辑已有记录。") from error

    def delete(self, actress_id: int) -> None:
        # Removes the database record only; never invokes filesystem deletion.
        with self.database.sessions.begin() as session:
            row = session.get(Actress, actress_id)
            if row is None:
                raise ValidationError("该女优记录已不存在。")
            session.delete(row)
