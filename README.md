<!-- Copyright (c) 2024 PJSC VimpelCom -->

# fdm-search

Сервис семантического поиска **Tech Capability (TC)**, **Business Capability (BC)** и **пользовательской документации**.

Индексирует сущности в Qdrant, синхронизирует TC и BC через RabbitMQ и отдаёт HTTP API.  
Dense-эмбеддинги и LLM-обогащение (synonyms / actions) — через OpenAI-compatible API.  
BM25 (sparse) считает **сервер Qdrant** (≥ 1.15.2) по тексту `Document`.

## Возможности

- **Hybrid-поиск TC / BC / документации** — dense + BM25 → RRF
- **Фильтры TC/BC** — `entity_type`, `parent`, `exclude_systems` (TC), `is_domain` (BC)
- **`SEARCH_MIN_SCORE`** — отсекает слабые кандидаты на **dense-prefetch только для TC/BC** (не для docs, не для RRF-score)
- **LLM rerank** — опционально (`llm_rerank=true`), по умолчанию выкл.
- **Точный поиск по `code`** — TC и BC
- **Синхронизация TC и BC** — RabbitMQ `CREATE` / `UPDATE` / `DELETE`
- **Обогащение LLM** — synonyms и actions при создании и при изменении name/description
- **Переиндексация доков** — `POST /api/v1/docs/reindex` (multipart zip)
- **Автоинициализация коллекций** Qdrant при старте (если их нет)
- **Swagger** — `/docs`, метрики — `/actuator/prometheus`

## Архитектура

```
┌─────────────┐   CREATE/UPDATE/DELETE    ┌──────────────┐
│  RabbitMQ   │ ────────────────────────► │  fdm-search  │
│  (TC / BC)  │                           │  (FastAPI)   │
└─────────────┘                           └──────┬───────┘
                                                 │
┌─────────────┐   GET TC/BC by id                │ zip → reindex docs
│ Capability  │ ◄────────────────────────────────┤
└─────────────┘                                  ▼
                                        ┌──────────────────┐
                                        │ Embeddings / LLM │
                                        │ (OpenAI-compatible)
                                        └────────┬─────────┘
                                                 │ dense + Document(bm25)
                                                 ▼
                                        ┌──────────────────┐
                                        │      Qdrant      │
                                        │ tech_capability  │
                                        │ business_capability
                                        │ user_documentation
                                        │ (dense + bm25)   │
                                        └──────────────────┘
```

При старте:

1. Подключение к Qdrant, создание hybrid-коллекций при отсутствии
2. Payload-индексы для фильтров
3. RabbitMQ (SSO или логин/пароль по `APP_AMBASSADOR_AUTH`), consumers TC и BC
4. Очереди из `TECH_CAPABILITY_QUEUE` и `BUSINESS_CAPABILITY_QUEUE`

### RabbitMQ

| `changeType` | Поведение |
|--------------|-----------|
| `CREATE` | Capability → LLM (synonyms/actions) → dense embedding + BM25 Document → Qdrant |
| `UPDATE` | LLM — если изменились `name`/`description` или нет synonyms/actions. Векторы пересчитываются также при смене `code` / system (TC) |
| `DELETE` | Удаление по `internal_id` |

### Тексты векторов TC/BC

| Вектор | Поля |
|--------|------|
| **dense** | `name` + очищенный укороченный `description` + `synonyms` + `actions` |
| **bm25** | `name` + `code` + `synonyms` + `actions` + `system_name`/`alias` (только TC) |

В payload полный `description` не режется. HTML/URL убираются только при сборке dense-текста.

Поиск TC/BC: prefetch dense (+ `SEARCH_MIN_SCORE`) и BM25 → **RRF**. Фильтры на оба prefetch.

### Документация

Не через RabbitMQ. Zip `.md` / `.dsl` → `POST /api/v1/docs/reindex`:

- пересоздаёт `user_documentation`
- чанки: `DOC_CHUNK_SIZE` / `DOC_CHUNK_OVERLAP`
- `source_url` из `DOC_SERVICE_URL`
- **dense** = embedding API по тексту чанка
- **bm25** = `title` + текст чанка (считает Qdrant)
- поиск: hybrid RRF **без** `SEARCH_MIN_SCORE` на dense

## Стек

| Компонент | Технология |
|-----------|------------|
| HTTP API | FastAPI, Uvicorn |
| Векторная БД | Qdrant ≥ 1.15.2, `qdrant-client==1.15.1` |
| Очередь | RabbitMQ (`aio-pika`) |
| Dense | OpenAI SDK → embeddings API |
| BM25 | server-side Qdrant (`Qdrant/bm25`, `language=russian`) |
| LLM | httpx → chat completions |
| Метрики | `prometheus-fastapi-instrumentator` |
| Python | 3.12 |

## Структура проекта

