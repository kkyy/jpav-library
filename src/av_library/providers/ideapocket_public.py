"""Read the public IdeaPocket actress and work catalog.

IdeaPocket exposes an HTML catalogue without requiring an account or API key.  The
provider checks the credited actresses on every work page before returning a record.
"""

import html
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http.client import IncompleteRead
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from av_library.parsing.codes import CodeParser
from av_library.providers.contracts import (
    MovieMetadata,
    MoviePage,
    ProviderActress,
    infer_compilation_title,
)


class IdeaPocketProviderError(RuntimeError):
    """Raised when the public IdeaPocket page cannot be trusted."""


_PROFILE = re.compile(r"^/actress/detail/(?P<id>\d+)/?$")
_COUNT = re.compile(r"全\s*(\d+)\s*作品中\s*(\d+)\s*〜\s*(\d+)")
_PROFILE_CARD = re.compile(
    r'<a class="item" href="https://ideapocket\.com/works/detail/'
    r'(?P<id>[A-Za-z0-9]+)[^"]*"(?P<body>.*?)</a>',
    re.S,
)
_COVER = re.compile(r'<img[^>]+data-src="([^"]+)"', re.S)
_TITLE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_SEARCH_ACTRESS = re.compile(
    r'<a class="name[^\"]*" href="https://ideapocket\.com/actress/detail/(?P<id>\d+)"'
    r"[^>]*>(?P<name>.*?)</a>",
    re.S,
)
_ACTRESS_LINK = re.compile(
    r'<a[^>]+href="https://ideapocket\.com/actress/detail/(?P<id>\d+)"[^>]*>'
    r"(?P<name>.*?)</a>",
    re.S,
)
_RELEASE = re.compile(
    r'<div class="th">発売日</div>.*?href="https://ideapocket\.com/works/list/date/'
    r"(?P<date>\d{4}-\d{2}-\d{2})",
    re.S,
)
_SERIES = re.compile(
    r'<div class="th">シリーズ</div>.*?href="https://ideapocket\.com/works/list/series/[^\"]+">'
    r"(?P<series>.*?)</a>",
    re.S,
)
_CODE = re.compile(r"^[A-Z]{2,10}\d{2,7}$")
_TAG = re.compile(r"<[^>]+>")


