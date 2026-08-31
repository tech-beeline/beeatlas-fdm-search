# Copyright (c) 2024 PJSC VimpelCom

import logging
import sys
from importlib.metadata import version
from typing import Annotated, Any, Optional

from fastapi import APIRouter, File, HTTPException, Path, Query, UploadFile
from pydantic import BeforeValidator

from app.repositories.tech_capability import tc_repository
from app.services.bc_message_service import bc_message_service
from app.services.doc_index_service import doc_index_service
from app.services.doc_search_service import doc_search_service
from app.services.message_service import message_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _blank_as_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _blank_as_false(value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    return value


def _blank_as_limit(value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 10
    return value

TAG_HEALTH = "Служебные"
TAG_SEARCH = "Поиск"
TAG_DOCUMENTS = "Документы"


@router.get(
    "/health",
    tags=[TAG_HEALTH],
    summary="Проверка доступности сервиса",
    description="Возвращает статус `ok`, если приложение запущено.",
)
async def health_check():
    return {"status": "ok"}


@router.get(
    "/versions",
    tags=[TAG_HEALTH],
    summary="Версии зависимостей",
    description="Версии Python, FastAPI, qdrant-client, aio-pika и pydantic.",
)
async def get_versions():
    import fastapi
    import aio_pika
    import pydantic

    return {
        "fastapi": fastapi.__version__,
        "qdrant-client": version("qdrant-client"),
        "aio-pika": aio_pika.__version__,
        "pydantic": pydantic.__version__,
        "python": sys.version,
    }


@router.get(
    "/api/v1/all-tcs",
    tags=[TAG_DOCUMENTS],
    summary="Список всех TC в Qdrant",
    description="Возвращает все технические возможности из коллекции.",
)
async def get_all_tcs():
    results = await message_service.get_all_documents()
    return {"documents": results, "count": len(results)}


@router.get(
    "/api/v1/all-bcs",
    tags=[TAG_DOCUMENTS],
    summary="Список всех BC в Qdrant",
    description="Возвращает все бизнес-возможности из коллекции.",
)
async def get_all_bcs():
    results = await bc_message_service.get_all_documents()
    return {"documents": results, "count": len(results)}


@router.get(
    "/api/v1/search",
    tags=[TAG_SEARCH],
    summary="Общий поиск TC + BC",
    description=(
            "Семантический поиск по смыслу запроса (`query`) или точный поиск по коду (`code`). "
            "Обязателен хотя бы один из параметров `query` или `code`.\n\n"
            "**Фильтры (опционально):**\n"
            "- `parent` — код родительской BC из цепочки вверх; несколько через запятую (OR)\n"
            "- `exclude_systems` — исключить системы по alias или name (через запятую) (только для TC)\n"
            "- `is_domain` — фильтр по признаку домена (`true`/`false`) (только для BC)\n"
            "- `entity_type` — `tc`, `bc` или `tc,bc` (дефолт: `tc,bc`)\n"
            "- `limit` — число результатов для семантического поиска (1–100)\n"
            "- `llm_rerank` — если `true`: LLM rerank кандидатов (только для `query`, TC+BC)"
    ),
)
async def search_documents(
        query: Optional[str] = Query(
            None,
            description="Текстовый запрос для семантического поиска (по смыслу)",
            examples=["оплата услуг"],
        ),
        code: Optional[str] = Query(
            None,
            description="Точное совпадение по полю code сущности (TC/BC)",
            examples=["TC-12345"],
        ),
        entity_type: Optional[str] = Query(
            None,
            description="Ограничить тип: tc, bc или tc,bc. Дефолт: tc,bc",
            examples=["tc", "bc", "tc,bc"],
        ),
        exclude_systems: Optional[str] = Query(
            None,
            description="Исключить системы: alias или name, через запятую",
            examples=["LegacySys,OldCRM"],
        ),
        parent: Optional[str] = Query(
            None,
            description="Фильтр по родительским BC: code через запятую (OR). Любой BC из цепочки вверх.",
            examples=["BC-018021,CATALOG_BC"],
        ),
        is_domain: Annotated[Optional[bool], BeforeValidator(_blank_as_none)] = Query(
            None,
            description="Фильтр по is_domain (только для BC). Пустое значение = без фильтра.",
        ),
        limit: Annotated[int, BeforeValidator(_blank_as_limit)] = Query(
            10,
            description="Максимум результатов для семантического поиска",
            ge=1,
            le=100,
        ),
        llm_rerank: Annotated[bool, BeforeValidator(_blank_as_false)] = Query(
            False,
            description=(
                    "LLM rerank: выбрать лучших кандидатов через LLM."
                    "Только для семантического поиска (query)."
            ),
        ),
):
    if not (query and query.strip()) and not (code and code.strip()):
        raise HTTPException(
            status_code=400,
            detail="Укажите query (семантика) или code (точный поиск)",
        )
    try:
        normalized_entity_types: list[str]
        if not entity_type or not entity_type.strip():
            normalized_entity_types = ["tc", "bc"]
        else:
            normalized_entity_types = [
                t.strip().lower()
                for t in entity_type.split(",")
                if t and t.strip()
            ]
            allowed = {"tc", "bc"}
            if any(t not in allowed for t in normalized_entity_types):
                raise HTTPException(
                    status_code=400,
                    detail="entity_type должен быть tc, bc или tc,bc",
                )

        tc_types = set(normalized_entity_types) & {"tc"}
        bc_types = set(normalized_entity_types) & {"bc"}

        llm_rerank_applied = False
        results: list[dict] = []

        code_value = code.strip() if code and code.strip() else None
        query_value = query.strip() if query and query.strip() else None

        if code_value:
            if tc_types:
                tc_results, _ = await message_service.search_documents(
                    query=None,
                    code=code_value,
                    exclude_systems=exclude_systems,
                    parent=parent,
                    limit=limit,
                    llm_rerank=False,
                )
                for item in tc_results:
                    payload = item.get("payload") or {}
                    item["entity_type"] = payload.get("entity_type") or "tech_capability"
                results.extend(tc_results)

            if bc_types:
                bc_results = await bc_message_service.search_documents(
                    query=None,
                    code=code_value,
                    parent=parent,
                    is_domain=is_domain,
                    limit=limit,
                )
                for item in bc_results:
                    payload = item.get("payload") or {}
                    item["entity_type"] = payload.get("entity_type") or "business_capability"
                results.extend(bc_results)

            if not results:
                raise HTTPException(
                    status_code=404,
                    detail=f"Сущность с code={code_value} не найдена",
                )

            results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:limit]
            return {
                "query": query_value,
                "code": code_value,
                "entity_type": normalized_entity_types,
                "exclude_systems": exclude_systems,
                "is_domain": is_domain,
                "parent": parent,
                "limit": limit,
                "llm_rerank": llm_rerank,
                "llm_rerank_applied": llm_rerank_applied,
                "found": len(results),
                "results": results,
            }

        if not query_value:
            raise HTTPException(
                status_code=400,
                detail="Укажите query (семантика) или code (точный поиск)",
            )

        if tc_types:
            tc_results, tc_llm_rerank_applied = await message_service.search_documents(
                query=query_value,
                code=None,
                exclude_systems=exclude_systems,
                parent=parent,
                limit=limit,
                llm_rerank=llm_rerank,
            )
            llm_rerank_applied = llm_rerank_applied or tc_llm_rerank_applied
            for item in tc_results:
                payload = item.get("payload") or {}
                item["entity_type"] = payload.get("entity_type") or "tech_capability"
            results.extend(tc_results)

        if bc_types:
            bc_results = await bc_message_service.search_documents(
                query=query_value,
                code=None,
                parent=parent,
                is_domain=is_domain,
                limit=limit,
                llm_rerank=llm_rerank,
            )
            if llm_rerank:
                llm_rerank_applied = True
            for item in bc_results:
                payload = item.get("payload") or {}
                item["entity_type"] = payload.get("entity_type") or "business_capability"
            results.extend(bc_results)

        if llm_rerank:
            for idx, item in enumerate(results):
                item.setdefault("llm_rank", idx)
            results = sorted(
                results,
                key=lambda x: (x.get("llm_rank", 10**9), -x.get("score", 0)),
            )[:limit]
        else:
            results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:limit]
        return {
            "query": query_value,
            "code": None,
            "entity_type": normalized_entity_types,
            "exclude_systems": exclude_systems,
            "is_domain": is_domain,
            "parent": parent,
            "limit": limit,
            "llm_rerank": llm_rerank,
            "llm_rerank_applied": llm_rerank_applied,
            "found": len(results),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка в поисковом API: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.post(
    "/api/v1/docs/reindex",
    tags=[TAG_DOCUMENTS],
    summary="Полная переиндексация документации из zip",
    description=(
            "Пересоздаёт коллекцию `user_documentation` в Qdrant."
    ),
)
async def reindex_documentation(
        file: UploadFile = File(..., description="Zip-архив документации"),
):
    filename = (file.filename or "").lower()
    if filename and not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Ожидается файл .zip")
    try:
        zip_bytes = await file.read()
        result = await doc_index_service.reindex_from_zip(zip_bytes)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("❌ Ошибка реиндексации документации: %s", e)
        raise HTTPException(
            status_code=500,
            detail=str(e) or "Внутренняя ошибка сервера",
        ) from e


@router.get(
    "/search/docs",
    tags=[TAG_SEARCH],
    summary="Поиск по пользовательской документации",
    description=(
            "Семантический поиск по коллекции `user_documentation` (BeeAtlas user docs).\n\n"
            "В каждом результате есть `source_url` — ссылка на страницу портала, "
            "с которой взят фрагмент."
    ),
)
async def search_documentation(
        query: str = Query(
            ...,
            description="Текстовый запрос (инструкция / вопрос по UI и сущностям BeeAtlas)",
            examples=["как создать техническую возможность"],
            min_length=1,
        ),
        limit: int = Query(
            5,
            description="Максимум чанков в ответе",
            ge=1,
            le=50,
        ),
):
    try:
        results = await doc_search_service.search(query=query, limit=limit)
        return {
            "query": query,
            "limit": limit,
            "found": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"❌ Ошибка поиска документации: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@router.get(
    "/api/v1/search/{internal_id}",
    tags=[TAG_SEARCH],
    summary="Получить TC по internal_id",
    description=(
            "Возвращает одну запись из Qdrant по `internal_id` (ID TC из Capability). "
            "Используется для проверки, что TC проиндексирована после CREATE."
    ),
)
async def search_by_internal_id(
        internal_id: str = Path(
            ...,
            description="ID технической возможности в Capability (строка)",
            examples=["12345"],
        ),
):
    point = await tc_repository.find_by_internal_id(internal_id)
    if point is None:
        raise HTTPException(
            status_code=404,
            detail=f"Документ с internal_id={internal_id} не найден",
        )
    return {
        "count": 1,
        "documents": [
            {
                "id": point.id,
                "payload": point.payload,
            }
        ],
    }

@router.get(
    "/api/v1/search/bc/{internal_id}",
    tags=[TAG_SEARCH],
    summary="Получить BC по internal_id",
    description=(
            "Возвращает одну запись BC из Qdrant по `internal_id` (ID BC из Capability). "
            "Используется для проверки, что BC проиндексирована после CREATE."
    ),
)
async def search_bc_by_internal_id(
        internal_id: str = Path(
            ...,
            description="ID бизнес-возможности в Capability (строка)",
            examples=["12345"],
        ),
):
    point = await bc_message_service.repository.find_by_internal_id(internal_id)
    if point is None:
        raise HTTPException(
            status_code=404,
            detail=f"BC с internal_id={internal_id} не найдена",
        )
    return {
        "count": 1,
        "documents": [
            {
                "id": point.id,
                "payload": point.payload,
            }
        ],
    }


@router.delete(
    "/api/v1/documents",
    tags=[TAG_DOCUMENTS],
    summary="Удалить все TC из Qdrant",
    description="Полная очистка коллекции.",
)
async def delete_all_documents():
    success = await message_service.delete_all_documents()
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка удаления документов")
    return {"success": True, "message": "Все документы удалены"}

@router.delete(
    "/api/v1/bc-documents",
    tags=[TAG_DOCUMENTS],
    summary="Удалить все BC из Qdrant",
    description="Полная очистка коллекции BC.",
)
async def delete_all_bc_documents():
    success = await bc_message_service.delete_all_documents()
    if not success:
        raise HTTPException(status_code=500, detail="Ошибка удаления BC")
    return {"success": True, "message": "Все BC удалены"}

@router.delete(
    "/api/v1/document/internal_id/{id}",
    tags=[TAG_DOCUMENTS],
    summary="Удалить TC по internal_id",
    description="Удаляет одну запись из Qdrant по ID TC из Capability.",
)
async def delete_by_internal_id(
        id: str = Path(..., description="ID технической возможности в Capability"),
):
    result = await tc_repository.delete_document_by_id(id)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.message)
    return result.message
