# Copyright (c) 2024 PJSC VimpelCom

from __future__ import annotations

import logging
from typing import Any

from app.repositories.documentation import docs_repository

logger = logging.getLogger(__name__)


class DocSearchService:
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        return await docs_repository.search_docs(query=q, limit=limit)


doc_search_service = DocSearchService()
