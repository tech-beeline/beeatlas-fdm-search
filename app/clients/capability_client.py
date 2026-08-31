import logging
from typing import Optional

import aiohttp

from app.core.config import settings

logger = logging.getLogger(__name__)


class CapabilityClient:
    def __init__(self):
        self.base_url = settings.INTEGRATION_CAPABILITY_SERVER_URL

    async def get_tc_by_id(self, tc_id: int) -> Optional[dict]:
        if not tc_id:
            logger.debug("TC ID пустой — возврат None")
            return None
        try:
            url = f"{self.base_url}/api/v1/tech-capabilities/{tc_id}/for-search"
            logger.info(f"Запрос к сервису: {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    logger.info(f"Статус ответа: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        return data
                    if response.status == 404:
                        logger.warning("⚠️TC %s не найдена в Capability (404)", tc_id)
                        return None
                    logger.error(
                        "❌Ошибка HTTP %s при получении TC %s",
                        response.status,
                        tc_id,
                    )
                    return None
        except Exception as e:
            logger.error(f"Ошибка при получении TC {tc_id}: {e}")
            return None

    async def get_bc_by_id(self, bc_id: int) -> Optional[dict]:
        if not bc_id:
            logger.debug("BC ID пустой — возврат None")
            return None
        try:
            url = f"{self.base_url}/api/v1/business-capability/{bc_id}/for-search"
            logger.info(f"Запрос к сервису: {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    logger.info(f"Статус ответа BC: {response.status}")
                    if response.status == 200:
                        return await response.json()
                    if response.status == 404:
                        logger.warning("BC %s не найдена в Capability (404)", bc_id)
                        return None
                    logger.error(
                        "Ошибка HTTP %s при получении BC %s",
                        response.status,
                        bc_id,
                    )
                    return None

        except Exception as e:
            logger.error(f"Ошибка при получении BC {bc_id}: {e}")
        return None


capability_client = CapabilityClient()
