# Upstream contract record

Source: [onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx).

Inspected revision: [`a4ed03e6717c85b3d9d4e22196dbc4166e7b965e`](https://github.com/onyx-dot-app/onyx/commit/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e), committed **2026-10-22T04:12:59Z**. The reference was shallow-cloned read-only into a fresh temporary checkout on 2026-10-22. No upstream services were started.

| Contract | Pinned primary source |
| --- | --- |
| `POST /onyx-api/ingestion`; synchronous pipeline and response | [ingestion.py](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/server/onyx_api/ingestion.py) |
| `IngestionDocument(document, cc_pair_id)` and `IngestionResult(document_id, already_existed)` | [ingestion models](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/server/onyx_api/models.py) |
| `DocumentBase`, `Section`, `TextSection`; omitted source defaults to ingestion API | [connector models](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/connectors/models.py) |
| `POST /admin/search`; keyword retrieval and admin permission | [query_backend.py](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/server/query_and_chat/query_backend.py) |
| Request requires `query` and `filters`; response contains `documents` | [query models](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/server/query_and_chat/models.py) |
| `SearchDoc`, `BaseFilters.tags`, and `Tag(tag_key, tag_value)` | [search models](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/context/search/models.py) |
| Bearer-key extraction | [api_key.py](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/backend/onyx/auth/api_key.py) |
| Proxy removes `/api`; Compose exposes port 3000 | [Nginx template](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/deployment/data/nginx/app.conf.template), [Compose](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/deployment/docker_compose/docker-compose.yml) |
| Manual setup, required authentication secret, image-tag default | [deployment README](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/deployment/docker_compose/README.md), [environment template](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/deployment/docker_compose/env.template) |

The independently implemented example uses documented JSON field names and routes. It does not vendor upstream implementation files. Upstream code outside its `ee` directories is generally MIT-licensed; enterprise directories have separate terms. Consult the pinned [upstream LICENSE](https://github.com/onyx-dot-app/onyx/blob/a4ed03e6717c85b3d9d4e22196dbc4166e7b965e/LICENSE) before copying or deploying upstream components. This project's own source is covered by its local MIT license.

Verified locally: offline ranking and abstention; synthetic input validity; actual upstream AST schema/route checks; real localhost HTTP requests with mocked responses; redirect rejection; proxy bypass; non-HTTPS URL restrictions; response validation; timeout and polling behavior. Not verified: a running Onyx stack, real API credentials, live ingestion/retrieval, model downloads, Docker deployment, upstream production authorization, provider-specific LLM behavior, connector synchronization, agents, MCP, or code execution.
