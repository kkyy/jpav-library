"""Read the public S1 actress catalog and its product metadata pages."""

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


class S1ProviderError(RuntimeError):
    pass


_PROFILE = re.compile(r"^/actress/detail/(?P<id>\d+)/?$")
_COUNT = re.compile(r"全\s*(\d+)\s*作品中")
_CARD = re.compile(
    r'<a class="item" href="https://s1s1s1\.com/works/detail/(?P<id>[A-Za-z0-9]+)'
    r'[^\"]*">(?P<body>.*?)</a>',
    re.S,
)
_COVER = re.compile(r'<img[^>]+data-src="(https://[^\"]+)"')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_DATE = re.compile(r"/works/list/date/(\d{4}-\d{2}-\d{2})")
_TAG = re.compile(r"<[^>]+>")
_ACTRESS_LINK = re.compile(r"/actress/detail/(\d+)")
_SEARCH_ACTRESS = re.compile(
    r'<a class="name[^\"]*" href="https://s1s1s1\.com/actress/detail/(?P<id>\d+)"'
    r"[^>]*>(?P<name>.*?)</a>",
    re.S,
)
_CODE = re.compile(r"^(?P<prefix>[A-Z]{2,10})-?(?P<number>\d{2,7})$")


def profile_id_from_url(value: str) -> str:
    parsed = urlparse(value.strip())
    match = _PROFILE.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "s1s1s1.com" or not match:
        raise S1ProviderError("请粘贴 S1 官网的 HTTPS 女优资料页，例如 /actress/detail/813682。")
    return match["id"]


