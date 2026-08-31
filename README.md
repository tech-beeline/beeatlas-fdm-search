<!-- Copyright (c) 2024 PJSC VimpelCom -->

# fdm-search

Сервис семантического поиска **Tech Capability (TC)**, **Business Capability (BC)** и **пользовательской документации** для платформы capability management.

Индексирует сущности в Qdrant, синхронизирует TC и BC через RabbitMQ и отдаёт HTTP API для поиска по смыслу. Эмбеддинги и обогащение (synonyms / actions) выполняются через OpenAI-compatible API.

## Возможности

- **Семантический поиск TC и BC** — единый эндпоинт `/api/v1/search` с фильтрами `entity_type`, `parent`, `exclude_systems` (TC), `is_domain` (BC)
- **Порог релевантности** — `SEARCH_MIN_SCORE`: результаты ниже порога не возвращаются (`found: 0`)
- **LLM rerank** — опциональное переранжирование кандидатов по `query` (`llm_rerank=true`)
- **Точный поиск по `code`** — TC и BC в одном запросе
- **Синхронизация TC и BC** — события `CREATE` / `UPDATE` / `DELETE` из RabbitMQ
- **Интеграция с Capability** — при CREATE/UPDATE данные подтягиваются по ID
- **Обогащение LLM** — synonyms (3–5) и actions (2–4) при создании и изменении name/description
- **Поиск по документации** — коллекция `user_documentation`, чанки с `source_url`
- **Переиндексация доков** — `POST /api/v1/docs/reindex` (multipart zip)
- **Автоинициализация Qdrant** — коллекции создаются при старте, если их нет
- **Swagger** — `/docs`, метрики Prometheus — `/actuator/prometheus`

## Архитектура

```
┌─────────────┐   CREATE/UPDATE/DELETE    ┌──────────────┐
│  RabbitMQ   │ ────────────────────────► │  fdm-search  │
│  qdrant_tc  │                           │  (FastAPI)   │
│  qdrant_bc  │                           └──────┬───────┘
└─────────────┘                                  │
                                                 │ zip (Job)
┌─────────────┐   GET TC/BC by id                │
│ Capability  │ ◄────────────────────────────────┤
│   service   │                                  │
└─────────────┘                                  ▼
                                        ┌──────────────────┐
                                        │  Embeddings / LLM │
                                        │  (OpenAI-compatible)
                                        └────────┬─────────┘
                                                 │
                                                 ▼
                                        ┌──────────────────┐
                                        │      Qdrant      │
                                        │ tech_capability  │
                                        │ business_capability
                                        │ user_documentation
                                        └──────────────────┘
```

При старте:

1. Подключение к Qdrant, создание коллекций `tech_capability`, `business_capability`, `user_documentation`
2. Создание payload-индексов для фильтров и поиска
3. Подключение к RabbitMQ (SSO-токен), два consumer: TC и BC
4. Прослушивание очередей `TECH_CAPABILITY_QUEUE` и `BUSINESS_CAPABILITY_QUEUE`

### Обработка сообщений RabbitMQ

| `changeType` | TC | BC |
|--------------|----|----|
| `CREATE` | Capability → LLM → embedding → Qdrant | то же |
| `UPDATE` | LLM и пересчёт вектора — только если изменились `name` или `description` | то же |
| `DELETE` | Удаление по `internal_id` | то же |

Текст для embedding TC/BC: `name` + `description` + `synonyms` + `actions` (одна сущность = один вектор).

### Документация

Доки не приходят через RabbitMQ. Zip с `.md` / `.dsl` загружается через `POST /api/v1/docs/reindex`: сервис пересоздаёт коллекцию `user_documentation`, режет текст на чанки (`DOC_CHUNK_SIZE` / `DOC_CHUNK_OVERLAP`) и строит `source_url` из `DOC_SERVICE_URL`.

## Стек

| Компонент | Технология |
|-----------|------------|
| HTTP API | FastAPI, Uvicorn |
| Векторная БД | Qdrant (`qdrant-client`) |
| Очередь | RabbitMQ (`aio-pika`) |
| Эмбеддинги | OpenAI SDK (`openai`) |
| LLM | httpx → chat completions API |
| Метрики | `prometheus-fastapi-instrumentator` |
| Python | 3.12 |

## Структура проекта