```
fdm-search/
├── app/
│   ├── main.py
│   ├── routes/routes.py
│   ├── services/
│   │   ├── message_service.py           # TC
│   │   ├── bc_message_service.py        # BC
│   │   ├── tc_payload.py                # dense/bm25 тексты, system-поля
│   │   ├── bc_payload.py
│   │   ├── doc_index_service.py
│   │   └── doc_search_service.py
│   ├── repositories/
│   │   ├── base.py                      # hybrid Qdrant, RRF
│   │   ├── tech_capability.py
│   │   ├── business_capability.py
│   │   └── documentation.py
│   ├── consumers/
│   ├── clients/
│   ├── models/
│   └── core/
├── Dockerfile
├── requirements.txt
└── pyproject.toml
```

## API

По умолчанию: `http://localhost:8080` (порт из `PORT`).

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/health` | Healthcheck |
| `GET` | `/versions` | Версии зависимостей |
| `GET` | `/actuator/prometheus` | Метрики |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/api/v1/search` | Hybrid-поиск TC + BC |
| `GET` | `/search/docs` | Hybrid-поиск документации |
| `POST` | `/api/v1/docs/reindex` | Переиндексация доков из zip |
| `GET` | `/api/v1/all-tcs` | Все TC |
| `GET` | `/api/v1/all-bcs` | Все BC |
| `GET` | `/api/v1/search/{internal_id}` | TC по `internal_id` |
| `GET` | `/api/v1/search/bc/{internal_id}` | BC по `internal_id` |
| `DELETE` | `/api/v1/documents` | Очистить TC |
| `DELETE` | `/api/v1/bc-documents` | Очистить BC |
| `DELETE` | `/api/v1/document/internal_id/{id}` | Удалить TC |

### `GET /api/v1/search`

Нужен `query` или `code`.

| Параметр | Описание |
|----------|----------|
| `query` | Hybrid-поиск |
| `code` | Exact по `code` |
| `entity_type` | `tc`, `bc` или `tc,bc` (по умолчанию `tc,bc`) |
| `parent` | Коды родительских BC через запятую (OR) |
| `exclude_systems` | Исключить системы (TC) |
| `is_domain` | Фильтр домена (BC) |
| `limit` | 1–100, по умолчанию 10 |
| `llm_rerank` | LLM rerank (только для `query`) |

```bash
curl "http://127.0.0.1:8080/api/v1/search?query=оплата%20услуг&limit=5"
curl "http://127.0.0.1:8080/api/v1/search?query=каталог&entity_type=bc&is_domain=true"
curl "http://127.0.0.1:8080/api/v1/search?code=BC-041968"
```

### `GET /search/docs`

| Параметр | Описание |
|----------|----------|
| `query` | Обязательный |
| `limit` | 1–50, по умолчанию 5 |

```bash
curl "http://127.0.0.1:8080/search/docs?query=как%20создать%20техническую%20возможность&limit=5"
```

### `POST /api/v1/docs/reindex`

Поле `file` — zip с `.md` / `.dsl`. Пересоздаёт `user_documentation`.

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/docs/reindex" \
  -F "file=@docs.zip"
```

## Переменные окружения

Приоритет: `app/.env.local` (если есть) → корневой `.env` → env процесса.

| Переменная | Обязательная | Описание |
|------------|:------------:|----------|
| `HOST` | нет | По умолчанию `0.0.0.0` |
| `PORT` | нет | По умолчанию `8080` |
| `RELOAD` | нет | Hot-reload Uvicorn |
| `QDRANT_URL` | да | URL Qdrant |
| `QDRANT_API_KEY` | нет | API-ключ Qdrant |
| `OPENAI_API_KEY` | да | Ключ embeddings и LLM |
| `OPENAI_BASE_URL` | да | Base URL embeddings |
| `OPENAI_EMBEDDING_MODEL` | да | Имя embedding-модели |
| `VECTOR_SIZE` | нет | Размерность dense (по умолчанию `1536`) |
| `LLM_API_URL` | да | URL chat completions |
| `LLM_MODEL` | да | Имя LLM |
| `SPRING_RABBITMQ_HOST` | да | Хост RabbitMQ |
| `SPRING_RABBITMQ_VIRTUAL_HOST` | да | Virtual host |
| `SPRING_RABBITMQ_USERNAME` | нет | Логин (если `APP_AMBASSADOR_AUTH=false`) |
| `SPRING_RABBITMQ_PASSWORD` | нет | Пароль (если `APP_AMBASSADOR_AUTH=false`) |
| `TECH_CAPABILITY_QUEUE` | да | Очередь TC |
| `BUSINESS_CAPABILITY_QUEUE` | да | Очередь BC |
| `RABBITMQ_EXCHANGE` | да | Exchange |
| `RABBITMQ_ROUTING_KEY` | да | Routing key |
| `APP_AMBASSADOR_AUTH` | нет | `true` (default) — SSO; `false` — логин/пароль |
| `INTEGRATION_AUTHSSO_SERVER_URL` | нет | URL SSO для RabbitMQ (нужен при ambassador auth) |
| `INTEGRATION_CAPABILITY_SERVER_URL` | да | Base URL Capability |
| `DOC_SERVICE_URL` | да | Base URL портала доков (для `source_url`) |
| `DOC_CHUNK_SIZE` | да | Размер чанка |
| `DOC_CHUNK_OVERLAP` | да | Перекрытие чанков |
| `SEARCH_MIN_SCORE` | да | Min cosine на dense-prefetch **TC/BC** |
| `HYBRID_PREFETCH_LIMIT` | нет | Кандидатов из каждого retriever до RRF (default `50`) |
| `DENSE_DESC_MAX_CHARS` | нет | Макс. длина description в dense-тексте (default `500`) |

### Сообщение RabbitMQ (TC / BC)

```json
{
  "id": 46712,
  "changeType": "CREATE",
  "name": "Пример названия",
  "source": "capability"
}
```

Обязательны `id` и `changeType` (`CREATE` | `UPDATE` | `DELETE`).  
Описание и code в очередь не передаются — сервис читает сущность из Capability по `id`.

## Запуск

### Требования

- Python 3.12
- Qdrant ≥ 1.15.2, RabbitMQ, Capability
- Настроенные embeddings / LLM API
- SSO URL, если `APP_AMBASSADOR_AUTH=true`

### Установка и старт

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / macOS

pip install -r requirements.txt
set PYTHONPATH=.
python app/main.py
```

