import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

_COMPILATION_MARKERS = (
    "合集",
    "精选集",
    "精选",
    "总集",
    "ベスト",
    "BEST",
    "総集編",
    "コンプリート",
    "COLLECTION",
    "福袋",
    "大集合",
    "厳選",
    "傑作",
    "ランキング",
    "年度版",
    "映像集",
    "作品史",
    "全史",
    "永久保存版",
    "詰め合わせ",
)


def infer_compilation_title(title: str) -> bool:
    """Conservative title-only classification; users can still manually ignore any row."""
    upper = title.upper()
    return any(marker in upper for marker in _COMPILATION_MARKERS) or bool(
        re.search(
            r"(?:\d+\s*(?:時間|本|作品|名|タイトル|コーナー|連発|選)|"
            r"(?:女優|美女)[^。！？]{0,20}\d+名)[^。！？]{0,30}"
            r"(?:収録|まとめ|一挙|ランキング|作品|女優|美女)",
            title,
        ) or bool(re.search(r"\d+\s*時間", title))
    )


@dataclass(frozen=True)
class ProviderActress:
    provider_id: str
    external_id: str
    name: str
    japanese_name: str = ""
    aliases: tuple[str, ...] = ()
    avatar_url: str | None = None


@dataclass(frozen=True)
class MovieMetadata:
    provider_id: str
    external_id: str
    code: str
    title: str
    japanese_title: str = ""
    release_date: date | None = None
    manufacturer: str | None = None
    publisher: str | None = None
    series: str | None = None
    cover_url: str | None = None
    detail_url: str | None = None
    actress_external_ids: tuple[str, ...] = ()
    is_compilation: bool = False
    is_solo: bool = True


@dataclass(frozen=True)
class MoviePage:
    items: tuple[MovieMetadata, ...]
    next_cursor: str | None
    # Complete within this provider's declared scope, never a global guarantee.
    complete: bool
    total_hint: int | None = None
    warnings: tuple[str, ...] = ()


class MovieProvider(Protocol):
    id: str
    display_name: str
    coverage_description: str

    def search_actresses(self, query: str) -> tuple[ProviderActress, ...]: ...

    def list_movies(self, actress_external_id: str, cursor: str | None = None) -> MoviePage: ...

    def get_movie(self, external_id: str) -> MovieMetadata: ...
