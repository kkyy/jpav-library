"""One local JSON/CSV catalog represents one confirmed actress profile."""

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from av_library.parsing.codes import CodeParser
from av_library.providers.contracts import (
    MovieMetadata,
    MoviePage,
    ProviderActress,
    infer_compilation_title,
)


class CatalogFormatError(ValueError):
    pass


def field(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def boolean_field(row: dict, *names: str) -> bool:
    value = field(row, *names).casefold()
    return value in {"1", "true", "yes", "y", "是", "合集", "精选集"}


def solo_field(row: dict) -> bool:
    multi = field(row, "multi_actress", "多人企划", "多人")
    if multi:
        return not boolean_field(row, "multi_actress", "多人企划", "多人")
    solo = field(row, "is_solo", "solo", "单人")
    return boolean_field(row, "is_solo", "solo", "单人") if solo else True


class FileCatalogProvider:
    def __init__(self, path: str, actress_name: str):
        self.path = Path(path)
        self.id = (
            "file:" + hashlib.sha256(str(self.path.resolve()).casefold().encode()).hexdigest()[:20]
        )
        self.display_name = f"本地导入 · {self.path.name}"
        self.coverage_description = "用户提供的当前文件内容；不保证演员全部发行作品"
        self.actress_name = actress_name
        self._cache: tuple[MovieMetadata, ...] | None = None

    def search_actresses(self, query: str) -> tuple[ProviderActress, ...]:
        return (ProviderActress(self.id, "profile", self.actress_name),)

    def _load(self) -> tuple[MovieMetadata, ...]:
        if self._cache is not None:
            return self._cache
        if self.path.stat().st_size > 50_000_000:
            raise CatalogFormatError("导入文件超过 50 MB，请拆分或精简后再试。")
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
                if self.path.suffix.lower() == ".json":
                    payload = json.load(stream)
                    rows = payload.get("movies") if isinstance(payload, dict) else payload
                elif self.path.suffix.lower() == ".csv":
                    rows = list(csv.DictReader(stream))
                else:
                    raise CatalogFormatError("只支持 UTF-8 的 .json 或 .csv 元数据文件。")
        except (UnicodeError, json.JSONDecodeError, csv.Error) as error:
            raise CatalogFormatError("导入文件无法按 UTF-8 JSON/CSV 读取。") from error
        if not isinstance(rows, list):
            raise CatalogFormatError("JSON 应为作品数组，或包含 movies 数组。")
        parser = CodeParser()
        items = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise CatalogFormatError(f"第 {index} 条作品不是对象。")
            try:
                code = parser.normalize_manual(field(row, "code", "number", "番号"))
                date_text = field(row, "release_date", "date", "发行日期", "発売日")
                release = date.fromisoformat(date_text) if date_text else None
            except ValueError as error:
                raise CatalogFormatError(f"第 {index} 条作品的番号或日期无效：{error}") from error
            title = field(row, "title", "标题", "作品名称")
            items.append(
                MovieMetadata(
                    provider_id=self.id,
                    external_id=field(row, "external_id", "source_id", "来源ID") or code,
                    code=code,
                    title=title,
                    japanese_title=field(row, "japanese_title", "日文标题"),
                    release_date=release,
                    manufacturer=field(row, "manufacturer", "maker", "制作商") or None,
                    publisher=field(row, "publisher", "发行商") or None,
                    series=field(row, "series", "系列") or None,
                    cover_url=field(row, "cover_url", "封面URL") or None,
                    detail_url=field(row, "detail_url", "详情页URL") or None,
                    is_compilation=boolean_field(
                        row, "is_compilation", "compilation", "合集", "精选集"
                    )
                    or infer_compilation_title(title),
                    is_solo=solo_field(row),
                )
            )
        self._cache = tuple(items)
        return self._cache

    def list_movies(self, actress_external_id: str, cursor: str | None = None) -> MoviePage:
        if actress_external_id != "profile":
            raise CatalogFormatError("导入文件的女优身份不匹配。")
        items = self._load()
        start = int(cursor) if cursor else 0
        if start < 0 or start > len(items):
            raise CatalogFormatError("导入页码无效。")
        end = min(start + 100, len(items))
        return MoviePage(
            items[start:end],
            str(end) if end < len(items) else None,
            complete=end == len(items),
            total_hint=len(items),
        )

    def get_movie(self, external_id: str) -> MovieMetadata:
        for item in self._load():
            if item.external_id == external_id:
                return item
        raise CatalogFormatError("导入文件中不存在指定来源作品 ID。")
