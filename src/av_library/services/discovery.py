"""Find an actress in available metadata sources and sync verified filmographies."""

import unicodedata
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from threading import Event

from av_library.db.models import Actress
from av_library.providers.contracts import MovieProvider
from av_library.providers.dmm import DmmProvider, ProviderError
from av_library.providers.s1_public import S1PublicProvider
from av_library.services.metadata_sync import MetadataService, SyncProgress


def _name_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


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
        providers: list[MovieProvider] = [S1PublicProvider()]
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
        query = (actress.japanese_name or actress.name).strip()
        if not query:
            return DiscoveryReport((SourceDiscovery("自动检索", "skipped", "请填写日文名"),))
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
                    candidates = tuple(
                        person
                        for person in provider.search_actresses(query)
                        if _name_key(person.japanese_name or person.name) == _name_key(query)
                    )
                    if not candidates:
                        results.append(
                            SourceDiscovery(
                                provider.display_name,
                                "not_found",
                                f"未找到与「{query}」完全同名的身份；请核对日文名",
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
                    self.metadata.bind(actress.id, provider, candidates[0])
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
