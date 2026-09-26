from datetime import date

import pytest

from av_library.providers.ideapocket_public import (
    IdeaPocketProviderError,
    IdeaPocketPublicProvider,
    parse_actress_search,
    parse_detail,
    parse_profile,
    profile_id_from_url,
)


def profile_html(total=2):
    cards = "".join(
        f'<a class="item" href="https://ideapocket.com/works/detail/IPZZ{849 - index}'
        f'?page_from=actress"><div><img data-src="https://cdn.example/{index}.jpg"></div></a>'
        for index in range(total)
    )
    return f'<h2 class="title">篠崎沙帆</h2><p>全{total}作品中 1 〜 {total} タイトルを表示</p>{cards}'


def detail_html(code):
    return (
        f"<h2>FIRST IMPRESSION 192 {code}</h2>"
        '<div class="th">女優</div><div class="td">'
        '<a href="https://ideapocket.com/actress/detail/865509">篠崎沙帆</a>'
        '<div class="th">発売日</div>'
        '<a href="https://ideapocket.com/works/list/date/2026-06-09">2026年6月9日</a>'
        '<div class="th">シリーズ</div>'
        '<a href="https://ideapocket.com/works/list/series/834">FIRST IMPRESSION</a>'
    )


def test_profile_and_detail_parsers_normalize_official_data():
    name, total, cards = parse_profile(profile_html())
    assert name == "篠崎沙帆" and total == 2
    assert cards[0] == ("IPZZ849", "https://cdn.example/0.jpg")
    movie = parse_detail(detail_html("IPZZ849"), "IPZZ849", cards[0][1], "865509")
    assert movie.code == "IPZZ-849"
    assert movie.release_date == date(2026, 6, 9)
    assert movie.cover_url == "https://cdn.example/0.jpg"
    assert movie.series == "FIRST IMPRESSION"
    assert movie.is_solo


def test_search_requires_exact_actress_identity():
    source = (
        '<a class="name c-main-font-hover" href="https://ideapocket.com/actress/detail/865509">篠崎沙帆</a>'
        '<a class="name c-main-font-hover" href="https://ideapocket.com/actress/detail/99">篠崎沙帆子</a>'
    )
    assert parse_actress_search(source, "篠崎沙帆") == (("865509", "篠崎沙帆"),)
    assert parse_actress_search(source, "翼舞") == ()


def test_provider_search_and_pagination(monkeypatch):
    provider = IdeaPocketPublicProvider()
    sources = {
        "/search/list?keyword=%E7%AF%A0%E5%B4%8E%E6%B2%99%E5%B8%86": (
            '<a class="name" href="https://ideapocket.com/actress/detail/865509">篠崎沙帆</a>'
        ),
        "/actress/detail/865509": profile_html(),
    }
    sources.update({f"/works/detail/IPZZ{849 - i}": detail_html(f"IPZZ{849 - i}") for i in range(2)})
    monkeypatch.setattr(provider, "_get", lambda path, missing_ok=False: sources[path])
    assert provider.search_actresses("篠崎沙帆")[0].external_id == "865509"
    page = provider.list_movies("865509")
    assert page.complete and page.total_hint == 2
    assert {item.code for item in page.items} == {"IPZZ-849", "IPZZ-848"}


def test_detail_parser_rejects_unknown_or_unrelated_actress():
    source = detail_html("IPZZ849").replace(
        '<a href="https://ideapocket.com/actress/detail/865509">篠崎沙帆</a>', ""
    )
    with pytest.raises(IdeaPocketProviderError, match="出演者名单不可用"):
        parse_detail(source, "IPZZ849")
    with pytest.raises(IdeaPocketProviderError, match="未列出当前女优"):
        parse_detail(detail_html("IPZZ849"), "IPZZ849", actress_external_id="999")


def test_multi_actress_and_compilation_are_excluded_from_solo_statistics():
    source = detail_html("IPZZ849").replace(
        '<div class="th">発売日</div>',
        '<a href="https://ideapocket.com/actress/detail/99">別の女優</a>'
        '<div class="th">発売日</div>',
    )
    assert not parse_detail(source, "IPZZ849").is_solo
    compilation = detail_html("IPZZ849").replace("FIRST IMPRESSION 192", "BEST COLLECTION")
    assert parse_detail(compilation, "IPZZ849").is_compilation


def test_provider_validates_pagination_and_rejects_repeated_first_page(monkeypatch):
    provider = IdeaPocketPublicProvider()
    first = profile_html(12).replace("全12作品中", "全13作品中")
    last = profile_html(1).replace("全1作品中 1 〜 1", "全13作品中 13 〜 13")
    last = last.replace("IPZZ849", "IPZZ800")
    pages = {"/actress/detail/865509": first, "/actress/detail/865509?page=2": last}

    def fetch(path):
        if path in pages:
            return pages[path]
        return detail_html(path.rsplit("/", 1)[-1])

    monkeypatch.setattr(provider, "_get", fetch)
    page = provider.list_movies("865509")
    assert not page.complete and page.next_cursor == "2"
    final = provider.list_movies("865509", page.next_cursor)
    assert final.complete and final.total_hint == 13
    assert len(page.items + final.items) == 13
    pages["/actress/detail/865509?page=2"] = first
    with pytest.raises(IdeaPocketProviderError, match="分页条数"):
        provider.list_movies("865509", "2")


def test_profile_url_is_strict():
    assert profile_id_from_url("https://ideapocket.com/actress/detail/865509") == "865509"
    with pytest.raises(IdeaPocketProviderError):
        profile_id_from_url("http://ideapocket.com/actress/detail/865509")
