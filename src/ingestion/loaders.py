"""Document loaders for multiple file types using LangChain loaders."""

from __future__ import annotations

import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .config import LoaderConfig
from .types import DocumentLike


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

    def __init__(self, config: Optional[LoaderConfig] = None) -> None:
        self.config = config or LoaderConfig()
        self.config.temp_dir.mkdir(parents=True, exist_ok=True)

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
        self._merge_metadata(documents, metadata or {}, str(resolved_path))
        return LoadedDocumentBatch(documents=documents, source=str(resolved_path), mime_type=mime_type)

    def load_from_url(
        self,
        url: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> LoadedDocumentBatch:
        """Load documents from a remote HTTP/HTTPS endpoint."""

        loader_cls = self._load_loader_class("langchain_community.document_loaders", "WebBaseLoader")
        loader = loader_cls(url)
        documents = loader.load()

        # WebBaseLoader does not expose mime type; rely on metadata hint.
        mime_type = metadata.get("mime_type") if metadata else None
        self._ensure_allowed_type(mime_type)
        self._merge_metadata(documents, metadata or {}, url)
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
        return batch

    # ------------------------------------------------------------------
    # Internal helpers
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
            loader_cls = self._load_loader_class(loaders_module, "UnstructuredHTMLLoader")
            return loader_cls(str(path))

        if self.config.use_unstructured:
            loader_cls = self._load_loader_class(loaders_module, "UnstructuredFileLoader")
            return loader_cls(str(path))

        raise UnsupportedDocumentTypeError(
            f"Unsupported document type for path={path} mime_type={mime_type}"
        )

    def _ensure_allowed_type(self, mime_type: Optional[str]) -> None:
        if self.config.allowed_mime_types and mime_type:
            allowed = set(self.config.allowed_mime_types)
            if mime_type not in allowed:
                raise UnsupportedDocumentTypeError(
                    f"MIME type {mime_type} not in allowed list"
                )

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
                f"Failed to import {attribute} from {module}. "
                "Ensure langchain-community and related extras are installed."
            ) from exc
