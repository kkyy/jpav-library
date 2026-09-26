"""Small offline alias seed used when a user enters a Chinese stage name."""

import unicodedata


def name_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


# This is intentionally additive: users can still enter provider-specific names in
# the actress aliases field, and new mappings can be added without changing sync code.
KNOWN_ACTRESS_ALIASES: dict[str, tuple[str, ...]] = {
    name_key("翼舞"): ("つばさ舞",),
    name_key("河北彩花"): ("河北彩花", "河北彩伽"),
    name_key("河北彩伽"): ("河北彩花", "河北彩伽"),
    name_key("石川澪"): ("石川澪",),
    name_key("七沢みあ"): ("七沢みあ",),
    name_key("七泽米亚"): ("七沢みあ",),
}


def search_names(*values: str, aliases: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return deduplicated provider search terms, including known aliases."""
    result: list[str] = []
    seen: set[str] = set()
    raw = (*values, *aliases)
    for value in raw:
        value = value.strip()
        if value and name_key(value) not in seen:
            result.append(value)
            seen.add(name_key(value))
        for alias in KNOWN_ACTRESS_ALIASES.get(name_key(value), ()):
            if name_key(alias) not in seen:
                result.append(alias)
                seen.add(name_key(alias))
    return tuple(result)
