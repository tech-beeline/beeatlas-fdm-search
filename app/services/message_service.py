import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.clients.llm_client import LlmEnrichmentError, llm_client
from app.models.schemas import RabbitMQMessage
from app.repositories.tech_capability import tc_repository
from app.services.tc_payload import (
    extract_parent_fields,
    extract_system_fields,
    parse_parent_codes,
    parse_exclude_systems,
)

logger = logging.getLogger(__name__)

# Сколько кандидатов брать из Qdrant перед LLM rerank
LLM_RERANK_CANDIDATE_MIN = 10
LLM_RERANK_CANDIDATE_MAX = 20
LLM_RERANK_CANDIDATE_MULTIPLIER = 3


def _candidate_limit_for_rerank(limit: int) -> int:
    return min(
        max(limit * LLM_RERANK_CANDIDATE_MULTIPLIER, LLM_RERANK_CANDIDATE_MIN),
        LLM_RERANK_CANDIDATE_MAX,
    )


def _apply_rerank(
        results: list[dict],
        ordered_codes: list[str],
        limit: int,
) -> list[dict]:
    by_code: dict[str, dict] = {}
    for item in results:
        payload = item.get("payload") or {}
        code = (payload.get("code") or "").strip()
        if code:
            by_code[code] = item

    reranked: list[dict] = []
    seen: set[str] = set()
    for code in ordered_codes:
        if code in by_code and code not in seen:
            reranked.append(by_code[code])
            seen.add(code)
        if len(reranked) >= limit:
            return reranked[:limit]

    for item in results:
        payload = item.get("payload") or {}
        code = (payload.get("code") or "").strip()
        if code and code not in seen:
            reranked.append(item)
            seen.add(code)
        if len(reranked) >= limit:
            break
    return reranked[:limit]


