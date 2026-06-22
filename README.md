# discogs-api-client

Unofficial OpenAPI 3.1.0 spec for the Discogs API v2.0, with a local Swagger UI proxy for interactive testing.

## What's here

| File | Purpose |
|------|---------|
| `discogs-openapi.yaml` | Full Discogs API spec — Database, Marketplace, Inventory Export/Upload, User Identity, Collection, Wantlist, Lists |
| `proxy.py` | Local reverse proxy that serves Swagger UI and forwards requests to `api.discogs.com` |
| `requirements.txt` | Python dependencies |

## Running the proxy

```bash
pip install -r requirements.txt
python proxy.py
```

Opens at: `http://localhost:8777/docs`

## Authentication

Click **Authorize** in Swagger UI and fill in one of the following:

### Personal Access Token (identifies you as a Discogs user)

Field: `discogsToken`
Value: `Discogs token=YOUR_TOKEN`

Get your token: discogs.com → Settings → Developers → Generate Token.

### Application Consumer Key + Secret (raises rate limit, unlocks image URLs — does not identify a user)

Fill in both fields:
- `discogsKey` → your Consumer Key
- `discogsSecret` → your Consumer Secret

Register your app: discogs.com → Settings → Developers → Register an Application.

## Spec coverage

- **Database** — Release, Master Release, Artist, Label, Search
- **Marketplace** — Inventory, Listings, Orders, Fee, Price Suggestions, Release Statistics
- **Inventory Export / Upload**
- **User Identity** — Profile, Submissions, Contributions
- **User Collection** — Folders, Items, Rating
- **User Wantlist**
- **User Lists**
- **OAuth** — `/oauth/request_token`, `/oauth/access_token` (full OAuth 1.0a flow documented as plain paths)

## Validation

```bash
# Structural validation
python -m openapi_spec_validator discogs-openapi.yaml

# OWASP API Security Top 10 lint
~/.npm-global/bin/spectral lint discogs-openapi.yaml \
  --ruleset https://unpkg.com/@stoplight/spectral-owasp-ruleset/dist/ruleset.mjs
```

## Requirements

- Python 3.9+
- See `requirements.txt` for pinned versions: httpx, starlette, uvicorn, PyYAML

## References

- Discogs API documentation: https://www.discogs.com/developers/
- Discogs API Terms of Use: https://support.discogs.com/hc/articles/360009334593-API-Terms-of-Use