```
fdm-search/
├── app/
│   ├── main.py                          # Точка входа, lifespan, Prometheus
│   ├── routes/routes.py                 # HTTP API
│   ├── services/
│   │   ├── message_service.py           # TC: CRUD, поиск, LLM
│   │   ├── bc_message_service.py        # BC: CRUD, поиск, LLM
│   │   ├── tc_payload.py                # system-поля, текст для embedding
│   │   ├── bc_payload.py                # is_domain, parent_codes
│   │   ├── doc_index_service.py         # zip → чанки → Qdrant
│   │   └── doc_search_service.py        # поиск по user_documentation
│   ├── repositories/
│   │   ├── base.py                      # Qdrant, поиск, score_threshold
│   │   ├── tech_capability.py
│   │   ├── business_capability.py
│   │   └── documentation.py
│   ├── consumers/
│   │   ├── rabbitmq_consumer.py         # TC
│   │   └── bc_rabbitmq_consumer.py      # BC
│   ├── clients/
│   │   ├── embedding_client.py
│   │   ├── llm_client.py
│   │   ├── auth_sso_client.py
│   │   └── capability_client.py
│   ├── models/schemas.py
│   └── core/
│       ├── config.py
│       └── logging.py
├── docs/
│   └── TZ_BC_Qdrant.md                  # ТЗ по индексации BC
├── certs/                               # CA для TLS (Docker, опционально)
├── Dockerfile
├── requirements.txt
└── pyproject.toml
```

## API

По умолчанию: `http://localhost:8080`  
Локально (`app/.env.local`): часто `http://127.0.0.1:8002`

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/health` | Healthcheck |
| `GET` | `/versions` | Версии зависимостей |
| `GET` | `/actuator/prometheus` | Метрики |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/api/v1/search` | Поиск TC + BC |
| `GET` | `/search/docs` | Семантический поиск по документации |
| `POST` | `/api/v1/docs/reindex` | Полная переиндексация доков из zip |
| `GET` | `/api/v1/all-tcs` | Все TC в Qdrant |
| `GET` | `/api/v1/all-bcs` | Все BC в Qdrant |
| `GET` | `/api/v1/search/{internal_id}` | TC по `internal_id` |
| `GET` | `/api/v1/search/bc/{internal_id}` | BC по `internal_id` |
| `DELETE` | `/api/v1/documents` | Удалить все TC |
| `DELETE` | `/api/v1/bc-documents` | Удалить все BC |
| `DELETE` | `/api/v1/document/internal_id/{id}` | Удалить TC по `internal_id` |

### `GET /api/v1/search`

Нужен **`query`** или **`code`**.

| Параметр | Описание |
|----------|----------|
| `query` | Семантический поиск |
| `code` | Точное совпадение по полю `code` (TC или BC) |
| `entity_type` | `tc`, `bc` или `tc,bc` (по умолчанию `tc,bc`) |
| `parent` | Код родительской BC из цепочки вверх; несколько через запятую (OR) |
| `exclude_systems` | Исключить системы по alias/name (только TC) |
| `is_domain` | Фильтр по признаку домена (только BC) |
| `limit` | Число результатов для semantic (1–100, по умолчанию 10) |
| `llm_rerank` | LLM rerank кандидатов (только для `query`) |

```bash
# Семантика TC + BC
curl "http://127.0.0.1:8002/api/v1/search?query=оплата%20услуг&limit=5"

# Только BC, домены
curl "http://127.0.0.1:8002/api/v1/search?query=каталог&entity_type=bc&is_domain=true"

# Точный code
curl "http://127.0.0.1:8002/api/v1/search?code=BC-041968"
```

### `GET /search/docs`

| Параметр | Описание |
|----------|----------|
| `query` | Текстовый запрос (обязательный) |
| `limit` | Число чанков (1–50, по умолчанию 5) |

```bash
curl "http://127.0.0.1:8002/search/docs?query=как%20создать%20техническую%20возможность&limit=5"
```

### `POST /api/v1/docs/reindex`

Multipart: поле `file` — zip с `.md` / `.dsl`. Пересоздаёт коллекцию `user_documentation`.

```bash
curl -X POST "http://127.0.0.1:8002/api/v1/docs/reindex" \
  -F "file=@docs.zip"
```

## Переменные окружения

Приоритет: `app/.env.local` (если есть) → корневой `.env` → переменные процесса.

Пример для локалки: скопировать `app/.env.local.example` → `app/.env.local`.