class MessageService:

    def __init__(self):
        self.repository = tc_repository

    async def get_all_documents(self) -> List[Dict[str, Any]]:
        try:
            return await self.repository.get_all_documents()
        except Exception as e:
            logger.error(f"❌ Ошибка получения документов: {e}")
            return []

    async def delete_all_documents(self) -> bool:
        try:
            return await self.repository.delete_all_documents()
        except Exception as e:
            logger.error(f"❌ Ошибка удаления документов: {e}")
            return False

    async def delete_by_internal_id(self, message: RabbitMQMessage) -> bool:
        try:
            internal_id = str(message.id)
            result = await self.repository.delete_document_by_id(internal_id)
            if result.success:
                logger.info(result.message)
            else:
                logger.warning(result.message)
            return result.success
        except Exception as e:
            logger.error(f"❌ Ошибка при удалении по internal_id {internal_id}: {e}")
            return False

    async def _enrich_with_llm(self, tc_data: dict) -> tuple[list[str], list[str]]:
        return await llm_client.generate_synonyms_and_actions_async(
            code=tc_data.get("code") or "",
            name=tc_data.get("name") or "",
            description=tc_data.get("description") or "",
        )

    async def create_document(self, message: RabbitMQMessage, tc_data: dict) -> bool:
        try:
            internal_id = str(message.id)
            existing = await self.repository.find_by_internal_id(internal_id)
            if existing:
                logger.info(
                    "CREATE для existing internal_id=%s -> обрабатываем как UPDATE",
                    internal_id,
                )
                return await self.update_document(message, tc_data)

            synonyms, actions = await self._enrich_with_llm(tc_data)
            if not synonyms or not actions:
                raise LlmEnrichmentError(
                    f"Пустые synonyms/actions для internal_id={message.id}, запись в БД пропущена"
                )

            name = tc_data.get("name") or ""
            document_data = {
                "entity_type": "tech_capability",
                "internal_id": internal_id,
                "name": name,
                "name_lower": name.lower() if name else "",
                "description": tc_data.get("description") or "",
                "code": tc_data.get("code") or "",
                "synonyms": synonyms,
                "actions": actions,
                **extract_system_fields(tc_data),
                **extract_parent_fields(tc_data),
                "created_at": datetime.now().isoformat(),
            }
            logger.info(
                "Создание: internal_id=%s code=%s",
                message.id,
                tc_data.get("code") or "",
                )
            success = await self.repository.add_documents([document_data])
            if success:
                logger.info(f"✅ Создан документ: {message.id}")
            return success
        except LlmEnrichmentError:
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка создания документа: {e}")
            raise

    async def update_document(self, message: RabbitMQMessage, tc_data: dict) -> bool:
        try:
            internal_id = str(message.id)
            existing = await self.repository.find_by_internal_id(internal_id)
            if not existing:
                created = await self.create_document(message, tc_data)
                if created:
                    logger.info(f"✅ Создан новый документ при попытке обновления: {message.id}")
                return created

            existing_payload = existing.payload or {}
            name = tc_data.get("name") or ""
            description = tc_data.get("description") or ""
            name_changed = name != (existing_payload.get("name") or "")
            description_changed = description != (existing_payload.get("description") or "")
            content_changed = name_changed or description_changed
            needs_llm = content_changed or not (existing_payload.get("synonyms") and existing_payload.get("actions"))

            updated_data: dict[str, Any] = {
                "name": name,
                "name_lower": name.lower() if name else "",
                "description": description,
                "code": tc_data.get("code") or "",
                **extract_system_fields(tc_data),
                **extract_parent_fields(tc_data),
                "updated_date": datetime.now().isoformat(),
            }

            if needs_llm:
                synonyms, actions = await self._enrich_with_llm(tc_data)
                if not synonyms or not actions:
                    raise LlmEnrichmentError(
                        f"Пустые synonyms/actions для internal_id={internal_id}, обновление в БД пропущено"
                    )
                updated_data["synonyms"] = synonyms
                updated_data["actions"] = actions
                logger.info(
                    "Обновление: internal_id=%s code=%s — synonyms/actions пересобраны",
                    internal_id,
                    tc_data.get("code") or "",
                    )
            else:
                logger.info(
                    "Обновление: название/описание и synonyms не требуют LLM "
                    "(internal_id=%s)",
                    internal_id,
                )

            success = await self.repository.update_document_by_internal_id(
                internal_id=internal_id,
                updated_data=updated_data,
                regenerate_vector=needs_llm,
            )
            if success:
                logger.info(f"✅ Обновлен существующий документ: {message.id}")
            return success
        except LlmEnrichmentError:
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка обновления документа: {e}")
            raise

    async def search_documents(
            self,
            query: Optional[str] = None,
            code: Optional[str] = None,
            exclude_systems: Optional[str] = None,
            parent: Optional[str] = None,
            limit: int = 10,
            llm_rerank: bool = False,
    ) -> tuple[List[dict], bool]:
        try:
            exclude = parse_exclude_systems(exclude_systems)
            parents = parse_parent_codes(parent)

            if code and code.strip():
                results = await self.repository.find_by_code(
                    code=code.strip(),
                    exclude_systems=exclude,
                    parents=parents,
                )
                logger.info(f"✅ Поиск по code='{code}' -> {len(results)} результатов")
                return results, False

            if not query or not query.strip():
                logger.warning(" Пустой поисковый запрос")
                return [], False

            q = query.strip()
            search_limit = _candidate_limit_for_rerank(limit) if llm_rerank else limit

            results = await self.repository.search_for_similar_documents(
                query=q,
                exclude_systems=exclude,
                parents=parents,
                limit=search_limit,
            )

            if llm_rerank and results:
                ordered_codes = await llm_client.rerank_search_results_async(
                    query=q,
                    candidates=results,
                    limit=limit,
                )
                if ordered_codes:
                    results = _apply_rerank(results, ordered_codes, limit)
                    logger.info(
                        "✅ Поиск с LLM rerank: '%s' -> %s результатов (кандидатов было %s)",
                        q,
                        len(results),
                        search_limit,
                    )
                    return results, True

                logger.warning(
                    "LLM rerank не сработал для '%s', возвращаем top-%s по vector score",
                    q,
                    limit,
                )
                results = results[:limit]
                return results, False

            logger.info(f"✅ Поиск завершен: '{q}' -> {len(results)} результатов")
            return results[:limit], False
        except Exception as e:
            logger.error(f"❌ Ошибка поиска документов: {e}")
            return [], False


message_service = MessageService()
