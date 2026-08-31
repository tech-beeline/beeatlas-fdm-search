# Copyright (c) 2024 PJSC VimpelCom

from __future__ import annotations

from typing import Any

from app.services.tc_payload import extract_parent_fields, parse_parent_codes


def extract_bc_fields(bc_data: dict) -> dict[str, Any]:
    is_domain = bc_data.get("isDomain")
    if is_domain is None:
        is_domain = bc_data.get("is_domain")
    return {
        "is_domain": bool(is_domain) if is_domain is not None else False,
        **extract_parent_fields(bc_data),
    }


def parse_is_domain(raw: bool | str | None) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return None


__all__ = [
    "extract_bc_fields",
    "parse_is_domain",
    "parse_parent_codes",
]
