# Copyright (c) 2024 PJSC VimpelCom

import asyncio
import json
import logging

import aio_pika

from app.clients.capability_client import capability_client
from app.clients.llm_client import LlmEnrichmentError
from app.core.config import settings
from app.core.rabbitmq import build_amqp_url, create_auth_sso_client
from app.models.schemas import RabbitMQMessage
from app.services.bc_message_service import bc_message_service

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SEC = 5


class BcConsumer:

    def __init__(self):
        self.auth_client = create_auth_sso_client()
        self.connection = None
        self.channel = None
        self._reconnect_lock = asyncio.Lock()
        self._closed = False

    async def connect(self):
        await self._establish()
        auth_mode = "ambassador (SSO token)" if settings.APP_AMBASSADOR_AUTH else "username/password"
        logger.info("BC RabbitMQ consumer: prefetch_count=1, auto-reconnect, auth=%s", auth_mode)

    async def _rabbitmq_url(self) -> str:
        return await build_amqp_url(self.auth_client)

    async def _establish(self):
        if self.connection is not None and not self.connection.is_closed:
            try:
                await self.connection.close()
            except Exception:
                pass

        url = await self._rabbitmq_url()
        self.connection = await aio_pika.connect(url)
        self.connection.close_callbacks.add(self._on_connection_close)

        self.channel = await self.connection.channel()
        await self.channel.set_qos(prefetch_count=1)

        self.exchange = await self.channel.declare_exchange(
            settings.RABBITMQ_EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        self.queue = await self.channel.declare_queue(
            settings.BUSINESS_CAPABILITY_QUEUE,
            durable=True,
        )
        await self.queue.bind(
            self.exchange,
            settings.RABBITMQ_ROUTING_KEY,
        )
        await self.queue.consume(self._handle_message)
        logger.info(
            "BC RabbitMQ: очередь=%s exchange=%s routing_key=%s",
            settings.BUSINESS_CAPABILITY_QUEUE,
            settings.RABBITMQ_EXCHANGE,
            settings.RABBITMQ_ROUTING_KEY,
        )

    def _on_connection_close(self, *args, **kwargs):
        if self._closed:
            return
        logger.warning("BC RabbitMQ соединение закрыто, переподключение...")
        asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        async with self._reconnect_lock:
            if self._closed:
                return
            while not self._closed:
                try:
                    await asyncio.sleep(RECONNECT_DELAY_SEC)
                    await self._establish()
                    logger.info("BC RabbitMQ: переподключено")
                    return
                except Exception as e:
                    logger.error(
                        "BC RabbitMQ reconnect failed (%s), повтор через %s с",
                        e,
                        RECONNECT_DELAY_SEC,
                    )

    async def _handle_message(self, message: aio_pika.IncomingMessage):
        async with message.process(requeue=True):
            message_body = message.body.decode()
            if not self._is_valid_message(message_body):
                return
            rabbitmq_message = RabbitMQMessage.parse_raw(message_body)
            if rabbitmq_message.changeType == "DELETE":
                logging.info(
                    "🗑 BC DELETE: %s (ID: %s)",
                    rabbitmq_message.name,
                    rabbitmq_message.id,
                )
                await self.process_delete(rabbitmq_message)
                return

            bc_data = await capability_client.get_bc_by_id(rabbitmq_message.id)
            if not bc_data:
                logging.info(
                    "BC с id=%s не найдена в Capability",
                    rabbitmq_message.id,
                )
                return

            logging.info("Найдена BC в Capability: %s", bc_data.get("id"))
            if rabbitmq_message.changeType == "CREATE":
                logging.info(
                    "BC CREATE: %s (ID: %s)",
                    rabbitmq_message.name,
                    rabbitmq_message.id,
                )
                await self.process_create(rabbitmq_message, bc_data)
            elif rabbitmq_message.changeType == "UPDATE":
                logging.info(
                    "BC UPDATE: %s (ID: %s)",
                    rabbitmq_message.name,
                    rabbitmq_message.id,
                )
                await self._process_update(rabbitmq_message, bc_data)

    def _is_valid_message(self, message_body: str) -> bool:
        try:
            data = json.loads(message_body)
            if not data.get("id"):
                logging.error("❌ BC: нет или пустое поле id")
                return False
            if not data.get("changeType"):
                logging.error("❌ BC: нет или пустое поле changeType")
                return False
            if data["changeType"] not in ["CREATE", "UPDATE", "DELETE"]:
                logging.info("BC: игнор changeType=%s", data["changeType"])
                return False
            return True
        except Exception as e:
            logging.error(f"BC: ошибка валидации: {e}")
        return False

    async def process_delete(self, message: RabbitMQMessage):
        success = await bc_message_service.delete_by_internal_id(message)
        if success:
            logging.info(f"BC DELETE ok: {message.id}")
        else:
            logging.error(f"BC DELETE failed: {message}")
            raise RuntimeError(f"BC DELETE failed for id={message.id}")

    async def process_create(self, message: RabbitMQMessage, bc_data: dict):
        internal_id = str(message.id)
        existing = await bc_message_service.repository.find_by_internal_id(internal_id)
        try:
            if existing:
                logging.info(
                    "CREATE для existing BC id=%s -> UPDATE",
                    message.id,
                )
                success = await bc_message_service.update_document(message, bc_data)
            else:
                success = await bc_message_service.create_document(message, bc_data)
        except LlmEnrichmentError as e:
            logging.error(f"LLM/CREATE BC без записи: {e}")
            raise
        if not success:
            raise RuntimeError(f"BC CREATE failed for id={message.id}")

    async def _process_update(self, message: RabbitMQMessage, bc_data: dict):
        try:
            success = await bc_message_service.update_document(message, bc_data)
        except LlmEnrichmentError as e:
            logging.error(f"LLM/UPDATE BC без записи: {e}")
            raise
        if not success:
            raise RuntimeError(f"BC UPDATE failed for id={message.id}")
