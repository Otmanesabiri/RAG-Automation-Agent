"""Safe retrieval of remote documents (SSRF protection)."""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_SCHEMES = {"http", "https"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}

Resolver = Callable[[str], Iterable[str]]


class UnsafeURLError(ValueError):
    """Raised when a URL targets a forbidden scheme, host or network."""


@dataclass(slots=True)
class FetchedResource:
    url: str
    content: bytes
    content_type: Optional[str]


def _default_resolver(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Cannot resolve host: {host}") from exc
    return [info[4][0] for info in infos]


def _parse_allowlist(raw: Optional[str]) -> List[str]:
    return [domain.strip().lower().lstrip(".") for domain in (raw or "").split(",") if domain.strip()]


class SafeURLFetcher:
    """HTTP fetcher that refuses internal targets, including through redirects.

    Every hop is validated: the scheme must be http(s), the host must match the
    optional domain allowlist, and every resolved address must be public.
    """

    def __init__(
        self,
        *,
        allowed_domains: Optional[Iterable[str]] = None,
        timeout: float = 10.0,
        max_bytes: int = 20 * 1024 * 1024,
        max_redirects: int = 5,
        resolver: Resolver = _default_resolver,
        session: Optional[requests.Session] = None,
    ) -> None:
        if allowed_domains is None:
            allowed_domains = _parse_allowlist(os.getenv("INGEST_URL_ALLOWLIST"))
        self.allowed_domains = [d.lower() for d in allowed_domains]
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.session = session or requests.Session()

    def validate(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            raise UnsafeURLError(f"Scheme not allowed: {parsed.scheme or '(none)'}")
        host = (parsed.hostname or "").lower()
        if not host:
            raise UnsafeURLError("URL has no host")
        if self.allowed_domains and not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
            raise UnsafeURLError(f"Host not in allowlist: {host}")
        for address in self.resolver(host):
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global or ip.is_multicast:
                raise UnsafeURLError(f"Host {host} resolves to a non-public address")

    def fetch(self, url: str) -> FetchedResource:
        current = url
        for _ in range(self.max_redirects + 1):
            self.validate(current)
            response = self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True)
            try:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise UnsafeURLError("Redirect without Location header")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content = self._read_limited(response)
                return FetchedResource(url=current, content=content, content_type=response.headers.get("Content-Type"))
            finally:
                response.close()
        raise UnsafeURLError(f"Too many redirects (>{self.max_redirects})")

    def _read_limited(self, response: requests.Response) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise UnsafeURLError(f"Response larger than {self.max_bytes} bytes")
        chunks: List[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > self.max_bytes:
                raise UnsafeURLError(f"Response larger than {self.max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
