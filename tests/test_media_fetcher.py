from __future__ import annotations

import hashlib
from contextlib import nullcontext
from typing import Any

import pytest

from fireviewer_contracts.client.media_fetcher import MediaFetcher, MediaFetchError


class FakeResponse:
    status_code = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"content-length": str(len(payload))}

    def iter_bytes(self, _chunk_size: int):
        yield self.payload


class FakeClient:
    def __init__(self, payloads: dict[str, bytes], **_kwargs: Any) -> None:
        self.payloads = payloads
        self.requests: list[str] = []
        self.closed = False

    def stream(self, method: str, url: str):
        assert method == "GET"
        self.requests.append(url)
        return nullcontext(FakeResponse(self.payloads[url]))

    def close(self) -> None:
        self.closed = True


def test_batch_scope_downloads_one_signed_url_once_and_cleans_it(monkeypatch) -> None:
    url = "https://media.internal/private/photo.jpg?signature=secret"
    clients: list[FakeClient] = []

    def client_factory(**kwargs: Any) -> FakeClient:
        client = FakeClient({url: b"private-image"}, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("httpx.Client", client_factory)
    fetcher = MediaFetcher(
        allowed_hosts=frozenset({"media.internal"}),
        max_bytes=1_024,
        max_cache_bytes=1_024,
    )

    with fetcher.batch_scope():
        with fetcher.download(url) as first_path:
            assert first_path.read_bytes() == b"private-image"
        with fetcher.download(url) as second_path:
            assert second_path == first_path
            assert second_path.read_bytes() == b"private-image"
        retained_path = second_path

    assert len(clients) == 1
    assert clients[0].requests == [url]
    assert clients[0].closed is True
    assert not retained_path.exists()


def test_batch_cache_evicts_old_media_before_exceeding_its_budget(monkeypatch) -> None:
    urls = [f"https://media.internal/private/{index}.jpg" for index in range(2)]
    client = FakeClient({url: b"12345" for url in urls})
    monkeypatch.setattr("httpx.Client", lambda **_kwargs: client)
    fetcher = MediaFetcher(
        allowed_hosts=frozenset({"media.internal"}),
        max_bytes=10,
        max_cache_bytes=5,
    )

    with fetcher.batch_scope():
        for url in urls:
            with fetcher.download(url) as path:
                assert path.read_bytes() == b"12345"
        with fetcher.download(urls[0]) as path:
            assert path.read_bytes() == b"12345"

    assert client.requests == [urls[0], urls[1], urls[0]]


def test_download_verified_checks_the_digest_once_per_cached_asset(monkeypatch) -> None:
    url = "https://media.internal/private/terrain.fwterrain"
    payload = b"signed-spatial-reference"
    client = FakeClient({url: payload})
    monkeypatch.setattr("httpx.Client", lambda **_kwargs: client)
    fetcher = MediaFetcher(
        allowed_hosts=frozenset({"media.internal"}),
        max_bytes=1_024,
        max_cache_bytes=1_024,
    )

    with fetcher.batch_scope():
        with fetcher.download_verified(
            url, expected_sha256=hashlib.sha256(payload).hexdigest()
        ) as first_path:
            assert first_path.read_bytes() == payload
        with fetcher.download_verified(
            url, expected_sha256=hashlib.sha256(payload).hexdigest()
        ) as second_path:
            assert second_path == first_path

    assert client.requests == [url]


def test_download_verified_rejects_an_asset_with_the_wrong_digest(monkeypatch) -> None:
    url = "https://media.internal/private/terrain.fwterrain"
    client = FakeClient({url: b"tampered"})
    monkeypatch.setattr("httpx.Client", lambda **_kwargs: client)
    fetcher = MediaFetcher(
        allowed_hosts=frozenset({"media.internal"}),
        max_bytes=1_024,
        max_cache_bytes=1_024,
    )

    with (
        fetcher.batch_scope(),
        pytest.raises(MediaFetchError, match="SHA-256"),
        fetcher.download_verified(url, expected_sha256="0" * 64),
    ):
        raise AssertionError("a mismatched asset must never be yielded")