| Переменная | Обязательная | Описание |
|------------|:------------:|----------|
| `HOST` | нет | Хост (по умолчанию `0.0.0.0`) |
| `PORT` | нет | Порт (по умолчанию `8080`) |
| `RELOAD` | нет | Hot-reload Uvicorn |
| `QDRANT_URL` | да | URL Qdrant |
| `QDRANT_API_KEY` | нет | API-ключ Qdrant |
| `OPENAI_API_KEY` | да | API-ключ для embeddings и LLM |
| `OPENAI_BASE_URL` | да | Базовый URL embeddings |
| `OPENAI_EMBEDDING_MODEL` | да | Модель эмбеддингов |
| `VECTOR_SIZE` | нет | Размерность вектора (по умолчанию `1536`) |
| `LLM_API_URL` | да | URL chat API |
| `LLM_MODEL` | да | Модель LLM |
| `RABBITMQ_HOST` | да | Хост RabbitMQ |
| `RABBITMQ_VIRTUAL_HOST` | да | Virtual host |
| `TECH_CAPABILITY_QUEUE` | да | Очередь TC |
| `BUSINESS_CAPABILITY_QUEUE` | да | Очередь BC |
| `RABBITMQ_EXCHANGE` | да | Exchange |
| `RABBITMQ_ROUTING_KEY` | да | Routing key |
| `INTEGRATION_AUTHSSO_SERVER_URL` | да | URL SSO-токена для RabbitMQ |
| `INTEGRATION_CAPABILITY_SERVER_URL` | да | Базовый URL Capability |
| `DOC_SERVICE_URL` | да | Базовый URL портала документации (для `source_url` в Qdrant) |
| `DOC_CHUNK_SIZE` | да | Размер чанка документации |
| `DOC_CHUNK_OVERLAP` | да | Перекрытие чанков |
| `SEARCH_MIN_SCORE` | да | Минимальный cosine score для TC/BC; ниже — `found: 0` |

### Сообщение RabbitMQ (TC / BC)

```json
{
  "id": 46712,
  "changeType": "CREATE",
  "name": "Цветовая маркировка критичных ресурсов",
  "source": "capability"
}
```

`id` и `changeType` обязательны. `changeType`: `CREATE` | `UPDATE` | `DELETE`.

Описание и code в очередь не передаются — сервис загружает сущность из Capability по `id`.

## Запуск

### Требования

- Python 3.12
- Qdrant, RabbitMQ, Capability-сервис, SSO
- API-ключ для embeddings и LLM (`OPENAI_API_KEY`)

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

Коллекции Qdrant создаются автоматически на пустом инстансе.

## Docker

```bash
docker build -t fdm-search .
docker run -p 8080:8080 \
  -e QDRANT_URL=http://qdrant:6333 \
  -e OPENAI_API_KEY=your-api-key \
  -e OPENAI_BASE_URL=https://api.example.com/v1 \
  -e OPENAI_EMBEDDING_MODEL=text-embedding-3-small \
  -e LLM_API_URL=https://api.example.com/v1/chat/completions \
  -e LLM_MODEL=your-llm-model \
  -e RABBITMQ_HOST=rabbitmq \
  -e RABBITMQ_VIRTUAL_HOST=dev_host \
  -e TECH_CAPABILITY_QUEUE=qdrant_tc \
  -e BUSINESS_CAPABILITY_QUEUE=qdrant_bc \
  -e RABBITMQ_EXCHANGE=adv-exchange \
  -e RABBITMQ_ROUTING_KEY=adv-routing \
  -e INTEGRATION_AUTHSSO_SERVER_URL=https://sso.example.com/rabbit-token \
  -e INTEGRATION_CAPABILITY_SERVER_URL=http://capability:8085 \
  -e DOC_SERVICE_URL=https://docs.example.com \
  -e DOC_CHUNK_SIZE=1000 \
  -e DOC_CHUNK_OVERLAP=150 \
  -e SEARCH_MIN_SCORE=0.35 \
  fdm-search
```

## Схема данных в Qdrant

### `tech_capability`

Параметры: `size=VECTOR_SIZE`, `distance=Cosine`

| Поле | Описание |
|------|----------|
| `entity_type` | `tech_capability` |
| `internal_id` | ID из Capability |
| `name` / `name_lower` | Название |
| `description` | Описание |
| `code` | Код TC |
| `synonyms` / `actions` | Массивы строк (LLM) |
| `system_id` / `system_name` / `system_alias` | Система |
| `system_name_lower` / `system_alias_lower` | Для `exclude_systems` |
| `parent_codes` | Коды BC-родителей |
| `created_at` / `updated_date` | ISO 8601 |

### `business_capability`

| Поле | Описание |
|------|----------|
| `entity_type` | `business_capability` |
| `internal_id` | ID из Capability |
| `code` | Код BC |
| `name` / `description` | Название и описание |
| `synonyms` / `actions` | LLM |
| `is_domain` | Признак домена |
| `parent_codes` | Коды BC-родителей вверх по иерархии |

Подробнее: [`docs/TZ_BC_Qdrant.md`](docs/TZ_BC_Qdrant.md).

### `user_documentation`

| Поле | Описание |
|------|----------|
| `path` / `source_path` | Путь файла в архиве |
| `doc_type` | Тип (`md`, `dsl`, …) |
| `chunk_id` | Идентификатор чанка |
| `source_url` | Ссылка на страницу портала |
| `title` | Заголовок фрагмента |
| `text` | Текст чанка |

### Миграция размерности вектора

Если коллекция создана под другую `VECTOR_SIZE`, удалите её и дайте сервису создать заново, затем переиндексируйте данные.
