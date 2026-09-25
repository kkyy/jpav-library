"""Export one S1 public actress page to the app's JSON import format."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from av_library.providers.s1_public import S1PublicProvider, profile_id_from_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_url", help="S1 actress profile URL")
    parser.add_argument("output", type=Path, help="Destination UTF-8 JSON path")
    args = parser.parse_args()
    provider = S1PublicProvider()
    actress_id = profile_id_from_url(args.profile_url)
    actress = provider.search_actresses(args.profile_url)[0]
    print(f"S1 profile {actress_id}", flush=True)
    rows = []
    cursor = None
    total = None
    while True:
        page = provider.list_movies(actress_id, cursor)
        rows.extend(page.items)
        total = page.total_hint
        print(f"Read {len(rows)}/{total} works", flush=True)
        if page.next_cursor is None:
            if not page.complete:
                raise RuntimeError("S1 catalog ended without confirming completeness")
            break
        cursor = page.next_cursor
    if len(rows) != total or len({row.code for row in rows}) != len(rows):
        raise RuntimeError("S1 total mismatch or duplicate product code")
    output = {
        "actress": {"name": actress.name, "source_url": args.profile_url},
        "source": "S1 NO.1 STYLE official public actress catalog",
        "scope": "S1 profile only; includes compilations, excludes other studios",
        "retrieved_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_total": total,
        "movies": [
            {
                "external_id": row.external_id,
                "code": row.code,
                "title": row.title,
                "japanese_title": row.japanese_title,
                "release_date": row.release_date.isoformat() if row.release_date else "",
                "manufacturer": row.manufacturer,
                "cover_url": row.cover_url,
                "detail_url": row.detail_url,
                "is_compilation": row.is_compilation,
                "is_solo": row.is_solo,
            }
            for row in rows
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Saved {args.output} ({len(rows)} works)", flush=True)


if __name__ == "__main__":
    main()
