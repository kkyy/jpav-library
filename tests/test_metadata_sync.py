import csv
import json
from dataclasses import replace
from datetime import date
from threading import Event

import pytest
from sqlalchemy import func, select, text

from av_library.db.database import Database
from av_library.db.migrations import MIGRATIONS
from av_library.db.models import ActressMovie, Movie, MovieSource, SyncHistory, SyncItem
from av_library.providers.contracts import MovieMetadata, MoviePage, ProviderActress
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.providers.file_import import FileCatalogProvider
from av_library.services.actresses import ActressInput, ActressService, ValidationError
from av_library.services.metadata_sync import MetadataService, SyncError


class FakeProvider:
    def __init__(self, id="fake", items=(), pagesize=2):
        self.id, self.display_name = id, id
        self.coverage_description = "test sample, limited coverage"
        self.items = items
        self.pagesize = pagesize
        self.fail_after = None
        self.incomplete = False

    def search_actresses(self, query):
        return (ProviderActress(self.id, query, query),)

    def list_movies(self, actress_external_id, cursor=None):
        offset = int(cursor or 0)
        if self.fail_after is not None and offset >= self.fail_after:
            raise SyncError("page unavailable")
        end = min(offset + self.pagesize, len(self.items))
        return MoviePage(
            tuple(self.items[offset:end]),
            str(end) if end < len(self.items) else None,
            complete=end == len(self.items) and not self.incomplete,
            total_hint=len(self.items),
        )

    def get_movie(self, external_id):
        return next(item for item in self.items if item.external_id == external_id)


def sample(provider="fake", code="MIDV-123", external_id="x1", title="Title"):
    return MovieMetadata(provider, external_id, code, title, release_date=date(2026, 9, 1))


@pytest.fixture
def catalog(database, tmp_path):
    actresses = ActressService(database)
    first = actresses.save(ActressInput("翼舞", str(tmp_path / "a")))
    second = actresses.save(ActressInput("河北彩花", str(tmp_path / "b")))
    return MetadataService(database), first, second


def test_multi_page_sync_idempotent_updates_only_missing_fields_and_history(catalog, database):
    service, first, _ = catalog
    provider = FakeProvider(items=[sample(), sample(code="MIDV-124", external_id="x2")], pagesize=1)
    service.bind(first.id, provider, provider.search_actresses("maimai")[0])
    first_history = service.sync(first.id, provider)
    assert first_history.status == "success" and first_history.coverage_complete
    assert first_history.is_baseline and first_history.added_count == 2
    assert first_history.pages_received == 2 and first_history.items_received == 2
    provider.items = [replace(provider.items[0], title="Conflicting title", manufacturer="Maker")]
    second_history = service.sync(first.id, provider)
    assert not second_history.is_baseline and second_history.added_count == 0
    assert len(service.movies(first.id)) == 2  # absence from later response never deletes history
    movie = next(row for row in service.movies(first.id) if row.code == "MIDV-123")
    assert movie.title == "Conflicting title" and movie.manufacturer == "Maker"
    assert movie.first_seen_time <= movie.last_update_time
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Movie)) == 2
        assert session.scalar(select(func.count()).select_from(SyncItem)) == 3


def test_cross_provider_and_multi_actress_merge_without_duplicate_movie(catalog, database):
    service, first, second = catalog
    a = FakeProvider(id="a", items=[sample("a", external_id="a1")])
    b = FakeProvider(id="b", items=[sample("b", external_id="b1", title="Other source title")])
    service.bind(first.id, a, ProviderActress("a", "actress-a", "翼舞"))
    service.bind(second.id, b, ProviderActress("b", "actress-b", "河北彩花"))
    service.sync(first.id, a)
    service.sync(second.id, b)
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Movie)) == 1
        assert session.scalar(select(func.count()).select_from(ActressMovie)) == 2
        assert session.scalar(select(func.count()).select_from(MovieSource)) == 2
    assert service.movies(first.id)[0].id == service.movies(second.id)[0].id
    assert service.movies(first.id)[0].title == "Title"


def test_identity_collision_rejected(catalog):
    service, first, second = catalog
    provider = FakeProvider()
    candidate = ProviderActress("fake", "same-id", "翼舞")
    service.bind(first.id, provider, candidate)
    with pytest.raises(ValidationError):
        service.bind(second.id, provider, candidate)
    assert service.bindings(second.id) == []


def test_partial_page_cancel_and_conflict_never_change_catalog(catalog, database):
    service, first, _ = catalog
    provider = FakeProvider(items=[sample(), sample(code="MIDV-124", external_id="x2")], pagesize=1)
    service.bind(first.id, provider, ProviderActress("fake", "a", "翼舞"))
    provider.fail_after = 1
    with pytest.raises(SyncError):
        service.sync(first.id, provider)
    assert service.latest(first.id).status == "partial" and service.movies(first.id) == []
    provider.fail_after = None
    cancelled = Event()
    history = service.sync(first.id, provider, cancelled, lambda _: cancelled.set())
    assert history.status == "cancelled" and service.movies(first.id) == []
    provider.incomplete = True
    with pytest.raises(SyncError, match="分页完整"):
        service.sync(first.id, provider)
    assert service.movies(first.id) == []
    provider.incomplete = False
    service.sync(first.id, provider)
    provider.items = [sample(code="MIDV-999", external_id="x1")]
    with pytest.raises(SyncError, match="番号已变化"):
        service.sync(first.id, provider)
    assert {row.code for row in service.movies(first.id)} == {"MIDV-123", "MIDV-124"}
    with database.sessions() as session:
        statuses = list(session.scalars(select(SyncHistory.status).order_by(SyncHistory.id)))
    assert statuses == ["partial", "cancelled", "partial", "success", "partial"]


