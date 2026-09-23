# Copyright (c) 2024 PJSC VimpelCom

from __future__ import annotations

import re
from typing import Any

from app.core.config import settings

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LINK_MARKERS = (
    "Ссылка на подробное описание",
    "Ссылка на описание",
    "Ссылка на",
)


def extract_system_fields(tc_data: dict) -> dict[str, Any]:
    system = tc_data.get("system") or {}
    if not isinstance(system, dict):
        system = {}
    name = system.get("name") or None
    alias = system.get("alias") or None
    system_id = system.get("id")
    return {
        "system_id": system_id,
        "system_name": name,
        "system_alias": alias,
        "system_name_lower": name.lower() if name else None,
        "system_alias_lower": alias.lower() if alias else None,
    }


def clean_description_for_dense(description: str | None, max_chars: int | None = None) -> str:
    """Очищает description только для текста dense-вектора (payload не меняем)."""
    text = description or ""
    if not text.strip():
        return ""

    text = _HTML_TAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)

    for marker in _LINK_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
            break

    text = re.sub(r"\s+", " ", text).strip()
    limit = max_chars if max_chars is not None else settings.DENSE_DESC_MAX_CHARS
    if limit > 0 and len(text) > limit:
        truncated = text[:limit]
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        text = truncated.strip()
    return text


def _join_list(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, str):
        return values.strip()
    return " ".join(str(item).strip() for item in values if item)


def build_dense_text(doc: dict) -> str:
    """Текст для dense: name + clean short description + synonyms + actions."""
    parts = [
        doc.get("name") or "",
        clean_description_for_dense(doc.get("description")),
        _join_list(doc.get("synonyms")),
        _join_list(doc.get("actions")),
    ]
    return " ".join(p for p in parts if p).strip()


def build_bm25_text(doc: dict) -> str:
    """Текст для BM25: name + code + synonyms + actions + system (TC). Без description."""
    parts = [
        doc.get("name") or "",
        doc.get("code") or "",
        _join_list(doc.get("synonyms")),
        _join_list(doc.get("actions")),
    ]
    system_name = (doc.get("system_name") or "").strip()
    system_alias = (doc.get("system_alias") or "").strip()
    if system_name:
        parts.append(system_name)
    if system_alias and system_alias.lower() != system_name.lower():
        parts.append(system_alias)
    return " ".join(p for p in parts if p).strip()


def build_embedding_text(doc: dict) -> str:
    return build_dense_text(doc)


def extract_parent_fields(tc_data: dict) -> dict[str, Any]:
    items = tc_data.get("parents") or []
    codes: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = (item.get("code") or "").strip()
        if code:
            codes.append(code)
    return {"parent_codes": codes}


def parse_exclude_systems(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def parse_parent_codes(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]
