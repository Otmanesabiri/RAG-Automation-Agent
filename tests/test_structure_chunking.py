"""Structure-aware chunking, page metadata and OCR fallback."""

import io
import sys
from types import ModuleType, SimpleNamespace

import pytest
from pypdf import PdfWriter

from src.ingestion import loaders
from src.ingestion.chunker import ChunkingService
from src.ingestion.config import ChunkingConfig, LoaderConfig
from src.ingestion.loaders import DocumentLoaderFactory

MARKDOWN = """# Guide

Intro text.

## Installation

Run the installer and follow the steps.

## Usage

### Advanced

Use the advanced flags carefully.
"""


def _doc(content, **metadata):
    return SimpleNamespace(page_content=content, metadata={"source": "guide.md", **metadata})


def test_markdown_is_split_by_section():
    chunks = ChunkingService(ChunkingConfig(chunk_size=500, chunk_overlap=0)).split_documents([_doc(MARKDOWN)])
    by_section = {c.metadata.get("section"): c.page_content for c in chunks}

    assert "Guide > Installation" in by_section
    assert "Run the installer" in by_section["Guide > Installation"]
    assert "Use the advanced flags" in by_section["Guide > Usage > Advanced"]
    # Sections never share a chunk, and heading keys do not leak into metadata.
    assert "Use the advanced" not in by_section["Guide > Installation"]
    assert all("h1" not in c.metadata for c in chunks)


def test_structure_awareness_can_be_disabled():
    config = ChunkingConfig(chunk_size=500, chunk_overlap=0, structure_aware=False)
    chunks = ChunkingService(config).split_documents([_doc(MARKDOWN)])
    assert len(chunks) == 1
    assert "section" not in chunks[0].metadata


def test_plain_text_is_not_split_on_hash_lines():
    doc = SimpleNamespace(page_content="# not a heading in a .txt file\nbody", metadata={"source": "a.txt"})
    chunks = ChunkingService(ChunkingConfig(chunk_size=500, chunk_overlap=0)).split_documents([doc])
    assert "section" not in chunks[0].metadata


def test_chunk_index_is_continuous_across_pdf_pages():
    pages = [
        SimpleNamespace(page_content="page one " * 30, metadata={"source": "r.pdf", "page": 0}),
        SimpleNamespace(page_content="page two " * 30, metadata={"source": "r.pdf", "page": 1}),
    ]
    chunks = ChunkingService(ChunkingConfig(chunk_size=150, chunk_overlap=0)).split_documents(pages)
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert {c.metadata["chunk_count"] for c in chunks} == {len(chunks)}
    assert {c.metadata["page_number"] for c in chunks} == {1, 2}


def test_html_headings_become_sections(tmp_path):
    html = b"<html><body><h1>Policy</h1><p>Scope.</p><h2>Retention</h2><p>Keep 5 years.</p></body></html>"
    batch = DocumentLoaderFactory(LoaderConfig(temp_dir=tmp_path)).load_from_bytes(content=html, filename="policy.html")
    chunks = ChunkingService(ChunkingConfig(chunk_size=500, chunk_overlap=0)).split_documents(batch.documents)
    assert {c.metadata.get("section") for c in chunks} >= {"Policy > Retention"}
    assert batch.documents[0].metadata["mime_type"] == "text/html"


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_scanned_pages_are_flagged_without_ocr_extras(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "_module_available", lambda name: False)
    batch = DocumentLoaderFactory(LoaderConfig(temp_dir=tmp_path)).load_from_bytes(content=_blank_pdf(), filename="scan.pdf")
    assert batch.documents[0].metadata["needs_ocr"] is True


def test_scanned_pages_are_ocred_when_available(tmp_path, monkeypatch):
    pytesseract = ModuleType("pytesseract")
    pytesseract.image_to_string = lambda image, lang: f"recognised text ({lang})"
    pdf2image = ModuleType("pdf2image")
    pdf2image.convert_from_path = lambda path, first_page, last_page: ["image"]
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    monkeypatch.setitem(sys.modules, "pdf2image", pdf2image)
    monkeypatch.setattr(loaders, "_module_available", lambda name: True)
    monkeypatch.setenv("OCR_LANGUAGES", "fra")

    batch = DocumentLoaderFactory(LoaderConfig(temp_dir=tmp_path)).load_from_bytes(content=_blank_pdf(), filename="scan.pdf")
    assert batch.documents[0].page_content == "recognised text (fra)"
    assert batch.documents[0].metadata["ocr"] is True


@pytest.mark.parametrize("structure_aware", [True, False])
def test_ingest_options_keep_structure_setting(structure_aware):
    service = ChunkingService(ChunkingConfig(structure_aware=structure_aware))
    assert service._resolve_config(_doc("x")).structure_aware is structure_aware
