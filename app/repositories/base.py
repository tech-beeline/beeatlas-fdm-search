# Copyright (c) 2024 PJSC VimpelCom

import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Document,
    FieldCondition,
    Filter,
    FilterSelector,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseVectorParams,
    VectorParams,
)

from app.clients.embedding_client import embedding_client
from app.core.config import settings
from app.models.schemas import DeleteResult
from app.services.tc_payload import build_bm25_text, build_dense_text

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = "dense"
BM25_VECTOR_NAME = "bm25"
BM25_MODEL = "Qdrant/bm25"
BM25_OPTIONS = {"language": "russian"}

VectorPayload = Union[List[float], Dict[str, Any]]


class BaseQdrantRepository:
    def __init__(self, collection_name: str, *, hybrid: bool = True):
        self.client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            prefer_grpc=False,
            cloud_inference=True,
        )
        self.collection_name = collection_name
        self.hybrid = hybrid
        logger.info(
            "Репозиторий инициализирован: %s (hybrid=%s)",
            collection_name,
            hybrid,
        )

    async def initialize(self):
        try:
            await self.client.get_collections()
            logger.info(f"Qdrant подключение успешно для {self.collection_name}")
            await self._init_collection()
            await self._ensure_payload_indexes()
        except Exception as e:
            logger.error(f"Ошибка подключения к Qdrant: {e}")
            raise

    def _bm25_document(self, text: str) -> Document:
        return Document(
            text=text,
            model=BM25_MODEL,
            options=BM25_OPTIONS,
        )

    async def _init_collection(self):
        if await self.client.collection_exists(self.collection_name):
            await self._validate_existing_collection_schema()
            logger.info(f"Коллекция уже существует: {self.collection_name}")
            return

        if self.hybrid:
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: VectorParams(
                        size=settings.VECTOR_SIZE,
                        distance=Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    BM25_VECTOR_NAME: SparseVectorParams(
                        modifier=Modifier.IDF,
                    )
                },
            )
            logger.info(
                "Коллекция создана (hybrid): %s dense=%s bm25=%s",
                self.collection_name,
                settings.VECTOR_SIZE,
                BM25_VECTOR_NAME,
            )
            return

        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=settings.VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )
        logger.info(
            f"Коллекция создана: {self.collection_name} "
            f"(size={settings.VECTOR_SIZE}, distance=cosine)"
        )

    async def _validate_existing_collection_schema(self) -> None:
        info = await self.client.get_collection(self.collection_name)
        params = info.config.params
        vectors = params.vectors
        sparse = params.sparse_vectors or {}

        if self.hybrid:
            named_dense = isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors
            has_bm25 = BM25_VECTOR_NAME in sparse
            if not named_dense or not has_bm25:
                raise RuntimeError(
                    f"Коллекция {self.collection_name} не в hybrid-схеме "
                    f"(нужны named vector '{DENSE_VECTOR_NAME}' и sparse '{BM25_VECTOR_NAME}'). "
                    f"Удалите коллекцию и переиндексируйте TC/BC."
                )
            return

        if isinstance(vectors, dict) or sparse:
            raise RuntimeError(
                f"Коллекция {self.collection_name} ожидает plain dense, "
                f"но схема другая. Удалите коллекцию и переиндексируйте."
            )

    async def _ensure_payload_indexes(self):
        for field_name in (
                "internal_id",
                "code",
                "system_alias_lower",
                "system_name_lower",
                "parent_codes",
        ):
            try:
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                logger.debug("Payload index %s: %s", field_name, e)

    def _build_query_filter(
            self,
            exclude_systems: Optional[List[str]] = None,
            parents: Optional[List[str]] = None,
            is_domain: Optional[bool] = None,
            extra_must: Optional[List] = None,
    ) -> Optional[Filter]:
        must = list(extra_must or [])
        must_not = []
        parent_values = [v for v in (parents or []) if v]
        if parent_values:
            must.append(
                FieldCondition(
                    key="parent_codes",
                    match=MatchAny(any=parent_values),
                )
            )
        if is_domain is not None:
            must.append(
                FieldCondition(
                    key="is_domain",
                    match=MatchValue(value=is_domain),
                )
            )
        if exclude_systems:
            values = [v.lower() for v in exclude_systems if v]
            if values:
                must_not.extend(
                    [
                        FieldCondition(
                            key="system_alias_lower",
                            match=MatchAny(any=values),
                        ),
                        FieldCondition(
                            key="system_name_lower",
                            match=MatchAny(any=values),
                        ),
                    ]
                )
        if not must and not must_not:
            return None
        return Filter(must=must or None, must_not=must_not or None)

    def _build_exclude_systems_filter(
            self,
            exclude_systems: Optional[List[str]] = None,
            extra_must: Optional[List] = None,
    ) -> Optional[Filter]:
        return self._build_query_filter(
            exclude_systems=exclude_systems,
            extra_must=extra_must,
        )

    async def _generate_vector_from_data(self, tc_data: dict) -> VectorPayload:
        try:
            if self.hybrid:
                return await self._generate_hybrid_vectors(tc_data)
            text = build_dense_text(tc_data)
            if not text:
                raise ValueError("Пустой текст для embedding")
            return await self._generate_vector_from_text(text)
        except Exception as e:
            error_msg = f"❌ Ошибка генерации вектора: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    async def _generate_hybrid_vectors(self, doc: dict) -> Dict[str, Any]:
        dense_text = build_dense_text(doc)
        bm25_text = build_bm25_text(doc)
        if not dense_text and not bm25_text:
            raise ValueError("Пустой текст для hybrid embedding")
        if not dense_text:
            raise ValueError("Пустой dense-текст для embedding")
        if not bm25_text:
            bm25_text = dense_text
        dense_vec = await self._generate_vector_from_text(dense_text)
        return {
            DENSE_VECTOR_NAME: dense_vec,
            BM25_VECTOR_NAME: self._bm25_document(bm25_text),
        }

    async def _generate_vector_from_text(self, text: str) -> List[float]:
        try:
            return await embedding_client.embed_text_async(text)
        except Exception as e:
            error_msg = f"❌ Ошибка генерации вектора для текста: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    async def add_documents(self, documents: List[Dict[str, Any]]) -> bool:
        try:
            points = []
            for doc in documents:
                internal_id = doc.get('internal_id')
                existing = await self.find_by_internal_id(internal_id)
                if existing:
                    logger.info(f" Документ с internal_id={internal_id} уже существует.")
                    continue
                vector = await self._generate_vector_from_data(doc)
                points.append(PointStruct(id=str(uuid.uuid4()), vector=vector, payload=doc))
            if points:
                await self.client.upsert(collection_name=self.collection_name, points=points)
                logger.info(f"✅ Добавлен {len(points)} новый документ")
                return True
            else:
                logger.info(f" Документ с данным id уже существуют.")
                return False
        except Exception:
            logger.exception(f"❌ Ошибка добавления документов")
            return False

    async def find_by_internal_id(self, internal_id: str) -> Optional[PointStruct]:
        try:
            points, _ = await self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="internal_id",
                            match=MatchValue(value=internal_id)
                        )
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False
            )
            if not points:
                logger.info(f" TC с internal_id={internal_id} не найден в бд")
                return None
            return points[0]
        except Exception as e:
            logger.error(f"❌ Ошибка поиска TC с internal_id={internal_id}: {e}")
            return None

    async def find_by_code(
            self,
            code: str,
            exclude_systems: Optional[List[str]] = None,
            parents: Optional[List[str]] = None,
            is_domain: Optional[bool] = None,
    ) -> List[dict]:
        try:
            scroll_filter = self._build_query_filter(
                exclude_systems=exclude_systems,
                parents=parents,
                is_domain=is_domain,
                extra_must=[
                    FieldCondition(
                        key="code",
                        match=MatchValue(value=code),
                    )
                ],
            )
            points, _ = await self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=scroll_filter,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            return [
                {
                    "id": point.id,
                    "score": 1.0,
                    "payload": point.payload,
                }
                for point in points
            ]
        except Exception as e:
            logger.error(f"❌ Ошибка поиска по code={code}: {e}")
            return []

    async def update_document_by_internal_id(
            self,
            internal_id: str,
            updated_data: dict,
            regenerate_vector: bool = True,
    ) -> bool:
        try:
            existing_point = await self.find_by_internal_id(internal_id)
            if not existing_point:
                logger.info(f" Документ {internal_id} не найден в бд")
                return False
            updated_payload = {
                **existing_point.payload,
                **updated_data
            }
            if regenerate_vector:
                new_vector = await self._generate_vector_from_data(updated_payload)
            else:
                full_points, _ = await self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=Filter(
                        must=[
                            FieldCondition(
                                key="internal_id",
                                match=MatchValue(value=internal_id),
                            )
                        ]
                    ),
                    limit=1,
                    with_payload=True,
                    with_vectors=True,
                )
                if not full_points or full_points[0].vector is None:
                    new_vector = await self._generate_vector_from_data(updated_payload)
                else:
                    new_vector = full_points[0].vector

            await self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=existing_point.id,
                        vector=new_vector,
                        payload=updated_payload
                    )
                ]
            )
            logger.info(
                " Документ обновлен: %s (vector_regenerated=%s)",
                internal_id,
                regenerate_vector,
            )
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка обновления документа {internal_id}: {e}")
            return False

    async def get_all_documents(self) -> List[Dict[str, Any]]:
        try:
            all_documents = []
            next_offset = None
            while True:
                records, next_offset = await self.client.scroll(collection_name=self.collection_name, limit=100,
                                                                offset=next_offset, with_payload=True,
                                                                with_vectors=False)
                for record in records:
                    all_documents.append({
                        "id": record.id,
                        "payload": record.payload
                    })
                if next_offset is None:
                    break
            logger.info(f" Получено {len(all_documents)} записей из {self.collection_name}")
            return all_documents
        except Exception as e:
            logger.exception(f"❌ Ошибка получения документов из {self.collection_name}: {e}")
            return []

    async def delete_all_documents(self) -> bool:
        try:
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[]
                    )
                )
            )
            logger.info(f" Все документы удалены из коллекции {self.collection_name}")
            return True
        except Exception as e:
            logger.exception(f"❌ Ошибка удаления документов из {self.collection_name}: {e}")
            return False

    async def delete_document_by_id(self, internal_id: str) -> DeleteResult:
        try:
            existing_point = await self.find_by_internal_id(str(internal_id))
            if not existing_point:
                logger.info(
                    "Запись %s уже отсутствует в %s",
                    internal_id,
                    self.collection_name,
                )
                return DeleteResult(
                    success=True,
                    message=f"Запись {internal_id} уже отсутствует",
                )
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=[existing_point.id]
            )
            logger.info(f" Запись удалена: {internal_id}")
            return DeleteResult(success=True, message=f"Запись {internal_id} удалена")
        except Exception as e:
            logger.error(f"❌ Ошибка удаления записи {internal_id}: {e}")
            return DeleteResult(success=False, message=f"Ошибка удаления записи {internal_id}")

    def _prefetch_limit(self, limit: int) -> int:
        configured = settings.HYBRID_PREFETCH_LIMIT
        return max(configured, limit)

    async def search_for_similar_documents(
            self,
            query: str,
            entity_type: Optional[str] = None,
            exclude_systems: Optional[List[str]] = None,
            parents: Optional[List[str]] = None,
            is_domain: Optional[bool] = None,
            limit: int = 10,
    ) -> List[dict]:
        try:
            extra_must = []
            if entity_type:
                extra_must.append(
                    FieldCondition(
                        key="entity_type",
                        match=MatchValue(value=entity_type),
                    )
                )
            query_filter = self._build_query_filter(
                exclude_systems=exclude_systems,
                parents=parents,
                is_domain=is_domain,
                extra_must=extra_must or None,
            )

            if self.hybrid:
                return await self._hybrid_search(
                    query=query,
                    query_filter=query_filter,
                    limit=limit,
                )

            query_vector = await self._generate_vector_from_text(query)
            search_results = await self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=limit,
                score_threshold=settings.SEARCH_MIN_SCORE,
                with_payload=True,
                with_vectors=False,
            )
            return self._format_scored_points(search_results, query)
        except Exception as e:
            logger.error(f"❌ Ошибка поиска для запроса '{query}': {e}")
            return []

    async def _hybrid_search(
            self,
            query: str,
            query_filter: Optional[Filter],
            limit: int,
    ) -> List[dict]:
        dense_vec = await self._generate_vector_from_text(query)
        prefetch_limit = self._prefetch_limit(limit)

        dense_prefetch = Prefetch(
            query=dense_vec,
            using=DENSE_VECTOR_NAME,
            limit=prefetch_limit,
            filter=query_filter,
            score_threshold=settings.SEARCH_MIN_SCORE,
        )
        bm25_prefetch = Prefetch(
            query=self._bm25_document(query),
            using=BM25_VECTOR_NAME,
            limit=prefetch_limit,
            filter=query_filter,
        )

        response = await self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[dense_prefetch, bm25_prefetch],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        points = response.points if hasattr(response, "points") else response
        return self._format_scored_points(points, query)

    def _format_scored_points(self, search_results, query: str) -> List[dict]:
        formatted_results = []
        for result in search_results:
            formatted_results.append({
                "id": result.id,
                "score": result.score,
                "payload": result.payload,
            })
        logger.info(f" Найдено {len(formatted_results)} результатов для запроса: '{query}'")
        return formatted_results
