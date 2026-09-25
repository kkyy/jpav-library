import re

import pytest

from av_library.parsing.codes import DEFAULT_RULES, CodeParser, CodeRule


@pytest.mark.parametrize(
    "filename,code",
    [
        ("MIDV-123.mp4", "MIDV-123"),
        ("MIDV123.mp4", "MIDV-123"),
        ("MIDV_123.mp4", "MIDV-123"),
        ("[midv-123] xxx.mp4", "MIDV-123"),
        ("翼舞 MIDV-123 1080p.mp4", "MIDV-123"),
        ("ABC 123.mkv", "ABC-123"),
        ("ＭＩＤＶ－１２３.MP4", "MIDV-123"),
        ("MIDV–123.webm", "MIDV-123"),
        ("ABC-001.mp4", "ABC-001"),
        ("ABC-0001.mp4", "ABC-0001"),
        ("MIDV-123-4K.mp4", "MIDV-123"),
        ("MIDV-123-sub.mp4", "MIDV-123"),
        ("FC2-PPV-1234567.mp4", "FC2-PPV-1234567"),
        ("MIDV-123 [MIDV123].avi", "MIDV-123"),
    ],
)
def test_formats(filename, code):
    result = CodeParser().parse_filename(filename)
    assert result.status == "recognized"
    assert result.code == code


@pytest.mark.parametrize(
    "filename",
    ["movie-h264-1080p.mp4", "h265_x265_4K.mkv", "无番号.mp4", "1080p.mp4", "UTF-16.mp4"],
)
def test_technical_tags_are_not_codes(filename):
    assert CodeParser().parse_filename(filename).status == "unrecognized"


def test_ambiguity_and_extension_rules():
    parsed = CodeParser().parse_filename("MIDV-123 MIDV-124.mp4")
    assert parsed.status == "ambiguous" and parsed.code is None
    assert parsed.candidates == ("MIDV-123", "MIDV-124")
    special = CodeRule(
        "numeric_prefix",
        re.compile(r"(?<![A-Z0-9])1PON-(\d{6})(?![A-Z0-9])"),
        lambda m: f"1PON-{m[1]}",
        200,
    )
    result = CodeParser((special, *DEFAULT_RULES)).parse_filename("1PON-123456.mp4")
    assert result.code == "1PON-123456" and result.rule_id == "numeric_prefix"


def test_manual_must_be_single_code():
    parser = CodeParser()
    assert parser.normalize_manual("midv_123") == "MIDV-123"
    with pytest.raises(ValueError):
        parser.normalize_manual("MIDV-123 MIDV-124")
