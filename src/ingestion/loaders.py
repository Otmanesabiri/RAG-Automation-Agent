"""Document loaders for multiple file types using LangChain loaders."""

from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

from langchain_core.documents import Document

from ..observability.logging import get_logger
from .config import LoaderConfig
from .types import DocumentLike
from .url_fetcher import SafeURLFetcher

logger = get_logger(__name__)


def _html_to_text(content: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # Keep the heading structure as Markdown so the chunker can split on sections.
    for level in range(1, 5):
        for heading in soup.find_all(f"h{level}"):
            text = heading.get_text(" ", strip=True)
            if text:
                heading.string = f"{'#' * level} {text}"
    return soup.get_text(separator="\n", strip=True)


class _HTMLLoader:
    """Minimal HTML loader (scripts and styles stripped) with the LangChain ``load()`` API."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> List[Document]:
        return [Document(page_content=_html_to_text(self.path.read_bytes()), metadata={})]


def _module_available(name: str) -> bool:
    from importlib.util import find_spec

    return find_spec(name) is not None


class UnsupportedDocumentTypeError(ValueError):
    """Raised when the loader factory cannot handle the document type."""


@dataclass(slots=True)
class LoadedDocumentBatch:
    """Container returned by the loader factory with applied metadata."""

    documents: List[DocumentLike]
    source: str
    mime_type: Optional[str]


class DocumentLoaderFactory:
    """Factory responsible for resolving and executing document loaders."""

    SUPPORTED_SUFFIXES: Dict[str, str] = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    def __init__(self, config: Optional[LoaderConfig] = None, url_fetcher: Optional[SafeURLFetcher] = None) -> None:
        self.config = config or LoaderConfig()
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)
        self.url_fetcher = url_fetcher or SafeURLFetcher()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_from_path(
        self,
        path: str | Path,
        metadata: Optional[Dict[str, str]] = None,
    ) -> LoadedDocumentBatch:
        """Load documents from a local filesystem path."""

        resolved_path = Path(path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Document not found: {resolved_path}")

        mime_type = self._detect_mime_type(resolved_path)
        self._ensure_allowed_type(mime_type)

        loader = self._resolve_loader(resolved_path, mime_type)
        documents = loader.load()
        if mime_type == "application/pdf":
            documents = self._ocr_empty_pages(resolved_path, documents)
        self._merge_metadata(documents, {"mime_type": mime_type, **(metadata or {})}, str(resolved_path))
        return LoadedDocumentBatch(documents=documents, source=str(resolved_path), mime_type=mime_type)

    def load_from_url(
        self,
        url: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> LoadedDocumentBatch:
        """Load documents from a remote HTTP/HTTPS endpoint.

        The download goes through :class:`SafeURLFetcher`, which rejects internal
        targets (SSRF). HTML is converted to text; other formats are handed to the
        regular file loaders.
        """

        resource = self.url_fetcher.fetch(url)
        mime_type = (resource.content_type or "").split(";")[0].strip().lower() or None
        if not mime_type and metadata:
            mime_type = metadata.get("mime_type")
        self._ensure_allowed_type(mime_type)

        if mime_type in (None, "text/html", "application/xhtml+xml"):
            documents = [Document(page_content=_html_to_text(resource.content), metadata={})]
        else:
            filename = Path(urlparse(resource.url).path).name or "document"
            if not Path(filename).suffix:
                filename += mimetypes.guess_extension(mime_type) or ".txt"
            documents = self.load_from_bytes(content=resource.content, filename=filename).documents

        self._merge_metadata(documents, {"mime_type": mime_type, **(metadata or {})}, url)
        return LoadedDocumentBatch(documents=documents, source=url, mime_type=mime_type)

    def load_from_bytes(
        self,
        *,
        content: bytes,
        filename: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> LoadedDocumentBatch:
        """Persist bytes to a temporary file and delegate to `load_from_path`."""

        mime_type = self._detect_mime_type(filename)
        self._ensure_allowed_type(mime_type)

        with self._temp_file(filename) as temp_path:
            temp_path.write_bytes(content)
            batch = self.load_from_path(temp_path, metadata=metadata)
        # Report the client's filename, not the random temp path: the source is
        # shown in citations and identifies the document for re-ingestion.
        source = (metadata or {}).get("source") or filename
        self._merge_metadata(batch.documents, {}, source)
        return LoadedDocumentBatch(documents=batch.documents, source=source, mime_type=mime_type or batch.mime_type)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ocr_empty_pages(self, path: Path, documents: List[DocumentLike]) -> List[DocumentLike]:
        """Recover text of scanned PDF pages (no text layer) with Tesseract OCR.

        Needs the optional extras (pytesseract, pdf2image) plus the tesseract and
        poppler binaries. Without them, empty pages are flagged ``needs_ocr``.
        """
        empty = [doc for doc in documents if not doc.page_content.strip()]
        if not empty:
            return documents
        if not (_module_available("pytesseract") and _module_available("pdf2image")):
            for doc in empty:
                doc.metadata["needs_ocr"] = True
            logger.warning(f"{len(empty)} PDF page(s) without text in {path.name}; install OCR extras to index them")
            return documents

        import pytesseract
        from pdf2image import convert_from_path

        languages = os.getenv("OCR_LANGUAGES", "eng+fra")
        for doc in empty:
            page = doc.metadata.get("page", 0)
            try:
                images = convert_from_path(str(path), first_page=page + 1, last_page=page + 1)
                doc.page_content = "\n".join(pytesseract.image_to_string(image, lang=languages) for image in images)
                doc.metadata["ocr"] = True
            except Exception as exc:  # one unreadable page must not fail the whole document
                doc.metadata["needs_ocr"] = True
                logger.warning(f"OCR failed for page {page + 1} of {path.name}: {exc}")
        return documents

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _resolve_loader(self, path: Path, mime_type: Optional[str]):
        suffix = path.suffix.lower()
        loaders_module = "langchain_community.document_loaders"
        if suffix == ".pdf":
            loader_cls = self._load_loader_class(loaders_module, "PyPDFLoader")
            return loader_cls(path)
        if suffix == ".docx":
            loader_cls = self._load_loader_class(loaders_module, "Docx2txtLoader")
            return loader_cls(path)
        if suffix in {".txt", ".md"}:
            loader_cls = self._load_loader_class(loaders_module, "TextLoader")
            return loader_cls(path, autodetect_encoding=True)
        if suffix in {".html", ".htm"}:
            return _HTMLLoader(path)

        # Optional extra (pip install -r requirements-extras.txt): handles exotic formats.
        if self.config.use_unstructured and _module_available("unstructured"):
            loader_cls = self._load_loader_class(loaders_module, "UnstructuredFileLoader")
            return loader_cls(str(path))

        raise UnsupportedDocumentTypeError(f"Unsupported document type for path={path} mime_type={mime_type}")

    def _ensure_allowed_type(self, mime_type: Optional[str]) -> None:
        if self.config.allowed_mime_types and mime_type:
            allowed = set(self.config.allowed_mime_types)
            if mime_type not in allowed:
                raise UnsupportedDocumentTypeError(f"MIME type {mime_type} not in allowed list")

    def _merge_metadata(
        self,
        documents: Iterable[DocumentLike],
        metadata: Dict[str, str],
        source: str,
    ) -> None:
        for doc in documents:
            doc.metadata = {**doc.metadata, **metadata, "source": source}

    def _detect_mime_type(self, value: str | Path) -> Optional[str]:
        if isinstance(value, Path):
            guess, _ = mimetypes.guess_type(value.name)
        else:
            guess, _ = mimetypes.guess_type(value)
        if guess:
            return guess
        if isinstance(value, Path):
            suffix = value.suffix.lower()
        else:
            suffix = Path(value).suffix.lower()
        return self.SUPPORTED_SUFFIXES.get(suffix)

    def _temp_file(self, filename: str):  # pragma: no cover - context manager wrapper
        class _TempFile:
            def __init__(self, factory: DocumentLoaderFactory, filename: str) -> None:
                self.factory = factory
                self.filename = filename
                self.path: Optional[Path] = None

            def __enter__(self) -> Path:
                suffix = Path(self.filename).suffix
                fd, raw_path = tempfile.mkstemp(
                    suffix=suffix,
                    dir=self.factory.config.temp_dir,
                    prefix="uploaded-",
                )
                os.close(fd)  # only the path is used; an open handle leaks and blocks deletion on Windows
                Path(raw_path).chmod(0o600)
                self.path = Path(raw_path)
                return self.path

            def __exit__(self, exc_type, exc, tb) -> None:
                if self.path and self.path.exists():
                    try:
                        self.path.unlink()
                    except OSError:
                        shutil.rmtree(self.path, ignore_errors=True)

        return _TempFile(self, filename)

    def _load_loader_class(self, module: str, attribute: str):  # pragma: no cover - dynamic import helper
        try:
            loaded_module = import_module(module)
            return getattr(loaded_module, attribute)
        except (ImportError, AttributeError) as exc:  # pragma: no cover
            raise RuntimeError(
                f"Failed to import {attribute} from {module}. " "Ensure langchain-community and related extras are installed."
            ) from exc
