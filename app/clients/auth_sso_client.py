# Copyright (c) 2024 PJSC VimpelCom

import logging
import time
from typing import Optional
import aiohttp
import jwt

logger = logging.getLogger(__name__)


TOKEN_REFRESH_MARGIN_SEC = 60


class AuthSSOClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self._access_token: Optional[str] = None
        self._expires_at: Optional[float] = None

    def invalidate(self) -> None:
        self._access_token = None
        self._expires_at = None

    async def get_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            self.invalidate()
        if self._access_token is None or self._is_token_expired():
            self._access_token = await self._obtain_access_token()
            self._set_token_expiry()
        return self._access_token

    def _is_token_expired(self) -> bool:
        if self._expires_at is None:
            return True
        return time.time() >= (self._expires_at - TOKEN_REFRESH_MARGIN_SEC)

    def _set_token_expiry(self):
        decoded_token = jwt.decode(self._access_token, options={"verify_signature": False})
        exp_timestamp = decoded_token.get("exp")

        if not exp_timestamp:
            raise ValueError("No 'exp' field in token")

        self._expires_at = exp_timestamp
        left = int(self._expires_at - time.time())
        logger.info("SSO токен истекает через %s с", left)

    async def _obtain_access_token(self) -> str:
        logger.info("Получение SSO токена")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    self.server_url,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
            ) as response:
                response.raise_for_status()
                data = await response.json()
                access_token = data.get("access_token")
                if not access_token:
                    raise ValueError("Нет access_token в ответе")
                logger.info("Токен SSO успешно получен")
                return access_token
