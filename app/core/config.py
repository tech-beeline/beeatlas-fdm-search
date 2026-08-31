from pydantic_settings import BaseSettings
from pathlib import Path

_app_dir = Path(__file__).parent.parent
_project_root = _app_dir.parent

_env_file = _project_root / '.env'
if (_app_dir / ".env.local").exists():
    _env_file = _app_dir / ".env.local"
    print("Using .env.local")


class Settings(BaseSettings):
    PROJECT_NAME: str = "fdm-search"
    APP_VERSION: str = "unknown"
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    RELOAD: bool = False

    QDRANT_URL: str
    QDRANT_API_KEY: str | None = None

    LLM_API_URL: str
    LLM_MODEL: str

    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str
    OPENAI_EMBEDDING_MODEL: str
    VECTOR_SIZE: int = 1536

    RABBITMQ_HOST: str
    RABBITMQ_VIRTUAL_HOST: str
    TECH_CAPABILITY_QUEUE: str
    RABBITMQ_EXCHANGE: str
    RABBITMQ_ROUTING_KEY: str
    INTEGRATION_AUTHSSO_SERVER_URL: str
    INTEGRATION_CAPABILITY_SERVER_URL: str
    BUSINESS_CAPABILITY_QUEUE: str


    DOC_CHUNK_SIZE: int
    DOC_CHUNK_OVERLAP: int
    DOC_SERVICE_URL: str

settings = Settings(_env_file=_env_file, _env_file_encoding='utf-8')
