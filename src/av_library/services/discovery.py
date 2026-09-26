"""Find an actress in available metadata sources and sync verified filmographies."""

from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from threading import Event

from av_library.db.models import Actress
from av_library.providers.actress_names import name_key, search_names
from av_library.providers.contracts import MovieProvider
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.providers.ideapocket_public import IdeaPocketPublicProvider
from av_library.providers.s1_public import S1PublicProvider
from av_library.services.actresses import ActressService
from av_library.services.metadata_sync import MetadataService, SyncProgress


@dataclass(frozen=True)
class SourceDiscovery:
    name: str
    status: str
    message: str
    items: int = 0
    added: int = 0


@dataclass(frozen=True)
class DiscoveryReport:
    sources: tuple[SourceDiscovery, ...]

    @property
    def successful_sources(self) -> int:
        return sum(source.status == "success" for source in self.sources)

    def summary(self) -> str:
        return "；".join(f"{source.name}：{source.message}" for source in self.sources)


class SoloCatalogDiscoveryService:
    def __init__(self, metadata: MetadataService):
        self.metadata = metadata

    def _available_providers(self) -> tuple[MovieProvider, ...]:
        providers: list[MovieProvider] = [S1PublicProvider(), IdeaPocketPublicProvider()]
        with suppress(ProviderError):
            providers.append(DmmProvider())
        return tuple(providers)

    def run(
        self,
        actress: Actress,
        cancel: Event | None = None,
        progress: Callable[[SyncProgress], None] = lambda _progress: None,
        providers: Sequence[MovieProvider] | None = None,
    ) -> DiscoveryReport:
        cancel = cancel or Event()
        search_terms = search_names(
            actress.japanese_name,
            actress.name,
            aliases=tuple(actress.aliases or ()),
        )
        expected_names = {name_key(term) for term in search_terms}
        if not search_terms:
            return DiscoveryReport((SourceDiscovery("自动检索", "skipped", "请填写女优名称"),))
        selected = tuple(providers) if providers is not None else self._available_providers()
        results: list[SourceDiscovery] = []
        for provider in selected:
            if cancel.is_set():
                results.append(SourceDiscovery(provider.display_name, "cancelled", "已取消"))
                break
            try:
                bound = next(
                    (
                        source
                        for source, _ in self.metadata.bindings(actress.id)
                        if source.provider_id == provider.id
                    ),
                    None,
                )
                if bound is None:
                    candidate_map = {}
                    for term in search_terms:
                        for person in provider.search_actresses(term):
                            canonical = name_key(person.japanese_name or person.name)
                            if canonical in expected_names:
                                candidate_map[person.external_id] = person
                        if candidate_map and actress.japanese_name.strip():
                            break
                    candidates = tuple(candidate_map.values())
                    if not candidates:
                        results.append(
                            SourceDiscovery(
                                provider.display_name,
                                "not_found",
                                f"未找到与「{actress.name}」匹配的来源身份；可在别名中补充日文名",
                            )
                        )
                        continue
                    if len(candidates) != 1:
                        results.append(
                            SourceDiscovery(
                                provider.display_name,
                                "ambiguous",
                                "找到多个同名身份，请手动绑定",
                            )
                        )
                        continue
                    if cancel.is_set():
                        results.append(
                            SourceDiscovery(provider.display_name, "cancelled", "已取消")
                        )
                        break
                    selected_person = candidates[0]
                    self.metadata.bind(actress.id, provider, selected_person)
                    resolved_name = (
                        selected_person.japanese_name or selected_person.name
                    ).strip()
                    if not actress.japanese_name.strip() and resolved_name:
                        ActressService(self.metadata.database).set_japanese_name(
                            actress.id, resolved_name
                        )
                history = self.metadata.sync(actress.id, provider, cancel, progress)
                if history.status == "cancelled":
                    results.append(SourceDiscovery(provider.display_name, "cancelled", "已取消"))
                    break
                results.append(
                    SourceDiscovery(
                        provider.display_name,
                        "success",
                        f"读取 {history.items_received} 条，新增关联 {history.added_count} 条",
                        history.items_received,
                        history.added_count,
                    )
                )
            except Exception as error:  # noqa: BLE001 -- one source must not prevent another
                results.append(SourceDiscovery(provider.display_name, "failed", str(error)))
        if not selected:
            results.append(SourceDiscovery("自动检索", "skipped", "没有可用的数据源"))
        if providers is None and not any(provider.id == "dmm" for provider in selected):
            results.append(
                SourceDiscovery(
                    "DMM/FANZA 官方 API",
                    "skipped",
                    "未配置 API 凭据，跨厂牌目录未检索",
                )
            )
        return DiscoveryReport(tuple(results))