def test_duplicate_source_id_with_conflicting_codes_rejected(catalog):
    service, first, _ = catalog
    provider = FakeProvider(items=[sample(), sample(code="MIDV-124")])
    service.bind(first.id, provider, ProviderActress("fake", "a", "翼舞"))
    with pytest.raises(SyncError, match="不同番号"):
        service.sync(first.id, provider)
    assert service.movies(first.id) == []


def test_json_and_csv_import_reimport(catalog, tmp_path):
    service, first, _ = catalog
    json_path = tmp_path / "works.json"
    json_path.write_text(
        json.dumps(
            {
                "movies": [
                    {"code": "midv123", "title": "作品A", "release_date": "2026-09-01"},
                    {"番号": "ABC_001", "标题": "作品B"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    provider = FileCatalogProvider(str(json_path), first.name)
    service.bind(first.id, provider, provider.search_actresses(first.name)[0])
    assert service.sync(first.id, provider).added_count == 2
    assert service.sync(first.id, FileCatalogProvider(str(json_path), first.name)).added_count == 0
    csv_path = tmp_path / "more.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["番号", "标题", "发行日期"])
        writer.writeheader()
        writer.writerow({"番号": "ABC-002", "标题": "作品C", "发行日期": "2026-09-02"})
    csv_provider = FileCatalogProvider(str(csv_path), first.name)
    service.bind(first.id, csv_provider, csv_provider.search_actresses(first.name)[0])
    service.sync(first.id, csv_provider)
    assert {movie.code for movie in service.movies(first.id)} == {"MIDV-123", "ABC-001", "ABC-002"}


def test_bad_import_never_applies_partial_rows(catalog, tmp_path):
    service, first, _ = catalog
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"code": "MIDV-001"}, {"code": "not-a-code"}]), encoding="utf-8")
    provider = FileCatalogProvider(str(path), first.name)
    service.bind(first.id, provider, provider.search_actresses(first.name)[0])
    with pytest.raises(SyncError):
        service.sync(first.id, provider)
    assert service.movies(first.id) == []


def test_dmm_response_mapping_and_secret_free_errors(monkeypatch):
    provider = DmmProvider("secret-api", "secret-affiliate")
    monkeypatch.setattr(
        provider,
        "_request",
        lambda endpoint, _params: (
            {
                "total_count": 1,
                "items": [
                    {
                        "content_id": "midv00123",
                        "product_id": "MIDV-123",
                        "title": "Test",
                        "date": "2026-09-01",
                        "maker": [{"name": "Maker"}],
                        "imageURL": {"large": "https://example.com/cover.jpg"},
                    }
                ],
            }
            if endpoint == "ItemList"
            else {"actress": [{"id": 123, "name": "Test Actress"}]}
        ),
    )
    assert provider.search_actresses("Test")[0].external_id == "123"
    page = provider.list_movies("123")
    assert page.complete and page.items[0].code == "MIDV-123"
    assert page.items[0].manufacturer == "Maker"
    assert provider.get_movie("midv00123").code == "MIDV-123"
    from urllib.error import HTTPError

    def denied(_request, timeout):
        raise HTTPError("https://api.dmm.com/?api_id=secret-api", 403, "denied", {}, None)

    monkeypatch.setattr("av_library.providers.dmm.urlopen", denied)
    with pytest.raises(ProviderError) as raised:
        DmmProvider("secret-api", "secret-affiliate")._request("ItemList", {})
    assert "secret-api" not in str(raised.value)


def test_v2_database_upgrade_preserves_scan_and_manual_code(tmp_path):
    db = Database(tmp_path / "previous.sqlite3")
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)"))
            for version, statements in MIGRATIONS[:2]:
                for statement in statements:
                    connection.execute(text(statement))
                connection.execute(
                    text("INSERT INTO schema_migrations(version) VALUES (:v)"), {"v": version}
                )
        actress = ActressService(db).save(ActressInput("旧用户", str(tmp_path / "videos")))
        with db.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO local_files
                (actress_id,path,path_key,scan_root_key,filename,normalized_code,manual_code,candidates,
                 parser_rule_id,parse_status,is_present,first_seen_time,last_seen_time)
                VALUES (:id,'X','x','r','old.mp4',NULL,'MIDV-999','[]',NULL,'unrecognized',1,
                        '2026-01-01','2026-01-01')"""),
                {"id": actress.id},
            )
        db.initialize()
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT max(version) FROM schema_migrations")) == 6
            assert connection.scalar(text("SELECT manual_code FROM local_files")) == "MIDV-999"
            assert connection.scalar(text("SELECT name FROM actresses")) == "旧用户"
    finally:
        db.close()
