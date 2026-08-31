# Copyright (c) 2024 PJSC VimpelCom

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
)

from app.repositories.base import BaseQdrantRepository

logger = logging.getLogger(__name__)

DOC_COLLECTION_NAME = "user_documentation"


class DocumentationRepository(BaseQdrantRepository):

    def __init__(self, collection_name: str = DOC_COLLECTION_NAME):
        super().__init__(collection_name=collection_name)

    async def _ensure_payload_indexes(self):
        for field_name in ("path", "source_path", "doc_type", "chunk_id"):
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                logger.debug("Payload index %s: %s", field_name, e)

    @staticmethod
    def point_id(doc_path: str, chunk_index: int) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_path}#{chunk_index}"))

    async def recreate_collection(self) -> None:
        if await self.client.collection_exists(self.collection_name):
            await self.client.delete_collection(self.collection_name)
            logger.info("Коллекция удалена: %s", self.collection_name)
        await self._init_collection()
        await self._ensure_payload_indexes()

    async def delete_by_path(self, path: str) -> None:
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="path",
                            match=MatchValue(value=path),
                        )
                    ]
                )
            ),
        )

    async def upsert_chunks(self, points: list[PointStruct]) -> int:
        if not points:
            return 0
        await self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )
        return len(points)

    async def search_docs(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        try:
            query_vector = await self._generate_vector_from_text(query)
            search_results = await self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            results: list[dict[str, Any]] = []
            for item in search_results:
                payload = item.payload or {}
                meta = payload.get("metadata") or {}
                text = (
                    payload.get("data")
                    or payload.get("content")
                    or payload.get("text")
                    or payload.get("pageContent")
                )
                results.append(
                    {
                        "score": item.score,
                        "title": payload.get("title") or meta.get("title"),
                        "text": text,
                        "path": payload.get("path") or meta.get("path"),
                        "source_path": payload.get("source_path"),
                        "source_url": payload.get("source_path")
                        or meta.get("source_url")
                        or payload.get("source_url"),
                        "chunk_id": payload.get("chunk_id", meta.get("chunk_index")),
                        "doc_type": payload.get("doc_type"),
                    }
                )
            logger.info(
                "Поиск по документации: '%s' -> %s результатов",
                query,
                len(results),
            )
            return results
        except Exception as e:
            logger.error("Ошибка поиска документации для '%s': %s", query, e)
            return []


docs_repository = DocumentationRepository()
