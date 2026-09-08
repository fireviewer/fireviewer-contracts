from __future__ import annotations

import tempfile
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import SplitResult, urlsplit


class MediaFetchError(RuntimeError):
    pass


class MediaFetcher:
    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str],
        max_bytes: int,
        max_cache_bytes: int | None = None,
    ) -> None:
        self.allowed_hosts = allowed_hosts
        self.max_bytes = max_bytes
        self.max_cache_bytes = max_bytes if max_cache_bytes is None else max_cache_bytes
        if self.max_bytes < 1 or self.max_cache_bytes < 0:
            raise ValueError("media download and cache budgets must be non-negative")
        self._cache_directory: Path | None = None
        self._cache_entries: OrderedDict[str, tuple[Path, int]] = OrderedDict()
        self._cache_sha256: dict[str, str] = {}
        self._cached_bytes = 0
        self._client: Any = None

    @contextmanager
    def batch_scope(self) -> Iterator[None]:
        """Cache private media only for one batch, then remove every local copy."""

        if self._cache_directory is not None:
            raise RuntimeError("a media batch scope is already active")
        with TemporaryDirectory(prefix="fw-media-batch-") as directory:
            self._cache_directory = Path(directory)
            self._cache_entries.clear()
            self._cache_sha256.clear()
            self._cached_bytes = 0
            try:
                yield
            finally:
                if self._client is not None:
                    self._client.close()
                self._client = None
                self._cache_entries.clear()
                self._cache_sha256.clear()
                self._cached_bytes = 0
                self._cache_directory = None

    def _validated_url(self, url: str) -> SplitResult:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise MediaFetchError("media URL is outside the configured internal HTTPS boundary")
        return parsed

    def _stream(self, url: str) -> Any:
        import httpx

        if self._cache_directory is None:
            return httpx.stream(
                "GET",
                url,
                follow_redirects=False,
                timeout=httpx.Timeout(60, connect=10),
                headers={"Accept": "application/octet-stream"},
            )
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(60, connect=10),
                headers={"Accept": "application/octet-stream"},
            )
        return self._client.stream("GET", url)

    def _download_to(self, url: str, target: Path) -> int:
        written = 0
        with target.open("wb") as stream, self._stream(url) as response:
            if response.status_code != 200:
                raise MediaFetchError(
                    f"internal media download returned HTTP {response.status_code}"
                )
            declared = response.headers.get("content-length")
            if declared:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise MediaFetchError("internal media size header is invalid") from exc
                if declared_size > self.max_bytes:
                    raise MediaFetchError("declared media size exceeds the download budget")
            for chunk in response.iter_bytes(1024 * 1024):
                written += len(chunk)
                if written > self.max_bytes:
                    raise MediaFetchError("streamed media exceeds the download budget")
                stream.write(chunk)
        return written

    def _evict_until_fits(self, required_bytes: int) -> None:
        while self._cache_entries and self._cached_bytes + required_bytes > self.max_cache_bytes:
            _url, (path, size) = self._cache_entries.popitem(last=False)
            path.unlink(missing_ok=True)
            self._cache_sha256.pop(_url, None)
            self._cached_bytes -= size

    @contextmanager
    def download(self, url: str) -> Iterator[Path]:
        parsed = self._validated_url(url)
        suffix = Path(parsed.path).suffix[:16]
        cached = self._cache_entries.get(url)
        if cached is not None:
            cached_path, cached_size = cached
            if cached_path.is_file():
                self._cache_entries.move_to_end(url)
                yield cached_path
                return
            self._cache_entries.pop(url)
            self._cache_sha256.pop(url, None)
            self._cached_bytes -= cached_size

        target: Path | None = None
        keep_cached = False
        try:
            if self._cache_directory is None:
                with tempfile.NamedTemporaryFile(
                    prefix="fw-media-", suffix=suffix, delete=False
                ) as temporary:
                    target = Path(temporary.name)
            else:
                digest = sha256(url.encode("utf-8")).hexdigest()
                target = self._cache_directory / f"{digest}.part{suffix}"
            written = self._download_to(url, target)
            if self._cache_directory is not None and written <= self.max_cache_bytes:
                self._evict_until_fits(written)
                cache_target = (
                    self._cache_directory / f"{sha256(url.encode('utf-8')).hexdigest()}{suffix}"
                )
                target.replace(cache_target)
                target = cache_target
                self._cache_entries[url] = (target, written)
                self._cached_bytes += written
                keep_cached = True
            yield target
        finally:
            if target is not None and not keep_cached:
                target.unlink(missing_ok=True)

    @contextmanager
    def download_verified(self, url: str, *, expected_sha256: str) -> Iterator[Path]:
        """Download a private asset and fail closed when its declared digest changed."""

        normalized_digest = expected_sha256.lower()
        if len(normalized_digest) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_digest
        ):
            raise MediaFetchError("expected media SHA-256 is invalid")
        with self.download(url) as path:
            actual_digest = self._cache_sha256.get(url)
            if actual_digest is None:
                digest = sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                actual_digest = digest.hexdigest()
                if self._cache_directory is not None:
                    self._cache_sha256[url] = actual_digest
            if actual_digest != normalized_digest:
                raise MediaFetchError("private media SHA-256 does not match its manifest")
            yield path
