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
from app.services.message_service import message_service

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SEC = 5


class TcConsumer:
    def __init__(self):
        self.auth_client = create_auth_sso_client()
        self.connection = None
        self.channel = None
        self._reconnect_lock = asyncio.Lock()
        self._closed = False

    async def connect(self):
        await self._establish()
        auth_mode = "ambassador (SSO token)" if settings.APP_AMBASSADOR_AUTH else "username/password"
        logger.info("RabbitMQ consumer: prefetch_count=1, auto-reconnect, auth=%s", auth_mode)

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
            settings.TECH_CAPABILITY_QUEUE,
            durable=True,
        )
        await self.queue.bind(
            self.exchange,
            settings.RABBITMQ_ROUTING_KEY,
        )
        await self.queue.consume(self._handle_message)
        logger.info("RabbitMQ: соединение установлено")

    def _on_connection_close(self, *args, **kwargs):
        if self._closed:
            return
        logger.warning("RabbitMQ соединение закрыто, переподключение...")
        asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        async with self._reconnect_lock:
            if self._closed:
                return
            while not self._closed:
                try:
                    await asyncio.sleep(RECONNECT_DELAY_SEC)
                    await self._establish()
                    logger.info("RabbitMQ: переподключено")
                    return
                except Exception as e:
                    logger.error(
                        "Не удалось переподключиться к RabbitMQ (%s), "
                        "повтор через %s с",
                        e,
                        RECONNECT_DELAY_SEC,
                    )

    async def _handle_message(self, message: aio_pika.IncomingMessage):
        # При исключении — NACK + requeue; при нормальном return — ACK
        async with message.process(requeue=True):
            message_body = message.body.decode()
            if not self._is_valid_message(message_body):
                return
            rabbitmq_message = RabbitMQMessage.parse_raw(message_body)
            if rabbitmq_message.changeType == "DELETE":
                logging.info(f"🗑 Процесс DELETE: {rabbitmq_message.name} (ID: {rabbitmq_message.id})")
                await self.process_delete(rabbitmq_message)
                return

            tc_data = await capability_client.get_tc_by_id(rabbitmq_message.id)
            if not tc_data:
                logging.info(f"TC с id: {rabbitmq_message.id} не найдена в сервисе Capability")
                return

            logging.info(f"Найдена TC в сервисе Capability: {tc_data['id']}")
            if rabbitmq_message.changeType == "CREATE":
                logging.info(f" Процесс CREATE: {rabbitmq_message.name} (ID: {rabbitmq_message.id})")
                await self.process_create(rabbitmq_message, tc_data)
            elif rabbitmq_message.changeType == "UPDATE":
                logging.info(f" Процесс UPDATE: {rabbitmq_message.name} (ID: {rabbitmq_message.id})")
                await self._process_update(rabbitmq_message, tc_data)

    def _is_valid_message(self, message_body: str) -> bool:
        try:
            data = json.loads(message_body)
            if not data.get('id'):
                logging.error("❌ Нет или пустое поле id")
                return False
            if not data.get('changeType'):
                logging.error("❌ Нет или пустое поле changeType")
                return False
            if data['changeType'] not in ["CREATE", "UPDATE", "DELETE"]:
                logging.info(f"Неверный changeType: {data['changeType']}")
                return False
            return True
        except Exception as e:
            logging.error(f"Ошибка валидации: {e}")
        return False

    async def process_delete(self, message: RabbitMQMessage):
        success = await message_service.delete_by_internal_id(message)
        if success:
            logging.info(f" Успешно удалено по ID: {message.id}")
        else:
            logging.error(f" Не удалось удалить по ID: {message}")
            raise RuntimeError(f"DELETE failed for id={message.id}")

    async def process_create(self, message: RabbitMQMessage, tc_data: dict):
        internal_id = str(message.id)
        existing = await message_service.repository.find_by_internal_id(internal_id)
        try:
            if existing:
                logging.info(
                    "⚠️ Получен CREATE для existing TC id=%s (%s) -> выполняем UPDATE",
                    message.id,
                    message.name,
                )
                success = await message_service.update_document(message, tc_data)
            else:
                success = await message_service.create_document(message, tc_data)
        except LlmEnrichmentError as e:
            logging.error(f" LLM/CREATE без записи в БД: {e}")
            raise
        if success:
            if existing:
                logging.info(f"⚠️ CREATE обработан как UPDATE для TC: {message.name}")
            else:
                logging.info(f" Создана запись TC: {message.name}")
        else:
            logging.error(f" Ошибка создания записи TC: {message.name}")
            raise RuntimeError(f"CREATE failed for id={message.id}")

    async def _process_update(self, message: RabbitMQMessage, tc_data: dict):
        try:
            success = await message_service.update_document(message, tc_data)
        except LlmEnrichmentError as e:
            logging.error(f" LLM/UPDATE без записи в БД: {e}")
            raise
        if success:
            logging.info(f" Создана/обновлена запись TC: {message.name}")
        else:
            logging.error(f" Ошибка создания/обновления записи TC: {message.name}")
            raise RuntimeError(f"UPDATE failed for id={message.id}")
