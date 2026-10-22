"""Document loaders and the ingestion pipeline."""

import pytest

from src.ingestion.config import ChunkingConfig, LoaderConfig
from src.ingestion.loaders import DocumentLoaderFactory, UnsupportedDocumentTypeError
from src.ingestion.pipeline import IngestionPipeline
from src.ingestion.url_fetcher import FetchedResource


class _Fetcher:
    def __init__(self, resource):
        self.resource = resource

    def fetch(self, url):
        return self.resource


@pytest.fixture
def loader_config(tmp_path):
    return LoaderConfig(temp_dir=tmp_path)


def test_bytes_use_client_filename_as_source(loader_config):
    factory = DocumentLoaderFactory(loader_config)
    batch = factory.load_from_bytes(content="Bonjour le monde".encode(), filename="notes.md", metadata={"team": "x"})
    assert batch.source == "notes.md"
    assert batch.documents[0].page_content == "Bonjour le monde"
    assert batch.documents[0].metadata["source"] == "notes.md"
    assert batch.documents[0].metadata["team"] == "x"


def test_temp_files_are_removed(loader_config, tmp_path):
    DocumentLoaderFactory(loader_config).load_from_bytes(content=b"x", filename="a.txt")
    assert list(tmp_path.iterdir()) == []


def test_allowed_mime_types_are_enforced(tmp_path):
    factory = DocumentLoaderFactory(LoaderConfig(temp_dir=tmp_path, allowed_mime_types=["application/pdf"]))
    with pytest.raises(UnsupportedDocumentTypeError):
        factory.load_from_bytes(content=b"x", filename="a.txt")


def test_html_url_is_converted_to_text(loader_config):
    html = b"<html><head><script>evil()</script><style>p{}</style></head><body><h1>Title</h1><p>Body text</p></body></html>"
    fetcher = _Fetcher(FetchedResource(url="https://example.com/page", content=html, content_type="text/html; charset=utf-8"))
    batch = DocumentLoaderFactory(loader_config, url_fetcher=fetcher).load_from_url("https://example.com/page")

    text = batch.documents[0].page_content
    assert "Title" in text and "Body text" in text
    assert "evil" not in text
    assert batch.mime_type == "text/html"
    assert batch.documents[0].metadata["source"] == "https://example.com/page"


def test_non_html_url_uses_file_loaders(loader_config):
    fetcher = _Fetcher(FetchedResource(url="https://example.com/readme", content=b"plain text", content_type="text/plain"))
    batch = DocumentLoaderFactory(loader_config, url_fetcher=fetcher).load_from_url("https://example.com/readme")
    assert batch.documents[0].page_content == "plain text"
    assert batch.source == "https://example.com/readme"


def test_pipeline_chunks_with_metadata(loader_config):
    pipeline = IngestionPipeline(loader_config=loader_config, chunking_config=ChunkingConfig(chunk_size=400, chunk_overlap=50))
    result = pipeline.ingest_bytes(content=("sentence. " * 300).encode(), filename="long.txt")
    assert len(result.chunks) > 1
    assert [c.metadata["chunk_index"] for c in result.chunks] == list(range(len(result.chunks)))
    assert all(c.metadata["source"] == "long.txt" for c in result.chunks)


def test_chunking_config_validation():
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=100)


def test_html_file_is_loaded_without_unstructured(loader_config):
    batch = DocumentLoaderFactory(loader_config).load_from_bytes(
        content=b"<html><body><p>Hello</p><script>x()</script></body></html>", filename="page.html"
    )
    assert batch.documents[0].page_content == "Hello"


def test_unknown_format_without_unstructured_is_unsupported(loader_config, monkeypatch):
    from src.ingestion import loaders

    monkeypatch.setattr(loaders, "_module_available", lambda name: False)
    with pytest.raises(UnsupportedDocumentTypeError):
        DocumentLoaderFactory(loader_config).load_from_bytes(content=b"x", filename="data.xyz")


def test_utf8_text_keeps_accents(loader_config):
    batch = DocumentLoaderFactory(loader_config).load_from_bytes(
        content="Politique de sécurité : délai de 72 heures".encode("utf-8"), filename="fr.txt"
    )
    assert batch.documents[0].page_content == "Politique de sécurité : délai de 72 heures"


def test_latin1_text_is_still_decoded(loader_config):
    batch = DocumentLoaderFactory(loader_config).load_from_bytes(content="déjà vu".encode("latin-1"), filename="old.txt")
    assert "vu" in batch.documents[0].page_content
