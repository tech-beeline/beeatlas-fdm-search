# Copyright (c) 2024 PJSC VimpelCom

from urllib.parse import quote

from app.clients.auth_sso_client import AuthSSOClient
from app.core.config import settings


def create_auth_sso_client() -> AuthSSOClient | None:
    if not settings.APP_AMBASSADOR_AUTH:
        return None
    if not settings.INTEGRATION_AUTHSSO_SERVER_URL:
        raise ValueError(
            "INTEGRATION_AUTHSSO_SERVER_URL обязателен при APP_AMBASSADOR_AUTH=true"
        )
    return AuthSSOClient(settings.INTEGRATION_AUTHSSO_SERVER_URL)


async def build_amqp_url(auth_client: AuthSSOClient | None) -> str:
    host = settings.SPRING_RABBITMQ_HOST
    vhost = settings.SPRING_RABBITMQ_VIRTUAL_HOST
    if settings.APP_AMBASSADOR_AUTH:
        if auth_client is None:
            raise ValueError(
                "AuthSSOClient не инициализирован при APP_AMBASSADOR_AUTH=true"
            )
        token = await auth_client.get_token(force_refresh=True)
        password = quote(token, safe="")
        return f"amqp://:{password}@{host}/{vhost}"

    if not settings.SPRING_RABBITMQ_USERNAME or settings.SPRING_RABBITMQ_PASSWORD is None:
        raise ValueError(
            "SPRING_RABBITMQ_USERNAME и SPRING_RABBITMQ_PASSWORD обязательны при APP_AMBASSADOR_AUTH=false"
        )
    user = quote(settings.SPRING_RABBITMQ_USERNAME, safe="")
    password = quote(settings.SPRING_RABBITMQ_PASSWORD, safe="")
    return f"amqp://{user}:{password}@{host}/{vhost}"
