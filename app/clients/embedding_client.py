import asyncio
import json
import logging

import httpx
from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_INPUT_LOG_LIMIT = 500


def _truncate(text: str, limit: int = _INPUT_LOG_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... (len={len(text)})"


class EmbeddingClient:
    def __init__(self):
        self._http_client = httpx.Client(timeout=120.0)
        self._client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            http_client=self._http_client,
        )
        self._model = settings.OPENAI_EMBEDDING_MODEL
        self._embeddings_url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/embeddings"

    def embed_text(self, text: str) -> list[float]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Пустой текст для эмбеддинга")

        logger.info(
            "Embeddings request: url=%s body=%s",
            self._embeddings_url,
            json.dumps(
                {"model": self._model, "input": _truncate(normalized)},
                ensure_ascii=False,
            ),
        )

        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=normalized,
                encoding_format="float"
            )
        except Exception as e:
            logger.error(
                "Embeddings error: url=%s body=%s error_type=%s error=%s",
                self._embeddings_url,
                json.dumps(
                    {"model": self._model, "input": _truncate(normalized)},
                    ensure_ascii=False,
                ),
                type(e).__name__,
                e,
                exc_info=True,
            )
            raise

        embedding = response.data[0].embedding
        usage = response.usage.model_dump() if response.usage else None
        logger.info(
            "Embeddings response: dimension=%s index=%s usage=%s",
            len(embedding),
            response.data[0].index,
            usage,
        )
        return embedding

    async def embed_text_async(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_text, text)


embedding_client = EmbeddingClient()