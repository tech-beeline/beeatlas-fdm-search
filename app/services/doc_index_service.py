# Copyright (c) 2024 PJSC VimpelCom

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qdrant_client.models import PointStruct

from app.clients.embedding_client import embedding_client
from app.core.config import settings
from app.repositories.documentation import docs_repository

logger = logging.getLogger(__name__)

USER_DOCS_PREFIX = "beeatlas-docs/"


def extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped.startswith("/*"):
            continue
        if stripped.startswith("*"):
            content = stripped.lstrip("*").strip()
            if content:
                return content
        if stripped.startswith("*/"):
            break
    return fallback


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str) -> list[str]:
    size = settings.DOC_CHUNK_SIZE
    overlap = settings.DOC_CHUNK_OVERLAP
    cleaned = strip_markdown_noise(text)
    if not cleaned:
        return []
    if len(cleaned) <= size:
        return [cleaned]

    step = max(size - overlap, 1)
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start += step
    return chunks


def path_to_page_url(site_url: str, doc_path: str) -> str:
    base = site_url.rstrip("/")
    path = doc_path.replace("\\", "/").lstrip("/")
    lower = path.lower()
    if lower.endswith(".md"):
        path = path[:-3]
    elif lower.endswith(".dsl"):
        path = path[:-4]

    name = path.rsplit("/", 1)[-1].lower()
    if name in {"readme", "index"}:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        path = parent

    parts = [quote(part, safe="") for part in path.split("/") if part]
    if parts:
        return f"{base}/{'/'.join(parts)}/"
    return f"{base}/"


def doc_type_for_path(doc_path: str) -> str:
    normalized = doc_path.replace("\\", "/")
    if normalized.lower().endswith(".dsl"):
        return "dsl_example"
    if normalized.startswith(USER_DOCS_PREFIX):
        return "user_docs"
    return "ptr_docs"


def doc_id_for_path(doc_path: str) -> str:
    return hashlib.sha1(doc_path.encode("utf-8")).hexdigest()[:16]


def point_id(doc_path: str, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_path}#{chunk_index}"))


def _safe_arcname(name: str) -> str | None:
    name = name.replace("\\", "/").lstrip("/")
    if not name or name.endswith("/"):
        return None
    parts = [p for p in name.split("/") if p]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _read_zip_entries(zip_bytes: bytes) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            arcname = _safe_arcname(info.filename)
            if not arcname:
                continue
            lower = arcname.lower()
            if not (lower.endswith(".md") or lower.endswith(".dsl")):
                continue
            raw = archive.read(info)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
                logger.warning("Файл %s прочитан с заменой невалидных utf-8 символов", arcname)
            entries.append((arcname, text))
    return entries


class DocIndexService:
    async def reindex_from_zip(self, zip_bytes: bytes) -> dict[str, Any]:
        site_url = (settings.DOC_SERVICE_URL or "").strip()
        if not site_url:
            raise ValueError("DOC_SERVICE_URL не задан")

        if not zip_bytes:
            raise ValueError("Пустой zip")

        entries = _read_zip_entries(zip_bytes)
        if not entries:
            raise ValueError("В zip нет .md/.dsl файлов")

        started = time.perf_counter()
        logger.info(
            "Реиндексация docs: файлов=%s DOC_SERVICE_URL=%s",
            len(entries),
            site_url,
        )

        await docs_repository.recreate_collection()
        logger.info("Коллекция %s пересоздана", docs_repository.collection_name)

        total_chunks = 0
        errors = 0
        indexed_files = 0

        for i, (doc_path, raw) in enumerate(entries, start=1):
            logger.info("[%s/%s] %s", i, len(entries), doc_path)
            try:
                chunks_written = await self._index_text(doc_path, raw, site_url)
                total_chunks += chunks_written
                if chunks_written:
                    indexed_files += 1
            except Exception:
                errors += 1
                logger.exception("Ошибка индексации %s", doc_path)

        elapsed = time.perf_counter() - started
        result = {
            "success": errors == 0,
            "files_in_zip": len(entries),
            "files_indexed": indexed_files,
            "chunks": total_chunks,
            "errors": errors,
            "elapsed_sec": round(elapsed, 1),
        }
        logger.info("Реиндексация docs завершена: %s", result)
        if errors:
            raise RuntimeError(
                f"Реиндексация завершена с ошибками: {errors} из {len(entries)}"
            )
        return result

    async def _index_text(self, doc_path: str, raw: str, site_url: str) -> int:
        source_url = path_to_page_url(site_url, doc_path)
        title = extract_title(raw, fallback=Path(doc_path).stem)
        doc_type = doc_type_for_path(doc_path)

        if doc_type == "dsl_example":
            chunks = [f"{title}\n\nФайл: {doc_path}\n\n{raw[:2000]}".strip()]
        else:
            chunks = chunk_text(raw)
        if not chunks:
            logger.warning("Пустой файл после очистки — пропуск: %s", doc_path)
            return 0

        points: list[PointStruct] = []
        for idx, text in enumerate(chunks):
            vector = await embedding_client.embed_text_async(text)
            points.append(
                PointStruct(
                    id=point_id(doc_path, idx),
                    vector=vector,
                    payload={
                        "data": text,
                        "text": text,
                        "pageContent": text,
                        "source_path": source_url,
                        "path": doc_path,
                        "title": title,
                        "name": title,
                        "chunk_id": idx,
                        "doc_type": doc_type,
                        "doc_id": doc_id_for_path(doc_path),
                    },
                )
            )

        upserted = await docs_repository.upsert_chunks(points)
        logger.info(
            "OK %s | chunks=%s | doc_type=%s | %s",
            doc_path,
            upserted,
            doc_type,
            source_url,
        )
        return upserted


doc_index_service = DocIndexService()
