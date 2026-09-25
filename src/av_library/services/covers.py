"""User-initiated, bounded metadata cover cache."""

import hashlib
import ipaddress
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader


class CoverError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise CoverError("封面地址发生重定向，请检查来源 URL。")


class CoverService:
    MAX_BYTES = 5_000_000

    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CoverError("仅支持公开 HTTPS 封面地址。")
        hostname = parsed.hostname.casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise CoverError("封面地址不能指向本机。")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise CoverError("封面地址不能指向本机或私有网络。")

    def cached(self, url: str | None) -> Path | None:
        if not url:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.directory / f"{key}.png"
        return path if path.is_file() else None

    def fetch(self, url: str) -> Path:
        self._validate_url(url)
        existing = self.cached(url)
        if existing:
            return existing
        request = Request(url, headers={"User-Agent": "JPAVLibrary/0.6"})
        opener = build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=15) as response:
                content_type = response.headers.get_content_type()
                if content_type not in ("image/jpeg", "image/png", "image/webp"):
                    raise CoverError("来源没有返回支持的封面图片。")
                content = response.read(self.MAX_BYTES + 1)
        except CoverError:
            raise
        except Exception as error:
            raise CoverError("封面获取失败，请检查网络或来源地址。") from error
        if len(content) > self.MAX_BYTES:
            raise CoverError("封面图片超过 5 MB 限制。")
        data = QByteArray(content)
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        QImageReader.setAllocationLimit(32)
        reader = QImageReader(buffer)
        dimensions = reader.size()
        if not dimensions.isValid() or max(dimensions.width(), dimensions.height()) > 8000:
            raise CoverError("封面尺寸无效或过大。")
        reader.setScaledSize(dimensions.scaled(QSize(300, 450), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            raise CoverError("封面图片无法解码。")
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="cover-", suffix=".png", dir=self.directory)
        os.close(handle)
        temporary = Path(name)
        try:
            if not image.save(str(temporary), "PNG"):
                raise CoverError("封面缓存写入失败。")
            target = self.directory / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.png"
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
