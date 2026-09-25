import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath
from re import Pattern


@dataclass(frozen=True)
class CodeRule:
    id: str
    pattern: Pattern[str]
    normalize: Callable[[re.Match[str]], str]
    priority: int = 0


@dataclass(frozen=True)
class ParseResult:
    status: str
    code: str | None
    candidates: tuple[str, ...]
    rule_id: str | None


EXCLUDED_PREFIXES = {
    "H",
    "X",
    "HDD",
    "HEVC",
    "AV",
    "MP",
    "AAC",
    "DTS",
    "WMV",
    "WEBM",
    "UTF",
    "FPS",
    "BIT",
    "WIN",
    "WINDOWS",
    "SHA",
    "MD",
}
DEFAULT_RULES = (
    CodeRule(
        "fc2_ppv",
        re.compile(r"(?<![A-Z0-9])FC2[-_ ]?PPV[-_ ]?(\d{5,10})(?![A-Z0-9])"),
        lambda m: f"FC2-PPV-{m[1]}",
        100,
    ),
    CodeRule(
        "standard",
        re.compile(r"(?<![A-Z0-9])(?P<prefix>[A-Z]{2,10})[-_\s]?(?P<number>\d{2,7})(?![A-Z0-9])"),
        lambda m: f"{m['prefix']}-{m['number']}",
        0,
    ),
)


class CodeParser:
    def __init__(self, rules: tuple[CodeRule, ...] = DEFAULT_RULES):
        self.rules = sorted(rules, key=lambda rule: rule.priority, reverse=True)

    def parse_code(self, value: str) -> ParseResult:
        text = unicodedata.normalize("NFKC", value).upper()
        text = text.translate(str.maketrans({c: "-" for c in "‐‑‒–—−"}))
        matches: dict[str, str] = {}
        covered: list[tuple[int, int]] = []
        for rule in self.rules:
            for match in rule.pattern.finditer(text):
                if any(match.start() < end and match.end() > start for start, end in covered):
                    continue
                if rule.id == "standard" and match["prefix"] in EXCLUDED_PREFIXES:
                    continue
                code = rule.normalize(match)
                matches[code] = rule.id
                covered.append(match.span())
        candidates = tuple(matches)
        if len(candidates) == 1:
            return ParseResult("recognized", candidates[0], candidates, matches[candidates[0]])
        return ParseResult("ambiguous" if candidates else "unrecognized", None, candidates, None)

    def parse_filename(self, filename: str) -> ParseResult:
        # Parent directory names are deliberately never used as evidence.
        return self.parse_code(PurePath(filename).stem)

    def normalize_manual(self, value: str) -> str:
        # Manual correction must be a single complete code, not an arbitrary filename.
        text = unicodedata.normalize("NFKC", value.strip()).upper()
        text = text.translate(str.maketrans({c: "-" for c in "‐‑‒–—−"}))
        for rule in self.rules:
            match = rule.pattern.fullmatch(text)
            if match and not (rule.id == "standard" and match["prefix"] in EXCLUDED_PREFIXES):
                return rule.normalize(match)
        raise ValueError("请输入单个完整番号，例如 MIDV-123；暂不支持的格式可新增解析规则。")
