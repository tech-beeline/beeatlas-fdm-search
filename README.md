# fdm-search

Сервис семантического поиска технических возможностей (Tech Capability) для платформы EA FDM Mart.

Индексирует сущности в [Qdrant](https://qdrant.tech/), синхронизирует данные через RabbitMQ и отдаёт HTTP API для поиска по смыслу. Эмбеддинги и обогащение (синонимы / действия) выполняются через корпоративный API Beeline.

## Возможности

- **Семантический поиск** — векторы через OpenAI-compatible API Beeline (`text-embedding-3-small`)
- **Точный поиск по `code`** и фильтр **`exclude_systems`**
- **Синхронизация** — события `CREATE` / `UPDATE` / `DELETE` из RabbitMQ
- **Интеграция с Capability** — при CREATE/UPDATE данные TC подтягиваются по ID
- **Обогащение LLM** — при создании и изменении name/description генерируются `synonyms` (3–5) и `actions` (2–4)
- **Автоинициализация Qdrant** — коллекция `tech_capability` создаётся при старте, если её нет
- **Swagger** — `/docs`, метрики Prometheus — `/actuator/prometheus`

## Архитектура

```
┌─────────────┐     CREATE/UPDATE/DELETE      ┌──────────────┐
│  RabbitMQ   │ ────────────────────────────► │  fdm-search  │
└─────────────┘                               │  (FastAPI)   │
                                              └──────┬───────┘
┌─────────────┐     GET TC by id               │      │
│ Capability  │ ◄──────────────────────────────┘      │
│   service   │                                       │
└─────────────┘                                       ▼
                                            ┌──────────────────┐
                                            │  Beeline AI API  │
                                            │  • embeddings    │
                                            │  • chat (LLM)    │
                                            └────────┬─────────┘
                                                     │
                                                     ▼
                                            ┌──────────────┐
                                            │    Qdrant    │
                                            │ tech_capability
                                            └──────────────┘
```

При старте:

1. Подключение к Qdrant, создание коллекции `tech_capability` (`size=VECTOR_SIZE`, Cosine)
2. Создание payload-индексов (`internal_id`, `code`, `system_alias_lower`, `system_name_lower`)
3. Подключение к RabbitMQ (SSO-токен)
4. Прослушивание `TECH_CAPABILITY_QUEUE`

### Обработка сообщений

| `changeType` | Действие |
|--------------|----------|
| `CREATE` | Capability → LLM (synonyms/actions) → embedding → запись в Qdrant |
| `UPDATE` | Обновление payload; LLM и пересчёт вектора — только если изменились `name` или `description` |
| `DELETE` | Удаление по `internal_id` |

Текст для embedding: `name` + `description` + `synonyms` + `actions` (одна сущность = один вектор, без чанков).

Эмбеддинги и LLM — **разные** API Beeline (v2 embeddings / v3 chat). Локальный ONNX и токенизатор не используются.

## Стек

| Компонент | Технология |
|-----------|------------|
| HTTP API | FastAPI, Uvicorn |
| Векторная БД | Qdrant (`qdrant-client`) |
| Очередь | RabbitMQ (`aio-pika`) |
| Эмбеддинги | OpenAI SDK → Beeline (`openai`) |
| LLM | httpx → Beeline chat completions |
| Метрики | `prometheus-fastapi-instrumentator` |
| Python | 3.12 |

## Структура проекта

```
fdm-search/
├── app/
│   ├── main.py                      # Точка входа, lifespan, Prometheus
│   ├── routes/routes.py             # HTTP API
│   ├── services/
│   │   ├── message_service.py       # CRUD, поиск, вызов LLM
│   │   └── tc_payload.py            # system-поля, текст для embedding
│   ├── repositories/
│   │   ├── base.py                  # Qdrant, поиск, авто-создание коллекции
│   │   └── tech_capability.py
│   ├── consumers/rabbitmq_consumer.py
│   ├── clients/
│   │   ├── embedding_client.py      # Beeline Embeddings
│   │   ├── llm_client.py            # Beeline LLM (synonyms/actions)
│   │   ├── auth_sso_client.py
│   │   └── capability_client.py
│   ├── models/schemas.py
│   └── core/
│       ├── config.py                # Settings из .env / .env.local
│       └── logging.py
├── scripts/
│   └── bulk_publish_tc.py           # Разовая заливка CREATE в очередь
├── Dockerfile
├── requirements.txt
└── pyproject.toml
```

## API

По умолчанию: `http://localhost:8080`  
Локально (`.env.local`): часто `http://127.0.0.1:8002`

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/health` | Healthcheck |
| `GET` | `/versions` | Версии зависимостей |
| `GET` | `/actuator/prometheus` | Метрики |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/documents` | Все документы |
| `GET` | `/search` | Поиск (см. ниже) |
| `GET` | `/search/{internal_id}` | Документ по `internal_id` |
| `DELETE` | `/documents` | Удалить все |
| `DELETE` | `/document/internal_id/{id}` | Удалить по `internal_id` |

### `GET /search`

Нужен **`query`** или **`code`**.

| Параметр | Описание |
|----------|----------|
| `query` | Семантический поиск |
| `code` | Точное совпадение по полю `code` |
| `exclude_systems` | Исключить системы по `system_alias` или `system_name` (через запятую) |
| `limit` | Число результатов для semantic (1–100, по умолчанию 10) |

```bash
# Семантика
curl "http://127.0.0.1:8002/search?query=цветовая%20маркировка&limit=5"

# С исключением системы
curl "http://127.0.0.1:8002/search?query=маркировка&exclude_systems=MySystem&limit=10"

# Точный code
curl "http://127.0.0.1:8002/search?code=BC-041968"
```

Пример ответа:

```json
{
  "query": "цветовая маркировка",
  "code": null,
  "exclude_systems": null,
  "limit": 5,
  "found": 2,
  "results": [
    {
      "id": "uuid",
      "score": 0.87,
      "payload": {
        "entity_type": "tech_capability",
        "internal_id": "46712",
        "name": "Цветовая маркировка критичных ресурсов",
        "description": "...",
        "code": "BC-041968",
        "synonyms": ["цветовая индикация", "метки бизнес-критичности"],
        "actions": ["Фильтровать ресурсы по цветовой метке"],
        "system_id": 1,
        "system_name": "...",
        "system_alias": "..."
      }
    }
  ]
}
```

## Переменные окружения

Приоритет: `app/.env.local` (если есть) → корневой `.env` → переменные окружения процесса.

Пример для локалки: скопировать `app/.env.local.example` → `app/.env.local`.

| Переменная | Обязательная | Описание |
|------------|:------------:|----------|
| `HOST` | нет | Хост (по умолчанию `0.0.0.0`) |
| `PORT` | нет | Порт (по умолчанию `8080`) |
| `RELOAD` | нет | Hot-reload Uvicorn |
| `QDRANT_URL` | да | URL Qdrant |
| `QDRANT_API_KEY` | нет | API-ключ Qdrant |
| `OPENAI_API_KEY` | да | Общий ключ Beeline: embeddings и LLM |
| `OPENAI_BASE_URL` | да | Базовый URL embeddings, например `https://api.ai.beeline.ru/api/v2` |
| `OPENAI_EMBEDDING_MODEL` | да | Модель эмбеддингов, например `text-embedding-3-small` |
| `VECTOR_SIZE` | нет | Размерность вектора (по умолчанию `1536`) |
| `LLM_API_URL` | да | URL chat API, например `https://api.ai.beeline.ru/api/v3/chat/completions` |
| `LLM_MODEL` | нет | Модель LLM (по умолчанию `llm-xlarge-moe-instruct`) |
| `RABBITMQ_HOST` | да | Хост RabbitMQ |
| `RABBITMQ_VIRTUAL_HOST` | да | Virtual host |
| `TECH_CAPABILITY_QUEUE` | да | Очередь |
| `RABBITMQ_EXCHANGE` | да | Exchange |
| `RABBITMQ_ROUTING_KEY` | да | Routing key |
| `INTEGRATION_AUTHSSO_SERVER_URL` | да | URL SSO-токена для RabbitMQ |
| `INTEGRATION_CAPABILITY_SERVER_URL` | да | Базовый URL Capability |

### Сообщение RabbitMQ

```json
{
  "id": 46712,
  "changeType": "CREATE",
  "name": "Цветовая маркировка критичных ресурсов",
  "source": "capability"
}
```

`id` и `changeType` обязательны. `changeType`: `CREATE` | `UPDATE` | `DELETE`.

Описание и code в очередь не передаются — сервис загружает TC из Capability по `id`.

## Запуск

### Требования

- Python 3.12
- Qdrant, RabbitMQ, Capability, SSO
- Ключ Beeline: `OPENAI_API_KEY` (embeddings + LLM)

### Установка

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / macOS

pip install -r requirements.txt
```

### Старт

```bash
set PYTHONPATH=.
python app/main.py
```

- API: `http://localhost:8080` (или порт из env)
- Swagger: `http://localhost:8080/docs`

Коллекция `tech_capability` создаётся автоматически на пустом Qdrant.

### Разовая заливка в очередь

Скрипт `scripts/bulk_publish_tc.py` публикует `CREATE` из Capability в RabbitMQ (настройки стенда задаются в самом скрипте):

```bash
python scripts/bulk_publish_tc.py --dry-run --max-pages 1
python scripts/bulk_publish_tc.py --delay 2
```

## Docker

```bash
docker build -t fdm-search .
docker run -p 8080:8080 \
  -e QDRANT_URL=http://qdrant:6333 \
  -e OPENAI_API_KEY=your-beeline-api-key \
  -e OPENAI_BASE_URL=https://api.ai.beeline.ru/api/v2 \
  -e OPENAI_EMBEDDING_MODEL=text-embedding-3-small \
  -e LLM_API_URL=https://api.ai.beeline.ru/api/v3/chat/completions \
  -e LLM_MODEL=llm-xlarge-moe-instruct \
  -e RABBITMQ_HOST=rabbitmq \
  -e RABBITMQ_VIRTUAL_HOST=dev_host \
  -e TECH_CAPABILITY_QUEUE=qdrant_tc \
  -e RABBITMQ_EXCHANGE=adv-exchange \
  -e RABBITMQ_ROUTING_KEY=adv-routing \
  -e INTEGRATION_AUTHSSO_SERVER_URL=https://sso.example.com/rabbit-token \
  -e INTEGRATION_CAPABILITY_SERVER_URL=http://capability:8085 \
  fdm-search
```

## Схема данных в Qdrant

Коллекция: `tech_capability`  
Параметры: `size=VECTOR_SIZE` (обычно 1536), `distance=Cosine`

| Поле | Описание |
|------|----------|
| `entity_type` | `tech_capability` |
| `internal_id` | ID из Capability (строка) |
| `name` / `name_lower` | Название |
| `description` | Описание |
| `code` | Код TC |
| `synonyms` | Массив строк (LLM) |
| `actions` | Массив строк (LLM) |
| `system_id` / `system_name` / `system_alias` | Система |
| `system_name_lower` / `system_alias_lower` | Для фильтра `exclude_systems` |
| `created_at` / `updated_date` | Даты ISO 8601 |

### Миграция со старой размерности (1024 → 1536)

Если коллекция создана под другую `VECTOR_SIZE`, удалите её и дайте сервису создать заново, затем переиндексируйте данные. Существующая коллекция с другим размером вектора не пересоздаётся автоматически.

## Репозиторий

```text
https://git.vimpelcom.ru/products/eafdmmart/fdm-search
```