def _plain(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", value)).split())


def _name_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def profile_id_from_url(value: str) -> str:
    parsed = urlparse(value.strip())
    match = _PROFILE.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "ideapocket.com" or not match:
        raise IdeaPocketProviderError(
            "请粘贴 IdeaPocket 官网的 HTTPS 女优资料页，例如 /actress/detail/865509。"
        )
    return match["id"]


def parse_actress_search(source: str, query: str) -> tuple[tuple[str, str], ...]:
    key = _name_key(query)
    found: dict[str, str] = {}
    for match in _SEARCH_ACTRESS.finditer(source):
        name = _plain(match["name"])
        if _name_key(name) == key:
            found[match["id"]] = name
    return tuple(found.items())


def parse_profile(
    source: str, page_number: int = 1
) -> tuple[str, int, tuple[tuple[str, str | None], ...]]:
    heading = _TITLE.search(source)
    count = _COUNT.search(source)
    if not heading or not count:
        raise IdeaPocketProviderError("IdeaPocket 女优页结构已变化，无法确认身份或作品总数。")
    name = _plain(heading[1])
    cards: list[tuple[str, str | None]] = []
    for match in _PROFILE_CARD.finditer(source):
        cover = _COVER.search(match["body"])
        cards.append((match["id"].upper(), html.unescape(cover[1]) if cover else None))
    if not name or not cards or len({code for code, _ in cards}) != len(cards):
        raise IdeaPocketProviderError("IdeaPocket 当前分页缺少作品或出现重复作品。")
    first, last = int(count[2]), int(count[3])
    if first != (page_number - 1) * 12 + 1 or last - first + 1 != len(cards):
        raise IdeaPocketProviderError("IdeaPocket 分页条数与官网显示不一致，未同步数据。")
    return name, int(count[1]), tuple(cards)


def _detail_actresses(source: str) -> tuple[tuple[str, str], ...]:
    marker = '<div class="th">女優</div>'
    start = source.find(marker)
    if start < 0:
        return ()
    end = source.find('<div class="th">', start + len(marker))
    section = source[start:] if end < 0 else source[start:end]
    found: dict[str, str] = {}
    for match in _ACTRESS_LINK.finditer(section):
        found[match["id"]] = _plain(match["name"])
    return tuple(found.items())


def parse_detail(
    source: str,
    raw_code: str,
    cover_url: str | None = None,
    actress_external_id: str | None = None,
) -> MovieMetadata:
    title = _TITLE.search(source)
    release = _RELEASE.search(source)
    if not title or not release:
        raise IdeaPocketProviderError(f"IdeaPocket 作品 {raw_code} 的标题或发行日期不可用。")
    japanese_title = _plain(title[1])
    if not japanese_title:
        raise IdeaPocketProviderError(f"IdeaPocket 作品 {raw_code} 标题为空。")
    try:
        normalized = CodeParser().normalize_manual(raw_code)
        release_date = date.fromisoformat(release["date"])
    except ValueError as error:
        raise IdeaPocketProviderError(f"IdeaPocket 作品 {raw_code} 的番号或发行日期无效。") from error
    actresses = _detail_actresses(source)
    if not actresses:
        raise IdeaPocketProviderError(f"IdeaPocket 作品 {raw_code} 的出演者名单不可用。")
    actress_ids = tuple(item[0] for item in actresses)
    if actress_external_id and actress_external_id not in actress_ids:
        raise IdeaPocketProviderError(f"IdeaPocket 作品 {raw_code} 未列出当前女优。")
    series_match = _SERIES.search(source)
    series = _plain(series_match["series"]) if series_match else None
    return MovieMetadata(
        provider_id="ideapocket_public",
        external_id=raw_code.upper(),
        code=normalized,
        title=japanese_title,
        japanese_title=japanese_title,
        release_date=release_date,
        manufacturer="IDEAPOCKET",
        publisher="IDEAPOCKET",
        series=series,
        cover_url=cover_url,
        detail_url=f"https://ideapocket.com/works/detail/{raw_code.upper()}",
        actress_external_ids=actress_ids,
        is_compilation=infer_compilation_title(japanese_title),
        is_solo=len(actress_ids) == 1,
    )


class IdeaPocketPublicProvider:
    id = "ideapocket_public"
    display_name = "IdeaPocket 官方公开目录"
    coverage_description = "仅 IdeaPocket 官网该女优资料页所列作品，包含合集；不代表跨厂牌全部作品"
    base_url = "https://ideapocket.com"

    def _get(self, path: str, missing_ok: bool = False) -> str | None:
        request = Request(
            self.base_url + path,
            headers={"User-Agent": "JPAVLibrary/0.7 (+public metadata; personal catalog)"},
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=20) as response:
                    payload = response.read(3_000_001)
                    if len(payload) > 3_000_000:
                        raise IdeaPocketProviderError("IdeaPocket 页面超过预期大小。")
                    return payload.decode("utf-8")
            except HTTPError as error:
                if missing_ok and error.code == 404:
                    return None
                raise IdeaPocketProviderError(
                    "无法读取 IdeaPocket 官网公开元数据，请稍后重试。"
                ) from error
            except (IncompleteRead, URLError, TimeoutError, OSError, UnicodeError) as error:
                if attempt < 2:
                    sleep(0.4 * (attempt + 1))
                    continue
                raise IdeaPocketProviderError(
                    "无法读取 IdeaPocket 官网公开元数据，请稍后重试。"
                ) from error
        raise IdeaPocketProviderError("无法读取 IdeaPocket 官网公开元数据，请稍后重试。")

    def search_actresses(self, query: str) -> tuple[ProviderActress, ...]:
        query = query.strip()
        if not query or len(query) > 200:
            raise IdeaPocketProviderError("请输入女优日文名或 IdeaPocket 官网资料页地址。")
        if "://" in query:
            actress_id = profile_id_from_url(query)
            name, _, _ = parse_profile(self._get(f"/actress/detail/{actress_id}"))
            return (ProviderActress(self.id, actress_id, name, japanese_name=name),)
        source = self._get(f"/search/list?keyword={quote(query, safe='')}")
        return tuple(
            ProviderActress(self.id, actress_id, name, japanese_name=name)
            for actress_id, name in parse_actress_search(source, query)
        )

    def list_movies(self, actress_external_id: str, cursor: str | None = None) -> MoviePage:
        if not actress_external_id.isdecimal():
            raise IdeaPocketProviderError("IdeaPocket 女优 ID 无效。")
        page_number = int(cursor or "1")
        if page_number < 1 or page_number > 500:
            raise IdeaPocketProviderError("IdeaPocket 分页超过支持范围。")
        suffix = f"?page={page_number}" if page_number > 1 else ""
        _, total, cards = parse_profile(
            self._get(f"/actress/detail/{actress_external_id}{suffix}"), page_number
        )

        def fetch_card(card: tuple[str, str | None]) -> MovieMetadata:
            raw_code, cover = card
            return parse_detail(
                self._get(f"/works/detail/{raw_code}"), raw_code, cover, actress_external_id
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            items = tuple(pool.map(fetch_card, cards))
        first = (page_number - 1) * 12 + 1
        end = first + len(items) - 1
        if end > total or (end < total and len(items) != 12):
            raise IdeaPocketProviderError("IdeaPocket 分页条数与官网总数不一致，未同步数据。")
        return MoviePage(
            items,
            str(page_number + 1) if end < total else None,
            complete=end == total,
            total_hint=total,
        )

    def get_movie(self, external_id: str) -> MovieMetadata:
        raw_code = external_id.strip().upper()
        if not _CODE.fullmatch(raw_code):
            raise IdeaPocketProviderError("IdeaPocket 作品 ID 无效。")
        return parse_detail(self._get(f"/works/detail/{raw_code}"), raw_code)