- API: `http://localhost:8080`
- Swagger: `http://localhost:8080/docs`

## Docker

Пример (плейсхолдеры, без реальных секретов):

```bash
docker build -t fdm-search .
docker run -p 8080:8080 \
  -e QDRANT_URL=http://qdrant:6333 \
  -e OPENAI_API_KEY=your-api-key \
  -e OPENAI_BASE_URL=https://api.example.com/v1 \
  -e OPENAI_EMBEDDING_MODEL=your-embedding-model \
  -e LLM_API_URL=https://api.example.com/v1/chat/completions \
  -e LLM_MODEL=your-llm-model \
  -e SPRING_RABBITMQ_HOST=rabbitmq \
  -e SPRING_RABBITMQ_VIRTUAL_HOST=dev_host \
  -e APP_AMBASSADOR_AUTH=true \
  -e TECH_CAPABILITY_QUEUE=qdrant_tc \
  -e BUSINESS_CAPABILITY_QUEUE=qdrant_bc \
  -e RABBITMQ_EXCHANGE=exchange-name \
  -e RABBITMQ_ROUTING_KEY=routing-key \
  -e INTEGRATION_AUTHSSO_SERVER_URL=https://sso.example.com/token \
  -e INTEGRATION_CAPABILITY_SERVER_URL=http://capability:8080 \
  -e DOC_SERVICE_URL=https://docs.example.com \
  -e DOC_CHUNK_SIZE=1000 \
  -e DOC_CHUNK_OVERLAP=150 \
  -e SEARCH_MIN_SCORE=0.35 \
  fdm-search
```

## Схема данных в Qdrant

Все три коллекции — **named vectors**:

| Имя | Тип | Параметры |
|-----|-----|-----------|
| `dense` | dense | `VECTOR_SIZE`, Cosine |
| `bm25` | sparse | IDF, `Qdrant/bm25`, `language=russian` |

### `tech_capability` payload

| Поле | Описание |
|------|----------|
| `entity_type` | `tech_capability` |
| `internal_id` | ID из Capability |
| `name` / `name_lower` | Название |
| `description` | Полное описание |
| `code` | Код TC |
| `synonyms` / `actions` | LLM |
| `system_*` | Система |
| `parent_codes` | Коды BC-родителей |
| `created_at` / `updated_date` | ISO 8601 |

### `business_capability` payload

| Поле | Описание |
|------|----------|
| `entity_type` | `business_capability` |
| `internal_id` | ID из Capability |
| `code` / `name` / `description` | Основные поля |
| `synonyms` / `actions` | LLM |
| `is_domain` | Признак домена |
| `parent_codes` | Родители вверх по иерархии |

### `user_documentation` payload

| Поле | Описание |
|------|----------|
| `path` / `source_path` | Путь в архиве / URL страницы |
| `doc_type` | Тип (`md`, `dsl`, …) |
| `chunk_id` | Индекс чанка |
| `title` / `text` | Заголовок и текст |

### Миграция на hybrid

Коллекции со старым одним unnamed dense **несовместимы**. Удалить коллекции, перезапустить сервис (создадутся hybrid), переиндексировать TC/BC и выполнить `POST /api/v1/docs/reindex`.

При смене `VECTOR_SIZE` — то же: удалить коллекции и переиндексировать.
