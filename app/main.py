from contextlib import asynccontextmanager
import logging
from app.repositories.business_capability import bc_repository
from app.repositories.documentation import docs_repository
from app.repositories.tech_capability import tc_repository
import uvicorn
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from app.consumers.bc_rabbitmq_consumer import BcConsumer
from app.consumers.rabbitmq_consumer import TcConsumer
from app.routes.routes import router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info(f" Host: {settings.HOST}")
    logging.info(f" Port: {settings.PORT}")
    logging.info(f" Reload: {settings.RELOAD}")
    logging.info(" Инициализация Qdrant репозиториев...")
    await tc_repository.initialize()
    await bc_repository.initialize()
    await docs_repository.initialize()
    logging.info(" Qdrant репозитории инициализированы")
    tc_consumer = TcConsumer()
    await tc_consumer.connect()
    app.state.tc_consumer = tc_consumer
    bc_consumer = BcConsumer()
    await bc_consumer.connect()
    app.state.bc_consumer = bc_consumer
    logging.info("Application started")
    yield

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.APP_VERSION,
        description=(
            "Сервис семантического поиска TC, BC и пользовательской документации BeeAtlas.\n\n"
            "TC: `/api/v1/search` — parent, exclude_systems.\n"
            "BC: `/search/bc` — parent, is_domain.\n"
            "Документация: `/search/docs`."
        ),
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "Поиск",
                "description": "Семантический поиск TC, BC и документации",
            },
            {
                "name": "Документы",
                "description": "Просмотр и удаление записей TC в Qdrant",
            },
            {
                "name": "Служебные",
                "description": "Health-check и версии компонентов",
            },
        ],
    )
    app.include_router(router)
    instrumentator = Instrumentator()
    instrumentator.instrument(app)
    instrumentator.expose(app, endpoint="/actuator/prometheus")
    return app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        access_log=False
    )
