# discogs-api-client

Unofficial OpenAPI 3.1.0 spec for the Discogs API v2.0, with a local Swagger UI proxy for interactive testing, an inventory management UI, and a collection viewer for listing items for sale.

## Project structure

```
discogs-api-client/
├── .github/
│   ├── dependabot.yml          # Weekly dependency update PRs
│   └── workflows/lint.yml      # ruff check + format on push/PR
├── sources/
│   ├── proxy.py               # Starlette proxy: OAuth 1.0a + API forwarding + serves the pages below
│   ├── discogs-openapi.yaml   # Full Discogs API OpenAPI spec
│   ├── home.html              # Landing page (/) — just the shared header, sign-in only
│   ├── docs.html              # Swagger UI page (/docs)
│   ├── success.html           # OAuth callback success page
│   ├── inventory.html         # Inventory management UI
│   ├── collection.html        # Collection viewer — list items for sale, single or bulk
│   ├── theme.css              # Shared dark theme for all 5 pages
│   ├── nav.js                 # Shared header: sign-in button / page-switcher dropdown
│   ├── combo.js               # Shared searchable dropdown widget for inventory/collection filter bars
│   └── requirements.txt       # App Python dependencies
├── ansible/
│   ├── ansible.cfg
│   ├── inventories/zelgray.work/
│   └── roles/discogs-api-client/  # Docker build + nginx deploy
├── requirements.txt           # Ansible/tooling dependencies
├── requirements.yml           # Ansible collections
├── pyproject.toml             # Package metadata + ruff config
├── .pre-commit-config.yaml    # ruff + ruff-format hooks
└── install_dependencies.sh    # Installs both requirements files + infisical CLI
```

## Running locally

```bash
pip install -r sources/requirements.txt
python sources/proxy.py
```

Home (sign-in landing page): `http://localhost:8777/`  
Swagger UI: `http://localhost:8777/docs`  
Inventory UI: `http://localhost:8777/inventory`  
Collection UI: `http://localhost:8777/collection`

The home page is intentionally minimal — just a sign-in button, or (once signed in) a dropdown to the other three pages. Everything else lives on its own page.

## Authentication

### OAuth 1.0a (recommended)

Set env vars and visit `/oauth/start`:

```bash
export DISCOGS_CONSUMER_KEY=your_key
export DISCOGS_CONSUMER_SECRET=your_secret
python sources/proxy.py
# then open http://localhost:8777/oauth/start
```

Once authorized, all proxied requests are signed automatically. The access token is kept in a signed, httpOnly session cookie in your browser (not on the server) — each browser/user authorizes independently. Set `DISCOGS_SECRET_KEY` to a stable random value so sessions survive a server restart; otherwise an ephemeral key is generated at startup and everyone is logged out on restart.

### Manual auth via Swagger UI

Click **Authorize** and fill in one of:

- `discogsToken` → `Discogs token=YOUR_PERSONAL_ACCESS_TOKEN`
- `discogsKeySecret` → key + secret pair

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `DISCOGS_CONSUMER_KEY` | OAuth app consumer key | _(empty)_ |
| `DISCOGS_CONSUMER_SECRET` | OAuth app consumer secret | _(empty)_ |
| `DISCOGS_SECRET_KEY` | Key used to sign OAuth session cookies | ephemeral (regenerated on each restart) |
| `DISCOGS_BASE_URL` | Public base URL of the proxy (used for OAuth callback and Swagger server URL) | `http://localhost:8777` |
| `DISCOGS_REDIS_URL` | Redis URL for caching `GET /releases/{id}` lookups (e.g. `redis://localhost:6379`) | _(empty — caching disabled)_ |

## Caching

`GET /releases/{id}` responses are cached in Redis for 30 days when `DISCOGS_REDIS_URL` is set — release metadata (especially artist/label ids, used for the Inventory page's on-demand artist/label links) rarely changes and this endpoint gets hit a lot. Caching is entirely optional and best-effort: without the env var, or if Redis is unreachable, the app just proxies every request straight through and logs a warning.

```bash
# Local Redis for testing
docker run -d --name redis -p 6379:6379 redis:7-alpine
export DISCOGS_REDIS_URL=redis://localhost:6379
python sources/proxy.py
```

## Spec coverage

- **Database** — Release, Master Release, Artist, Label, Search
- **Marketplace** — Inventory, Listings, Orders, Fee, Price Suggestions, Release Statistics
- **Inventory Export / Upload**
- **User Identity** — Profile, Submissions, Contributions
- **User Collection** — Folders, Items, Rating
- **User Wantlist**
- **User Lists**
- **OAuth** — `/oauth/request_token`, `/oauth/access_token`

## Deployment

Deployed to `zelgray.work` at `/discogs` via Ansible. Runs as a Docker container on the `active` network behind nginx.

```bash
cd ansible
ansible-playbook playbooks/deploy.yml
```

Requires `INFISICAL_API_URL`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET` in the environment.  
Secrets `discogs-consumer-key`, `discogs-consumer-secret`, and `discogs-secret-key` must exist in Infisical under `/hosts/zelgray-work`.

See [`ansible/roles/discogs-api-client/README.md`](ansible/roles/discogs-api-client/README.md).

## Spec validation

```bash
# Structural validation
python -m openapi_spec_validator sources/discogs-openapi.yaml

# OWASP API Security Top 10 lint
spectral lint sources/discogs-openapi.yaml \
  --ruleset https://unpkg.com/@stoplight/spectral-owasp-ruleset/dist/ruleset.mjs
```

## Linting

```bash
pip install -r requirements.txt
pre-commit install
pre-commit run --all-files
```

Runs `ruff check` and `ruff format --check` on `sources/`. The same checks run in CI on every push/PR (`.github/workflows/lint.yml`).

## Requirements

- Python 3.9+
- Docker (for deployment)

## References

- Discogs API documentation: https://www.discogs.com/developers/
- Discogs API Terms of Use: https://support.discogs.com/hc/articles/360009334593-API-Terms-of-Use
