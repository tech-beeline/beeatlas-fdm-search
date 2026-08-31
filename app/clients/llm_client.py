# Copyright (c) 2024 PJSC VimpelCom

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Ты улучшаешь описание возможностей для семантического поиска."
    'Сгенерируй ответ в строгом JSON формате с полями: "synonyms" (массив из 3-5 строк) '
    'и "actions" (массив из 2-4 строк).'
    "И synonyms, и actions — только строки, без объектов и полей title/description."
    "Правила: не повторяй одно и то же, не дублируй название сущности, "
    "используй разные формулировки, думай как пользователь, а не разработчик."
    "Верни только валидный JSON без пояснений и форматирования"
)

LLM_MAX_ATTEMPTS = 30
LLM_RETRY_DELAY_SEC = 10

SEARCH_RERANK_MAX_ATTEMPTS = 3
SEARCH_RERANK_RETRY_DELAY_SEC = 2

_RERANK_SYSTEM_PROMPT = (
    "Ты помощник по поиску сущностей платформы. "
    "По запросу пользователя выбери из списка кандидатов наиболее подходящие сущности. "
    'Верни только JSON: {"codes": ["код1", "код2", ...]} — коды в порядке убывания релевантности. '
    "Используй только коды из списка кандидатов. Если ничего не подходит — верни {\"codes\": []}."
)


class LlmEnrichmentError(Exception):
    """LLM не вернул synonyms/actions после всех попыток."""


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _to_plain_string(item: Any) -> str:
    """Приводит элемент к строке для embedding (без dict/title/description)."""
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if title and description:
            return f"{title}: {description}"
        return title or description
    return str(item).strip()


