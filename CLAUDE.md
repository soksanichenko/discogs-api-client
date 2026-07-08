# Project: discogs-api-client

## Overview

Discogs API proxy: serves Swagger UI, forwards requests to `api.discogs.com`, handles OAuth 1.0a, and includes an inventory management UI. Deployed to `zelgray.work/discogs` via Ansible as a Docker container.

## Project Structure

```
discogs-api-client/
├── .github/
│   ├── dependabot.yml          # Weekly pip + github-actions updates, target-branch master
│   └── workflows/
│       └── lint.yml            # ruff check + ruff format --check on sources/ (push, PR to master)
├── sources/
│   ├── proxy.py               # Starlette app — all routes and proxy logic
│   ├── discogs-openapi.yaml   # OpenAPI 3.1.0 spec for Discogs API v2.0
│   ├── inventory.html         # Single-page inventory management UI
│   ├── collection.html        # Single-page collection viewer — list items for sale (single/bulk)
│   └── requirements.txt       # App deps (httpx, starlette, uvicorn, oauthlib, PyYAML, python-dotenv)
├── ansible/
│   ├── ansible.cfg            # inventory = inventories/zelgray.work, roles_path = roles
│   ├── inventories/
│   │   └── zelgray.work/
│   │       ├── hosts.yml      # vds: zelgray.work / ssh.zelgray.work (user: zelgray)
│   │       └── group_vars/
│   │           └── all.yml    # nginx paths, docker vars, Infisical secret refs
│   ├── playbooks/
│   │   ├── deploy.yml   # Main playbook (hosts: vds, Infisical pre_tasks)
│   │   └── pre_tasks/
│   │       └── infisical.yml        # Login + load secrets from Infisical EU
│   └── roles/
│       └── discogs-api-client/
│           ├── defaults/main.yml
│           ├── tasks/main.yml
│           ├── handlers/main.yml
│           ├── files/Dockerfile     # Python 3.12-slim image
│           └── templates/
│               ├── nginx-location.conf.j2
│               └── nginx-upstream.conf.j2
├── requirements.txt           # Ansible/tooling: infisicalsdk, ansible-lint, yamllint, pre-commit
├── requirements.yml           # Ansible collections: infisical.vault, community.docker
├── pyproject.toml             # Package metadata + deps; ruff format quote-style = single
├── .pre-commit-config.yaml    # ruff + ruff-format hooks (sources/)
└── install_dependencies.sh    # pip install + ansible-galaxy + infisical CLI (DNF)
```

## Application (sources/proxy.py)

**Framework:** Starlette + uvicorn, port `8777`

**Routes:**

| Route | Handler | Description |
|---|---|---|
| `GET /` | `swagger_ui` | Swagger UI HTML |
| `GET /docs` | `swagger_ui` | Alias |
| `GET /openapi.json` | `openapi_spec` | Spec with server URL set to `BASE_URL` |
| `GET /inventory` | `inventory_page` | Serves `inventory.html` |
| `GET /collection` | `collection_page` | Serves `collection.html` |
| `GET /oauth/status` | `oauth_status` | JSON: `{authorized, configured, username}` |
| `GET /oauth/start` | `oauth_start` | Step 1: redirect to Discogs authorize page |
| `GET /oauth/callback` | `oauth_callback` | Step 2: exchange verifier for access token |
| `GET /oauth/revoke` | `oauth_revoke` | Clear stored token, redirect to `/` |
| `ANY /{path}` | `proxy` | Transparent proxy to `api.discogs.com` |

**Key globals:**

| Name | Source | Purpose |
|---|---|---|
| `PORT` | hardcoded `8777` | uvicorn listen port |
| `BASE_URL` | `DISCOGS_BASE_URL` env | OAuth callback URL + Swagger server URL |
| `CONSUMER_KEY/SECRET` | `DISCOGS_CONSUMER_KEY/SECRET` env | OAuth 1.0a app credentials |
| `TOKEN_PATH` | `DISCOGS_TOKEN_DIR` env / `__file__` parent | Persisted access token location |
| `_oauth_access` | module-level dict | In-memory OAuth state (`token`, `secret`, `username`) |