def _plain(value: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", value)).split())


def _name_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def parse_actress_search(source: str, query: str) -> tuple[tuple[str, str], ...]:
    key = _name_key(query)
    found: dict[str, str] = {}
    for match in _SEARCH_ACTRESS.finditer(source):
        name = _plain(match["name"])
        if _name_key(name) == key:
            found[match["id"]] = name
    return tuple(found.items())


def is_compilation_title(title: str) -> bool:
    return infer_compilation_title(title)


def parse_profile(source: str) -> tuple[str, int, tuple[tuple[str, str | None], ...]]:
    heading = re.search(r"<h2[^>]*>(.*?)</h2>", source, re.S)
    count = _COUNT.search(source)
    if not heading or not count:
        raise S1ProviderError("S1 女优页结构已变化，无法确认身份或作品总数。")
    name = _plain(heading[1])
    cards = []
    for match in _CARD.finditer(source):
        cover = _COVER.search(match["body"])
        cards.append((match["id"].upper(), html.unescape(cover[1]) if cover else None))
    if not name or not cards or len({code for code, _ in cards}) != len(cards):
        raise S1ProviderError("S1 当前分页缺少作品或出现重复作品。")
    return name, int(count[1]), tuple(cards)


def _detail_actress_ids(source: str) -> tuple[str, ...]:
    marker = '<div class="th">女優</div>'
    start = source.find(marker)
    if start < 0:
        return ()
    end = source.find('<div class="th">', start + len(marker))
    section = source[start:] if end < 0 else source[start:end]
    return tuple(dict.fromkeys(_ACTRESS_LINK.findall(section)))


def parse_detail(
    source: str,
    raw_code: str,
    cover_url: str | None = None,
    actress_external_id: str | None = None,
) -> MovieMetadata:
    title = _TITLE.search(source)
    release = _DATE.search(source)
    if not title or not release:
        raise S1ProviderError(f"S1 作品 {raw_code} 的标题或发行日期不可用。")
    japanese_title = _plain(title[1].split(" | ", 1)[0])
    if not japanese_title:
        raise S1ProviderError(f"S1 作品 {raw_code} 标题为空。")
    try:
        normalized = CodeParser().normalize_manual(raw_code)
        release_date = date.fromisoformat(release[1])
    except ValueError as error:
        raise S1ProviderError(f"S1 作品 {raw_code} 的番号或发行日期无效。") from error
    actress_ids = _detail_actress_ids(source)
    if not actress_ids:
        raise S1ProviderError(f"S1 作品 {raw_code} 的出演者名单不可用，无法判断是否单人作品。")
    if actress_external_id and actress_external_id not in actress_ids:
        raise S1ProviderError(f"S1 作品 {raw_code} 未列出当前女优，无法确认作品归属。")
    return MovieMetadata(
        provider_id="s1_public",
        external_id=raw_code,
        code=normalized,
        title=japanese_title,
        japanese_title=japanese_title,
        release_date=release_date,
        manufacturer="S1 NO.1 STYLE",
        cover_url=cover_url,
        detail_url=f"https://s1s1s1.com/works/detail/{raw_code}",
        is_compilation=is_compilation_title(japanese_title),
        actress_external_ids=actress_ids,
        is_solo=len(actress_ids) == 1,
    )


class S1PublicProvider:
    id = "s1_public"
    display_name = "S1 官方公开目录"
    coverage_description = "仅 S1 官网该女优资料页所列作品，包含合集；不代表跨厂牌全部作品"
    base_url = "https://s1s1s1.com"

    def __init__(self, adjacent_radius: int = 0):
        if adjacent_radius < 0 or adjacent_radius > 10:
            raise ValueError("邻近番号范围必须在 0 到 10 之间。")
        self.adjacent_radius = adjacent_radius
        self._known_codes: set[str] = set()

    def _get(self, path: str, missing_ok: bool = False) -> str | None:
        request = Request(
            self.base_url + path,
            headers={"User-Agent": "JPAVLibrary/0.6 (+public metadata; personal catalog)"},
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=20) as response:
                    payload = response.read(3_000_001)
                    if len(payload) > 3_000_000:
                        raise S1ProviderError("S1 页面超过预期大小。")
                    return payload.decode("utf-8")
            except HTTPError as error:
                if missing_ok and error.code == 404:
                    return None
                raise S1ProviderError("无法读取 S1 官网公开元数据，请稍后重试。") from error
            except (IncompleteRead, URLError, TimeoutError, OSError, UnicodeError) as error:
                if attempt < 2:
                    sleep(0.4 * (attempt + 1))
                    continue
                raise S1ProviderError("无法读取 S1 官网公开元数据，请稍后重试。") from error
        raise S1ProviderError("无法读取 S1 官网公开元数据，请稍后重试。")

    def search_actresses(self, query: str) -> tuple[ProviderActress, ...]:
        query = query.strip()
        if not query or len(query) > 200:
            raise S1ProviderError("请输入女优日文名或 S1 官网资料页地址。")
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
            raise S1ProviderError("S1 女优 ID 无效。")
        page_number = int(cursor or "1")
        if page_number < 1 or page_number > 500:
            raise S1ProviderError("S1 分页超过支持范围。")
        suffix = f"?page={page_number}" if page_number > 1 else ""
        _, total, cards = parse_profile(self._get(f"/actress/detail/{actress_external_id}{suffix}"))
        self._known_codes.update(raw_code for raw_code, _ in cards)

        def fetch_card(card: tuple[str, str | None]) -> MovieMetadata:
            raw_code, cover = card
            return parse_detail(
                self._get(f"/works/detail/{raw_code}"), raw_code, cover, actress_external_id
            )

        # At most three public detail requests at once; every page is checked before it is applied.
        with ThreadPoolExecutor(max_workers=3) as pool:
            items = tuple(pool.map(fetch_card, cards))
        end = (page_number - 1) * 12 + len(items)
        base_total = total
        if end > base_total or (end < base_total and len(items) != 12):
            raise S1ProviderError("S1 分页条数与官网总数不一致，未同步数据。")
        if end == base_total and self.adjacent_radius:
            extras = self._nearby_movies(actress_external_id)
            items += extras
            total += len(extras)
        return MoviePage(
            items,
            str(page_number + 1) if end < base_total else None,
            complete=end == base_total,
            total_hint=total,
        )

    def _nearby_movies(self, actress_external_id: str) -> tuple[MovieMetadata, ...]:
        candidates: set[str] = set()
        parser = CodeParser()
        for raw_code in self._known_codes:
            match = _CODE.fullmatch(raw_code.upper())
            if not match:
                continue
            number = int(match["number"])
            width = len(match["number"])
            for offset in range(-self.adjacent_radius, self.adjacent_radius + 1):
                if offset and number + offset > 0:
                    candidates.add(f"{match['prefix']}{number + offset:0{width}d}")
        candidates.difference_update(self._known_codes)

        def fetch(raw_code: str) -> MovieMetadata | None:
            source = self._get(f"/works/detail/{raw_code}", missing_ok=True)
            if not source or f"/actress/detail/{actress_external_id}" not in source:
                return None
            try:
                return parse_detail(source, raw_code, actress_external_id=actress_external_id)
            except S1ProviderError:
                return None

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = tuple(item for item in pool.map(fetch, sorted(candidates)) if item)
        unique: dict[str, MovieMetadata] = {}
        for item in results:
            unique.setdefault(parser.normalize_manual(item.code), item)
        return tuple(unique.values())

    def get_movie(self, external_id: str) -> MovieMetadata:
        if not re.fullmatch(r"[A-Za-z0-9]{4,20}", external_id):
            raise S1ProviderError("S1 作品 ID 无效。")
        raw_code = external_id.upper()
        return parse_detail(self._get(f"/works/detail/{raw_code}"), raw_code)