class LlmClient:
    def __init__(self):
        self._url = settings.LLM_API_URL
        self._api_key = settings.OPENAI_API_KEY
        self._model = settings.LLM_MODEL

    def generate_synonyms_and_actions(
            self,
            *,
            code: str,
            name: str,
            description: str,
    ) -> tuple[list[str], list[str]]:
        if not self._api_key:
            raise LlmEnrichmentError("OPENAI_API_KEY пустой — нельзя обогатить TC")

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f'Техническая возможность код: "{code}" '
                    f'наименование: "{name}" описание: "{description}".'
                ),
            },
        ]
        payload = {
            "messages": messages,
            "model": self._model,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(self._url, headers=headers, json=payload)

                    if response.status_code == 429:
                        logger.warning(
                            "LLM 429 code=%s попытка %s/%s — ждём %s с "
                            "(сообщение удерживается, новые из очереди не берутся)",
                            code,
                            attempt,
                            LLM_MAX_ATTEMPTS,
                            LLM_RETRY_DELAY_SEC,
                        )
                        time.sleep(LLM_RETRY_DELAY_SEC)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = _extract_json(content)
                    synonyms = [
                        s for s in (_to_plain_string(item) for item in parsed.get("synonyms", [])) if s
                    ]
                    actions = [
                        s for s in (_to_plain_string(item) for item in parsed.get("actions", [])) if s
                    ]

                    if synonyms and actions:
                        logger.info(
                            "LLM: code=%s, синонимы (%s): %s, действия (%s): %s",
                            code,
                            len(synonyms),
                            synonyms,
                            len(actions),
                            actions,
                        )
                        return synonyms, actions

                    logger.warning(
                        "LLM code=%s попытка %s/%s: пустые synonyms/actions "
                        "(syn=%s, act=%s) — ждём %s с, в БД не пишем",
                        code,
                        attempt,
                        LLM_MAX_ATTEMPTS,
                        len(synonyms),
                        len(actions),
                        LLM_RETRY_DELAY_SEC,
                    )
                    time.sleep(LLM_RETRY_DELAY_SEC)
            except LlmEnrichmentError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    "Ошибка LLM code=%s попытка %s/%s: %s — ждём %s с",
                    code,
                    attempt,
                    LLM_MAX_ATTEMPTS,
                    e,
                    LLM_RETRY_DELAY_SEC,
                )
                time.sleep(LLM_RETRY_DELAY_SEC)

        raise LlmEnrichmentError(
            f"LLM не вернул synonyms/actions для code={code} "
            f"после {LLM_MAX_ATTEMPTS} попыток: {last_error}"
        )

    def _format_rerank_candidates(self, candidates: list[dict]) -> str:
        parts: list[str] = []
        for idx, item in enumerate(candidates, start=1):
            payload = item.get("payload") or {}
            code = payload.get("code") or "—"
            name = payload.get("name") or ""
            description = payload.get("description") or ""
            synonyms = payload.get("synonyms") or []
            actions = payload.get("actions") or []
            parts.append(
                f"{idx}. Код: {code}\n"
                f"Наименование: {name}\n"
                f"Описание: {description}\n"
                f"Синонимы: {', '.join(str(s) for s in synonyms)}\n"
                f"Действия: {', '.join(str(a) for a in actions)}"
            )
        return "\n\n".join(parts)

    def rerank_search_results(
            self,
            *,
            query: str,
            candidates: list[dict],
            limit: int,
    ) -> list[str] | None:
        if not self._api_key or not candidates:
            return None

        context = self._format_rerank_candidates(candidates)
        messages = [
            {"role": "system", "content": _RERANK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Запрос пользователя: \"{query}\"\n\n"
                    f"Кандидаты:\n{context}\n\n"
                    f"Выбери до {limit} наиболее подходящих TC."
                ),
            },
        ]
        payload = {
            "messages": messages,
            "model": self._model,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(1, SEARCH_RERANK_MAX_ATTEMPTS + 1):
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(self._url, headers=headers, json=payload)

                    if response.status_code == 429:
                        logger.warning(
                            "LLM rerank 429 попытка %s/%s — ждём %s с",
                            attempt,
                            SEARCH_RERANK_MAX_ATTEMPTS,
                            SEARCH_RERANK_RETRY_DELAY_SEC,
                        )
                        time.sleep(SEARCH_RERANK_RETRY_DELAY_SEC)
                        continue

                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    parsed = _extract_json(content)
                    raw_codes = parsed.get("codes") or []
                    codes = [
                        str(code).strip()
                        for code in raw_codes
                        if code is not None and str(code).strip()
                    ]
                    if codes:
                        logger.info(
                            "LLM rerank: query=%r -> %s кодов: %s",
                            query[:80],
                            len(codes),
                            codes[:limit],
                        )
                        return codes[:limit]

                    logger.warning(
                        "LLM rerank попытка %s/%s: пустой codes — fallback на vector score",
                        attempt,
                        SEARCH_RERANK_MAX_ATTEMPTS,
                    )
                    return None
            except Exception as e:
                last_error = e
                logger.warning(
                    "LLM rerank попытка %s/%s: %s",
                    attempt,
                    SEARCH_RERANK_MAX_ATTEMPTS,
                    e,
                )
                if attempt < SEARCH_RERANK_MAX_ATTEMPTS:
                    time.sleep(SEARCH_RERANK_RETRY_DELAY_SEC)

        logger.warning("LLM rerank не удался, fallback на vector score: %s", last_error)
        return None

    async def rerank_search_results_async(
            self,
            *,
            query: str,
            candidates: list[dict],
            limit: int,
    ) -> list[str] | None:
        return await asyncio.to_thread(
            self.rerank_search_results,
            query=query,
            candidates=candidates,
            limit=limit,
        )

    async def generate_synonyms_and_actions_async(
            self,
            *,
            code: str,
            name: str,
            description: str,
    ) -> tuple[list[str], list[str]]:
        return await asyncio.to_thread(
            self.generate_synonyms_and_actions,
            code=code,
            name=name,
            description=description,
        )


llm_client = LlmClient()