OAuth token is persisted as JSON to `TOKEN_PATH` (loaded on startup). In Docker: `/app/data/.oauth_token.json`.

## Ansible Role (discogs-api-client)

**Deploy flow:** copy `sources/` from control machine → copy `Dockerfile` from role `files/` → `docker_image` build → `docker_container` run → nginx upstream + location configs.

**No git clone** — always deploys from local working copy.

**nginx config placement:** location block goes to `discogs_api_client_nginx_custom_locations_path` (default: `nginx_custom_locations_path`). For `zelgray.work` this is overridden to `zelgray.work-custom-locations/` so the location is included only in that server block. The upstream goes to `nginx_custom_upstream_path`.

**Redirect fix:** `location = /discogs` uses `absolute_redirect off` to avoid nginx constructing redirects with its internal port `8443`.

**Role defaults:**

| Variable | Default |
|---|---|
| `discogs_api_client_container_name` | `discogs-api-client` |
| `discogs_api_client_port` | `8777` |
| `discogs_api_client_data_path` | `{{ docker_volumes_directory }}/discogs-api-client` |
| `discogs_api_client_nginx_location` | `/discogs` |
| `discogs_api_client_nginx_custom_locations_path` | `{{ nginx_custom_locations_path }}` |
| `discogs_api_client_upstream_name` | `discogs_api_client_upstream` |

## Secrets (Infisical)

Project ID: `286db07f-4dba-4ca9-a515-f017d77b8bf1`, env: `prod`, path: `/hosts/zelgray-work`.

| Infisical key | Variable |
|---|---|
| `discogs-consumer-key` | `discogs_api_client_consumer_key` |
| `discogs-consumer-secret` | `discogs_api_client_consumer_secret` |

## Deployment

```bash
cd ansible
ansible-playbook playbooks/deploy.yml
```

Requires env: `INFISICAL_API_URL=https://eu.infisical.com`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.

Live at: `https://zelgray.work/discogs`

## Key Dependencies (sources/requirements.txt)

| Package | Version | Purpose |
|---|---|---|
| `starlette` | 1.3.1 | ASGI framework |
| `uvicorn` | 0.49.0 | ASGI server |
| `httpx` | 0.28.1 | Async HTTP client for proxying |
| `oauthlib` | 3.3.1 | OAuth 1.0a request signing |
| `PyYAML` | 6.0.3 | Load OpenAPI spec |
| `python-dotenv` | 1.2.2 | `.env` file support for local dev |

## Conventions

- All YAML files start with `---` and end with `...` followed by an empty line.
- Single quotes for Python string literals — enforced by ruff (`quote-style = "single"` in `pyproject.toml`), run via pre-commit and the `lint.yml` CI workflow.
- Secrets never in files — always injected via env vars into the container.
- `inventory.html` and `collection.html` derive their API base URL from `window.location.pathname` at runtime (subpath-aware).
- `collection.html` lists a user's collection folders and lets the owner list items for sale (single item, or a queue that reopens the same form per selected item so each lot gets its own condition/price) via `POST /marketplace/listings`; each successfully listed item is then removed from the collection folder via `DELETE /users/{username}/collection/folders/{folder_id}/releases/{release_id}/instances/{instance_id}`. The listing form prefills price from `GET /marketplace/price_suggestions/{release_id}` (rounded up) and links to `discogs.com/sell/history/{release_id}`.
- `collection.html` columns are sortable by clicking the header (toggles asc/desc). Artist/Title/Format/Label/Cat#/Year/Rating/Added map to the API's `sort` query param (server-side, works across the whole paginated collection); Folder/Notes have no API sort support, so those are sorted client-side and only affect the currently loaded page. There is no Low/Median/High price column — the Discogs API does not expose historical sold-price statistics anywhere.
