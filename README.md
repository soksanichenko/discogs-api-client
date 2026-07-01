# discogs-api-client

Unofficial OpenAPI 3.1.0 spec for the Discogs API v2.0, with a local Swagger UI proxy for interactive testing and an inventory management UI.

## Project structure

```
discogs-api-client/
├── sources/
│   ├── proxy.py               # Starlette proxy: Swagger UI + OAuth 1.0a + API forwarding
│   ├── discogs-openapi.yaml   # Full Discogs API OpenAPI spec
│   ├── inventory.html         # Inventory management UI
│   └── requirements.txt       # App Python dependencies
├── ansible/
│   ├── ansible.cfg
│   ├── inventories/zelgray.work/
│   └── roles/discogs-api-client/  # Docker build + nginx deploy
├── requirements.txt           # Ansible/tooling dependencies
├── requirements.yml           # Ansible collections
└── install_dependencies.sh    # Installs both requirements files + infisical CLI
```

## Running locally

```bash
pip install -r sources/requirements.txt
python sources/proxy.py
```

Opens at: `http://localhost:8777/`  
Inventory UI: `http://localhost:8777/inventory`

## Authentication

### OAuth 1.0a (recommended)

Set env vars and visit `/oauth/start`:

```bash
export DISCOGS_CONSUMER_KEY=your_key
export DISCOGS_CONSUMER_SECRET=your_secret
python sources/proxy.py
# then open http://localhost:8777/oauth/start
```

Once authorized, all proxied requests are signed automatically. The access token is persisted to `.oauth_token.json` (or `$DISCOGS_TOKEN_DIR/.oauth_token.json`).

### Manual auth via Swagger UI

Click **Authorize** and fill in one of:

- `discogsToken` → `Discogs token=YOUR_PERSONAL_ACCESS_TOKEN`
- `discogsKeySecret` → key + secret pair

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `DISCOGS_CONSUMER_KEY` | OAuth app consumer key | _(empty)_ |
| `DISCOGS_CONSUMER_SECRET` | OAuth app consumer secret | _(empty)_ |
| `DISCOGS_BASE_URL` | Public base URL of the proxy (used for OAuth callback and Swagger server URL) | `http://localhost:8777` |
| `DISCOGS_TOKEN_DIR` | Directory where `.oauth_token.json` is stored | same dir as `proxy.py` |

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
Secrets `discogs-consumer-key` and `discogs-consumer-secret` must exist in Infisical under `/hosts/zelgray-work`.

See [`ansible/roles/discogs-api-client/README.md`](ansible/roles/discogs-api-client/README.md).

## Spec validation

```bash
# Structural validation
python -m openapi_spec_validator sources/discogs-openapi.yaml

# OWASP API Security Top 10 lint
spectral lint sources/discogs-openapi.yaml \
  --ruleset https://unpkg.com/@stoplight/spectral-owasp-ruleset/dist/ruleset.mjs
```

## Requirements

- Python 3.9+
- Docker (for deployment)

## References

- Discogs API documentation: https://www.discogs.com/developers/
- Discogs API Terms of Use: https://support.discogs.com/hc/articles/360009334593-API-Terms-of-Use
