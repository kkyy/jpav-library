import sqlite3
from threading import Event

import pytest
from sqlalchemy import select

from av_library.db.models import Actress
from av_library.providers.contracts import MovieMetadata, MoviePage, ProviderActress
from av_library.providers.dmm import ProviderError
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.automatic_sync import AutomaticSyncService
from av_library.services.backups import BackupError, BackupService
from av_library.services.metadata_sync import MetadataService
from av_library.services.settings import SettingsService


def test_backup_restore_and_safety_copy(database, tmp_path):
    service = ActressService(database)
    original = service.save(ActressInput("原资料", str(tmp_path / "a")))
    backing = BackupService(database, tmp_path / "test.sqlite3", tmp_path / "backups")
    snapshot = backing.backup(tmp_path / "export.sqlite3")
    assert backing.verify(snapshot) == 6
    service.save(ActressInput("后来新增", str(tmp_path / "b")))
    safety = backing.restore(snapshot)
    assert safety.is_file()
    assert [row.name for row in service.list()] == [original.name]
    with sqlite3.connect(safety) as connection:
        assert connection.execute("SELECT count(*) FROM actresses").fetchone()[0] == 2
    with database.sessions() as session:
        assert session.scalar(select(Actress).where(Actress.id == original.id)).name == "原资料"


def test_invalid_backup_rejected_before_current_data_changes(database, tmp_path):
    actress = ActressService(database).save(ActressInput("保留", str(tmp_path / "a")))
    backing = BackupService(database, tmp_path / "test.sqlite3", tmp_path / "backups")
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(BackupError):
        backing.restore(invalid)
    assert ActressService(database).get(actress.id).name == "保留"
    assert not (tmp_path / "backups").exists()
    with pytest.raises(BackupError):
        backing.backup(tmp_path / "test.sqlite3")


def test_opt_in_automatic_sync_only_runs_due_bound_source(database, tmp_path, monkeypatch):
    class Provider:
        id = "dmm"
        display_name = "test DMM"
        coverage_description = "fixture"

        def search_actresses(self, query):
            return (ProviderActress("dmm", "actress-1", query),)

        def list_movies(self, actress_external_id, cursor=None):
            return MoviePage((MovieMetadata("dmm", "item-1", "MIDV-001", "Test"),), None, True, 1)

        def get_movie(self, external_id):
            raise NotImplementedError

    provider = Provider()
    actress = ActressService(database).save(ActressInput("测试", str(tmp_path / "videos")))
    MetadataService(database).bind(actress.id, provider, provider.search_actresses("测试")[0])
    automatic = AutomaticSyncService(database)
    assert automatic.run_due(Event()) == (0, 0)
    SettingsService(database).save_update_interval(24)
    monkeypatch.setattr("av_library.services.automatic_sync.DmmProvider", lambda: provider)
    assert automatic.run_due(Event()) == (1, 0)
    assert automatic.run_due(Event()) == (0, 0)
    assert len(MetadataService(database).movies(actress.id)) == 1


def test_periodic_sync_can_refresh_bound_s1_without_dmm_credentials(
    database, tmp_path, monkeypatch
):
    class Provider:
        id = "s1_public"
        display_name = "test S1"
        coverage_description = "fixture"

        def list_movies(self, actress_external_id, cursor=None):
            assert actress_external_id == "813682"
            return MoviePage(
                (MovieMetadata(self.id, "SONE341", "SONE-341", "作品"),), None, True, 1
            )

    provider = Provider()
    actress = ActressService(database).save(ActressInput("翼舞", str(tmp_path / "videos")))
    MetadataService(database).bind(
        actress.id, provider, ProviderActress(provider.id, "813682", "つばさ舞")
    )
    SettingsService(database).save_update_interval(24)
    monkeypatch.setattr("av_library.services.automatic_sync.S1PublicProvider", lambda: provider)
    monkeypatch.setattr(
        "av_library.services.automatic_sync.DmmProvider",
        lambda: (_ for _ in ()).throw(ProviderError("missing")),
    )
    automatic = AutomaticSyncService(database)
    assert automatic.run_due(Event()) == (1, 0)
    assert automatic.run_due(Event()) == (0, 0)
