from __future__ import annotations

from typing import Any


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


def build_embedding_text(doc: dict) -> str:
    parts = [
        doc.get("name") or "",
        doc.get("description") or "",
        ]
    synonyms = doc.get("synonyms") or []
    actions = doc.get("actions") or []
    if synonyms:
        parts.append(" ".join(str(s) for s in synonyms))
    if actions:
        parts.append(" ".join(str(a) for a in actions))
    return " ".join(p for p in parts if p).strip()


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
