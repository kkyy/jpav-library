"""Credentialed DMM Affiliate v3 metadata adapter; no media endpoints."""

import json
import os
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from av_library.parsing.codes import CodeParser
from av_library.providers.contracts import (
    MovieMetadata,
    MoviePage,
    ProviderActress,
    infer_compilation_title,
)


class ProviderError(RuntimeError):
    pass


class DmmProvider:
    id = "dmm"
    display_name = "DMM/FANZA 官方 API"
    coverage_description = "DMM/FANZA API 可查询的商品；不保证全部作品、所有厂商或历史记录"
    base_url = "https://api.dmm.com/affiliate/v3"

    def __init__(self, api_id: str | None = None, affiliate_id: str | None = None):
        self.api_id = api_id or os.environ.get("DMM_API_ID", "")
        self.affiliate_id = affiliate_id or os.environ.get("DMM_AFFILIATE_ID", "")
        if not self.api_id or not self.affiliate_id:
            raise ProviderError("请先配置 DMM_API_ID 和 DMM_AFFILIATE_ID 环境变量。")

    def _request(self, endpoint: str, params: dict) -> dict:
        params = {
            "api_id": self.api_id,
            "affiliate_id": self.affiliate_id,
            "output": "json",
            **params,
        }
        request = Request(
            f"{self.base_url}/{endpoint}?{urlencode(params)}",
            headers={"User-Agent": "JPAVLibrary/0.6 (metadata only)"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                data = json.load(response)
        except HTTPError as error:
            # Do not show exception URL, which contains API credentials.
            raise ProviderError(
                f"DMM API 返回 HTTP {error.code}；请检查凭据、地区及接口可用性。"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("无法读取 DMM API 响应；请检查网络、地区或稍后重试。") from None
        if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
            raise ProviderError("DMM API 响应格式已变化。")
        return data["result"]

    def search_actresses(self, query: str) -> tuple[ProviderActress, ...]:
        result = self._request("ActressSearch", {"keyword": query, "hits": 50, "offset": 1})
        actresses = result.get("actress", [])
        if not isinstance(actresses, list):
            raise ProviderError("DMM 女优搜索格式已变化。")
        return tuple(
            ProviderActress(
                self.id, str(item["id"]), str(item["name"]), japanese_name=str(item["name"])
            )
            for item in actresses
            if isinstance(item, dict) and item.get("id") and item.get("name")
        )

    def list_movies(self, actress_external_id: str, cursor: str | None = None) -> MoviePage:
        offset = int(cursor) if cursor else 1
        if offset < 1 or offset > 50000:
            raise ProviderError("DMM API 分页超过支持范围，结果可能不完整。")
        result = self._request(
            "ItemList",
            {
                "site": "FANZA",
                "article": "actress",
                "article_id": actress_external_id,
                "hits": 100,
                "offset": offset,
            },
        )
        raw_items = result.get("items")
        if not isinstance(raw_items, list):
            raise ProviderError("DMM 作品列表格式已变化。")
        total = int(result.get("total_count", 0))
        items = []
        warnings = []
        for raw in raw_items:
            try:
                items.append(self._map_item(raw, actress_external_id))
            except ProviderError as error:
                warnings.append(str(error))
        next_offset = offset + len(raw_items)
        if total > 0 and next_offset <= total and not raw_items:
            raise ProviderError("DMM 返回空分页，但总数尚未读取完整。")
        next_cursor = str(next_offset) if next_offset <= total else None
        return MoviePage(
            tuple(items),
            next_cursor,
            complete=next_cursor is None and not warnings,
            total_hint=total,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _name(value):
        if isinstance(value, list):
            value = value[0] if value else None
        return str(value.get("name")) if isinstance(value, dict) and value.get("name") else None

    def _map_item(self, raw: dict, actress_external_id: str | None = None) -> MovieMetadata:
        if not isinstance(raw, dict):
            raise ProviderError("存在非对象作品条目。")
        raw_code = str(raw.get("product_id") or "")
        try:
            code = CodeParser().normalize_manual(raw_code)
            date_text = str(raw.get("date") or "")[:10].replace("/", "-")
            release = date.fromisoformat(date_text) if date_text else None
        except ValueError:
            raise ProviderError(f"不支持的番号或发行日期：{raw_code[:40]}") from None
        source_id = str(raw.get("content_id") or "")
        if not source_id:
            raise ProviderError(f"{code} 缺少来源 ID")
        image = raw.get("imageURL") or {}
        title = str(raw.get("title") or "")
        return MovieMetadata(
            self.id,
            source_id,
            code,
            title,
            japanese_title=title,
            release_date=release,
            manufacturer=self._name(raw.get("maker")),
            publisher=self._name(raw.get("label")),
            series=self._name(raw.get("series")),
            cover_url=image.get("large") if isinstance(image, dict) else None,
            detail_url=raw.get("URL"),
            actress_external_ids=(actress_external_id,) if actress_external_id else (),
            is_compilation=infer_compilation_title(title),
        )

    def get_movie(self, external_id: str) -> MovieMetadata:
        if not external_id.strip():
            raise ProviderError("作品来源 ID 不能为空。")
        result = self._request("ItemList", {"site": "FANZA", "cid": external_id, "hits": 1})
        items = result.get("items")
        if not isinstance(items, list):
            raise ProviderError("DMM 作品详情格式已变化。")
        for item in items:
            if isinstance(item, dict) and str(item.get("content_id")) == external_id:
                return self._map_item(item)
        raise ProviderError("DMM 未返回指定作品 ID。")
