# Copyright (c) 2024 PJSC VimpelCom

import logging
from datetime import datetime
from typing import Any, List, Optional

from app.clients.llm_client import LlmEnrichmentError, llm_client
from app.models.schemas import RabbitMQMessage
from app.repositories.business_capability import bc_repository
from app.services.bc_payload import extract_bc_fields, parse_is_domain, parse_parent_codes

logger = logging.getLogger(__name__)


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
        code_value = (payload.get("code") or "").strip()
        if code_value:
            by_code[code_value] = item

    reranked: list[dict] = []
    seen: set[str] = set()
    for code_value in ordered_codes:
        if code_value in by_code and code_value not in seen:
            reranked.append(by_code[code_value])
            seen.add(code_value)
        if len(reranked) >= limit:
            return reranked[:limit]

    # fallback: дополняем оставшимися кандидатами (в их исходном порядке)
    for item in results:
        payload = item.get("payload") or {}
        code_value = (payload.get("code") or "").strip()
        if code_value and code_value not in seen:
            reranked.append(item)
            seen.add(code_value)
        if len(reranked) >= limit:
            break
    return reranked[:limit]


class BcMessageService:
    def __init__(self):
        self.repository = bc_repository

    async def get_all_documents(self) -> List[dict[str, Any]]:
        try:
            return await self.repository.get_all_documents()
        except Exception as e:
            logger.error(f"❌ Ошибка получения BC: {e}")
            return []

    async def delete_all_documents(self) -> bool:
        try:
            return await self.repository.delete_all_documents()
        except Exception as e:
            logger.error(f"❌ Ошибка удаления всех BC: {e}")
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
            logger.error(f"❌ Ошибка DELETE BC internal_id={message.id}: {e}")
            return False

    async def _enrich_with_llm(self, bc_data: dict) -> tuple[list[str], list[str]]:
        return await llm_client.generate_synonyms_and_actions_async(
            code=bc_data.get("code") or "",
            name=bc_data.get("name") or "",
            description=bc_data.get("description") or "",
        )

    async def create_document(self, message: RabbitMQMessage, bc_data: dict) -> bool:
        try:
            internal_id = str(message.id)
            existing = await self.repository.find_by_internal_id(internal_id)
            if existing:
                logger.info(
                    "CREATE BC для existing internal_id=%s -> UPDATE",
                    internal_id,
                )
                return await self.update_document(message, bc_data)

            synonyms, actions = await self._enrich_with_llm(bc_data)
            if not synonyms or not actions:
                raise LlmEnrichmentError(
                    f"Пустые synonyms/actions для BC internal_id={message.id}"
                )

            name = bc_data.get("name") or ""
            document_data = {
                "entity_type": "business_capability",
                "internal_id": internal_id,
                "name": name,
                "name_lower": name.lower() if name else "",
                "description": bc_data.get("description") or "",
                "code": bc_data.get("code") or "",
                "synonyms": synonyms,
                "actions": actions,
                **extract_bc_fields(bc_data),
                "created_at": datetime.now().isoformat(),
            }
            logger.info(
                "Создание BC: internal_id=%s code=%s",
                message.id,
                bc_data.get("code") or "",
                )
            success = await self.repository.add_documents([document_data])
            if success:
                logger.info(f"✅ Создан BC: {message.id}")
            return success
        except LlmEnrichmentError:
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка создания BC: {e}")
            raise

    async def update_document(self, message: RabbitMQMessage, bc_data: dict) -> bool:
        try:
            internal_id = str(message.id)
            existing = await self.repository.find_by_internal_id(internal_id)
            if not existing:
                created = await self.create_document(message, bc_data)
                if created:
                    logger.info(f"✅ Создан BC при UPDATE: {message.id}")
                return created

            existing_payload = existing.payload or {}
            name = bc_data.get("name") or ""
            description = bc_data.get("description") or ""
            content_changed = name != (existing_payload.get("name") or "") or description != (
                    existing_payload.get("description") or ""
            )
            needs_llm = content_changed or not (
                    existing_payload.get("synonyms") and existing_payload.get("actions")
            )

            updated_data: dict[str, Any] = {
                "name": name,
                "name_lower": name.lower() if name else "",
                "description": description,
                "code": bc_data.get("code") or "",
                **extract_bc_fields(bc_data),
                "updated_date": datetime.now().isoformat(),
            }

            if needs_llm:
                synonyms, actions = await self._enrich_with_llm(bc_data)
                if not synonyms or not actions:
                    raise LlmEnrichmentError(
                        f"Пустые synonyms/actions для BC internal_id={internal_id}"
                    )
                updated_data["synonyms"] = synonyms
                updated_data["actions"] = actions
                logger.info(
                    "Обновление BC: internal_id=%s — synonyms/actions пересобраны",
                    internal_id,
                )
            else:
                logger.info(
                    "Обновление BC без LLM (internal_id=%s)",
                    internal_id,
                )

            success = await self.repository.update_document_by_internal_id(
                internal_id=internal_id,
                updated_data=updated_data,
                regenerate_vector=needs_llm,
            )
            if success:
                logger.info(f"✅ Обновлён BC: {message.id}")
            return success
        except LlmEnrichmentError:
            raise
        except Exception as e:
            logger.error(f"❌ Ошибка обновления BC: {e}")
            raise

    async def search_documents(
            self,
            query: Optional[str] = None,
            code: Optional[str] = None,
            parent: Optional[str] = None,
            is_domain: Optional[bool | str] = None,
            limit: int = 10,
            llm_rerank: bool = False,
    ) -> List[dict]:
        try:
            parents = parse_parent_codes(parent)
            domain_filter = parse_is_domain(is_domain)

            if code and code.strip():
                results = await self.repository.find_by_code(
                    code=code.strip(),
                    parents=parents,
                    is_domain=domain_filter,
                )
                logger.info(f"✅ BC поиск по code='{code}' -> {len(results)}")
                return results

            if not query or not query.strip():
                logger.warning("Пустой поисковый запрос BC")
                return []

            q = query.strip()
            search_limit = _candidate_limit_for_rerank(limit) if llm_rerank else limit
            results = await self.repository.search_for_similar_documents(
                query=q,
                parents=parents,
                is_domain=domain_filter,
                entity_type="business_capability",
                limit=search_limit,
            )
            logger.info(f"✅ BC поиск: '{q}' -> {len(results)}")

            if llm_rerank and results:
                ordered_codes = await llm_client.rerank_search_results_async(
                    query=q,
                    candidates=results,
                    limit=limit,
                )
                if ordered_codes:
                    results = _apply_rerank(results, ordered_codes, limit)
                    logger.info(
                        "✅ BC поиск с LLM rerank: '%s' -> %s результатов (кандидатов было %s)",
                        q,
                        len(results),
                        search_limit,
                    )
                    return results[:limit]

            return results[:limit]
        except Exception as e:
            logger.error(f"❌ Ошибка поиска BC: {e}")
            return []


bc_message_service = BcMessageService()
