from datetime import date

import pytest

from av_library.providers.s1_public import (
    S1ProviderError,
    S1PublicProvider,
    is_compilation_title,
    parse_detail,
    parse_profile,
    profile_id_from_url,
)


def profile_html(total=2):
    cards = "".join(
        f'<a class="item" href="https://s1s1s1.com/works/detail/OFJE{647 - index}'
        f'?page_from=actress"><div><img data-src="https://cdn.example/{index}.jpg"></div></a>'
        for index in range(total)
    )
    return f"<h2>つばさ舞</h2><p>全{total}作品中</p>{cards}"


def detail_html(code):
    return (
        f"<title>作品 {code} | S1</title>"
        f'<div class="th">発売日</div><a href="https://s1s1s1.com/works/list/date/2026-09-08">2026年9月8日</a>'
    )


def test_profile_and_detail_parsers_normalize_official_data():
    name, total, cards = parse_profile(profile_html())
    assert name == "つばさ舞" and total == 2
    assert cards[0] == ("OFJE647", "https://cdn.example/0.jpg")
    movie = parse_detail(detail_html("OFJE647"), "OFJE647", cards[0][1])
    assert movie.code == "OFJE-647"
    assert movie.release_date == date(2026, 9, 8)
    assert movie.cover_url == "https://cdn.example/0.jpg"


def test_compilation_classifier_marks_best_and_collection_titles():
    assert is_compilation_title("S1 2026 BEST COLLECTION")
    assert not is_compilation_title("新人NO.1STYLE つばさ舞AVデビュー")


def test_detail_parser_marks_multi_actress_work_as_non_solo():
    source = (
        detail_html("OFJE647")
        + '<div class="th">女優</div><div class="td">'
        + '<a href="https://s1s1s1.com/actress/detail/813682">つばさ舞</a>'
        + '<a href="https://s1s1s1.com/actress/detail/999">他女优</a>'
        + '<div class="th">発売元</div>'
    )
    movie = parse_detail(source, "OFJE647")
    assert movie.actress_external_ids == ("813682", "999")
    assert not movie.is_solo


def test_profile_url_is_strict_and_profile_count_is_required():
    assert profile_id_from_url("https://s1s1s1.com/actress/detail/813682") == "813682"
    with pytest.raises(S1ProviderError):
        profile_id_from_url("http://s1s1s1.com/actress/detail/813682")
    with pytest.raises(S1ProviderError):
        parse_profile("<h2>つばさ舞</h2>")


def test_provider_paginates_and_does_not_accept_incomplete_page(monkeypatch):
    provider = S1PublicProvider()
    pages = {"1": profile_html(2)}
    details = {"OFJE647": detail_html("OFJE647"), "OFJE646": detail_html("OFJE646")}

    def fake_get(path):
        if path.startswith("/actress/detail/"):
            return pages[path.rsplit("=", 1)[-1] if "=" in path else "1"]
        code = path.rsplit("/", 1)[-1]
        return details.get(code, detail_html(code))

    monkeypatch.setattr(provider, "_get", fake_get)
    page = provider.list_movies("813682")
    assert page.complete and page.next_cursor is None and len(page.items) == 2
    assert page.items[1].code == "OFJE-646"

    pages["1"] = profile_html(3).replace("全3作品中", "全15作品中", 1)
    with pytest.raises(S1ProviderError, match="分页条数"):
        provider.list_movies("813682")


def test_adjacent_mode_keeps_only_pages_that_name_the_actress(monkeypatch):
    provider = S1PublicProvider(adjacent_radius=1)

    def fake_get(path, missing_ok=False):
        if path.startswith("/actress/detail/"):
            return profile_html(1)
        code = path.rsplit("/", 1)[-1]
        if code == "OFJE646":
            return detail_html(code) + "/actress/detail/813682"
        if code == "OFJE648":
            return detail_html(code) + "/actress/detail/999"
        return None if missing_ok else detail_html(code)

    monkeypatch.setattr(provider, "_get", fake_get)
    page = provider.list_movies("813682")
    assert page.complete and page.total_hint == 2
    assert {item.code for item in page.items} == {"OFJE-647", "OFJE-646"}
