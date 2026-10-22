"""SSRF protection for URL ingestion."""

import pytest

from src.ingestion.url_fetcher import SafeURLFetcher, UnsafeURLError


class _Response:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        return self.responses[url]


def _resolver(mapping):
    return lambda host: mapping[host]


PUBLIC = {
    "example.com": ["93.184.215.14"],
    "docs.example.com": ["93.184.215.14"],
    "evil.test": ["93.184.215.15"],
}


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://127.0.0.1:9200/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.10/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ],
)
def test_rejects_internal_and_non_http_targets(url):
    fetcher = SafeURLFetcher(allowed_domains=[], resolver=lambda host: [host.strip("[]")])
    with pytest.raises(UnsafeURLError):
        fetcher.validate(url)


def test_rejects_hostname_resolving_to_private_address():
    fetcher = SafeURLFetcher(allowed_domains=[], resolver=_resolver({"internal.test": ["10.1.2.3"]}))
    with pytest.raises(UnsafeURLError):
        fetcher.validate("http://internal.test/")


def test_allowlist_restricts_domains():
    fetcher = SafeURLFetcher(allowed_domains=["example.com"], resolver=_resolver(PUBLIC))
    fetcher.validate("https://docs.example.com/page")
    with pytest.raises(UnsafeURLError):
        fetcher.validate("https://evil.test/page")


def test_redirect_to_internal_address_is_blocked():
    session = _Session(
        {"https://example.com/a": _Response(302, {"Location": "http://169.254.169.254/latest"})}
    )
    resolver = _resolver({**PUBLIC, "169.254.169.254": ["169.254.169.254"]})
    fetcher = SafeURLFetcher(allowed_domains=[], resolver=resolver, session=session)
    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/a")
    assert session.requested == ["https://example.com/a"]


def test_fetch_follows_safe_redirect_and_enforces_size():
    session = _Session(
        {
            "https://example.com/a": _Response(301, {"Location": "/b"}),
            "https://example.com/b": _Response(200, {"Content-Type": "text/html"}, b"<p>hi</p>"),
        }
    )
    fetcher = SafeURLFetcher(allowed_domains=[], resolver=_resolver(PUBLIC), session=session)
    resource = fetcher.fetch("https://example.com/a")
    assert resource.content == b"<p>hi</p>"
    assert resource.url == "https://example.com/b"

    big = _Session({"https://example.com/big": _Response(200, {}, b"x" * 100)})
    fetcher = SafeURLFetcher(allowed_domains=[], resolver=_resolver(PUBLIC), session=big, max_bytes=10)
    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/big")


def test_ingest_endpoint_returns_400_for_internal_url(client, auth_headers):
    payload = {"source": "url", "documents": [{"url": "http://127.0.0.1:9200/_cat/indices"}]}
    response = client.post("/ingest", json=payload, headers=auth_headers)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unsafe_url"
