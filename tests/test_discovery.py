from datetime import date

from av_library.providers.contracts import MovieMetadata, MoviePage, ProviderActress
from av_library.services.actresses import ActressInput, ActressService
from av_library.services.discovery import SoloCatalogDiscoveryService
from av_library.services.matching import MatchService
from av_library.services.metadata_sync import MetadataService


class CatalogProvider:
    id = "catalog_test"
    display_name = "测试官网"
    coverage_description = "测试厂牌目录"

    def __init__(self, candidates=None):
        self.candidates = candidates or (ProviderActress(self.id, "813682", "つばさ舞"),)
        self.searches = []

    def search_actresses(self, query):
        self.searches.append(query)
        return self.candidates

    def list_movies(self, actress_external_id, cursor=None):
        assert actress_external_id == "813682" and cursor is None
        return MoviePage(
            (
                MovieMetadata(
                    self.id, "SINGLE1", "SSIS-286", "单人作品", release_date=date(2021, 12, 28)
                ),
                MovieMetadata(self.id, "GROUP1", "OFJE-647", "多人作品", is_solo=False),
                MovieMetadata(self.id, "BEST1", "OFJE-474", "精选集", is_compilation=True),
            ),
            None,
            complete=True,
            total_hint=3,
        )

    def get_movie(self, external_id):
        raise NotImplementedError


def test_discovery_auto_binds_exact_actress_and_counts_only_solo(database, tmp_path):
    actress = ActressService(database).save(
        ActressInput("翼舞", str(tmp_path / "videos"), japanese_name="つばさ舞")
    )
    metadata = MetadataService(database)
    provider = CatalogProvider()
    service = SoloCatalogDiscoveryService(metadata)
    first = service.run(actress, providers=[provider])
    assert first.successful_sources == 1
    assert first.sources[0].items == 3
    assert provider.searches == ["つばさ舞"]
    assert MatchService(database).summary(actress.id).total == 1
    second = service.run(actress, providers=[provider])
    assert second.sources[0].added == 0
    assert provider.searches == ["つばさ舞"]


def test_discovery_does_not_bind_fuzzy_or_ambiguous_identity(database, tmp_path):
    actress = ActressService(database).save(
        ActressInput("翼舞", str(tmp_path / "videos"), japanese_name="つばさ舞")
    )
    metadata = MetadataService(database)
    provider = CatalogProvider((ProviderActress("catalog_test", "99", "つばさ舞子"),))
    result = SoloCatalogDiscoveryService(metadata).run(actress, providers=[provider])
    assert result.sources[0].status == "not_found"
    assert metadata.bindings(actress.id) == []
    provider.candidates = (
        ProviderActress("catalog_test", "1", "つばさ舞"),
        ProviderActress("catalog_test", "2", "つばさ舞"),
    )
    result = SoloCatalogDiscoveryService(metadata).run(actress, providers=[provider])
    assert result.sources[0].status == "ambiguous"
    assert metadata.bindings(actress.id) == []
