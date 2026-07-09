# Project: discogs-api-client

## Overview

Discogs API proxy: serves Swagger UI, forwards requests to `api.discogs.com`, handles OAuth 1.0a, and includes inventory/collection management UIs. Deployed to `zelgray.work/discogs` via Ansible as a Docker container.

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
│   ├── home.html              # Landing page (/) — just the shared header, sign-in only
│   ├── docs.html              # Swagger UI page (/docs)
│   ├── success.html           # OAuth callback success page
│   ├── inventory.html         # Single-page inventory management UI
│   ├── collection.html        # Single-page collection viewer — list items for sale (single/bulk)
│   ├── theme.css              # Shared dark theme + header/nav/table/modal/form/combo styles for all 5 pages
│   ├── nav.js                 # Shared header: fetches OAuth status, renders sign-in button or page-switcher dropdown
│   ├── combo.js                # Shared searchable dropdown widget, used by inventory.html/collection.html's filter bars
│   └── requirements.txt       # App deps (httpx, starlette, uvicorn, oauthlib, itsdangerous, redis, PyYAML, python-dotenv)
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
| `GET /` | `home_page` | Serves `home.html` — landing page, just the shared header |
| `GET /docs` | `swagger_ui` | Serves `docs.html` — the actual Swagger UI |
| `GET /openapi.json` | `openapi_spec` | Spec with server URL set to `BASE_URL` |
| `GET /inventory` | `inventory_page` | Serves `inventory.html` |
| `GET /collection` | `collection_page` | Serves `collection.html` |
| `GET /theme.css` | `theme_css` | Shared dark theme stylesheet |
| `GET /nav.js` | `nav_js` | Shared header/nav script |
| `GET /combo.js` | `combo_js` | Shared searchable-dropdown widget script (filter bars) |
| `GET /oauth/status` | `oauth_status` | JSON: `{authorized, configured, username}` |
| `GET /oauth/start` | `oauth_start` | Step 1: redirect to Discogs authorize page |
| `GET /oauth/callback` | `oauth_callback` | Step 2: exchange verifier for access token, serves `success.html` |
| `GET /oauth/revoke` | `oauth_revoke` | Clear this browser's session, redirect to `{BASE_URL}/` |
| `ANY /{path}` | `proxy` | Transparent proxy to `api.discogs.com` |

**Key globals:**

| Name | Source | Purpose |
|---|---|---|
| `PORT` | hardcoded `8777` | uvicorn listen port |
| `BASE_URL` | `DISCOGS_BASE_URL` env | OAuth callback URL + Swagger server URL |
| `CONSUMER_KEY/SECRET` | `DISCOGS_CONSUMER_KEY/SECRET` env | OAuth 1.0a app credentials |
| `SECRET_KEY` | `DISCOGS_SECRET_KEY` env, or an ephemeral `secrets.token_hex(32)` if unset | Signs the session cookie |
| `REDIS_URL` | `DISCOGS_REDIS_URL` env | Redis connection string; empty disables caching entirely |
| `_redis` | `redis.asyncio.Redis \| None`, set up in the `lifespan` context manager | `None` if `REDIS_URL` is unset or the initial `PING` fails at startup |
| `_pending_tokens` | module-level dict | Transient `oauth_token` → `oauth_token_secret` map between `/oauth/start` and `/oauth/callback` |

OAuth access token/secret/username are **not** stored server-side — they live in `request.session`, backed by a signed, httpOnly cookie (`discogs_session`, via `starlette.middleware.sessions.SessionMiddleware`, 1-year `max_age`). Each browser authorizes and proxies independently; nothing survives on disk. If `DISCOGS_SECRET_KEY` isn't set, a random key is generated at process startup, so every restart invalidates all existing sessions — set it explicitly anywhere sessions need to survive a restart.

**Redis caching (`_cache_get`/`_cache_set`, wired into `proxy()`):** `GET` requests matching `_CACHEABLE_GET_PATHS` (currently just `releases/{id}`) are cached under `discogs:{path}?{sorted query string}` for `CACHE_TTL` (30 days) — release metadata, especially artist/label ids, is effectively static and this is hit a lot by `inventory.html`'s on-demand artist/label link resolution (`fetchReleaseCached`). Both cache helpers swallow Redis errors and log a warning rather than failing the request — caching is strictly best-effort, on both the read and write path, and works identically whether `_redis` is `None` (unconfigured) or a live connection that happens to error out mid-session (e.g. Redis restarts). No other endpoint is cached; adding one means appending to `_CACHEABLE_GET_PATHS`.

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
| `discogs_api_client_data_path` | `{{ docker_volumes_directory }}/discogs-api-client` (build context only — no longer used for OAuth persistence) |
| `discogs_api_client_secret_key` | _(empty)_ — random string signing OAuth session cookies |
| `discogs_api_client_redis_url` | `redis://{{ redis_container_name }}:6379` — shared Redis (not a dedicated container; see `infra`'s `redis` role) |
| `discogs_api_client_nginx_location` | `/discogs` |
| `discogs_api_client_nginx_custom_locations_path` | `{{ nginx_custom_locations_path }}` |
| `discogs_api_client_upstream_name` | `{{ discogs_api_client_container_name }}` |

## Secrets (Infisical)

Project ID: `286db07f-4dba-4ca9-a515-f017d77b8bf1`, env: `prod`, path: `/hosts/zelgray-work`.

| Infisical key | Variable |
|---|---|
| `discogs-consumer-key` | `discogs_api_client_consumer_key` |
| `discogs-consumer-secret` | `discogs_api_client_consumer_secret` |
| `discogs-secret-key` | `discogs_api_client_secret_key` |

## Deployment

```bash
cd ansible
ansible-playbook playbooks/deploy.yml
```

Requires env: `INFISICAL_API_URL=https://eu.infisical.com`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`.

Also requires the shared `redis` role already deployed (`infra`'s `playbooks/redis.yml`) — this role doesn't bring its own Redis container. Not a hard dependency: if Redis is down or unreachable, the app just proxies uncached and logs a warning (see Redis caching note above).

Live at: `https://zelgray.work/discogs`

## Key Dependencies (sources/requirements.txt)

| Package | Version | Purpose |
|---|---|---|
| `starlette` | 1.3.1 | ASGI framework |
| `uvicorn` | 0.51.0 | ASGI server |
| `httpx` | 0.28.1 | Async HTTP client for proxying |
| `itsdangerous` | 2.2.0 | Signs the session cookie (required by Starlette's `SessionMiddleware`) |
| `oauthlib` | 3.3.1 | OAuth 1.0a request signing |
| `PyYAML` | 6.0.3 | Load OpenAPI spec |
| `python-dotenv` | 1.2.2 | `.env` file support for local dev |
| `redis` | 8.0.1 | Async client for the `GET /releases/{id}` cache (`redis.asyncio`) |

## Conventions

- All YAML files start with `---` and end with `...` followed by an empty line.
- Single quotes for Python string literals — enforced by ruff (`quote-style = "single"` in `pyproject.toml`), run via pre-commit and the `lint.yml` CI workflow.
- Secrets never in files — always injected via env vars into the container.
- `inventory.html` and `collection.html` derive their API base URL from `window.location.pathname` at runtime (subpath-aware).
- `collection.html` lists a user's collection folders and lets the owner list items for sale (single item, or a queue that reopens the same form per selected item so each lot gets its own condition/price) via `POST /marketplace/listings`; each successfully listed item is then removed from the collection folder via `DELETE /users/{username}/collection/folders/{folder_id}/releases/{release_id}/instances/{instance_id}`. The listing form prefills price from `GET /marketplace/price_suggestions/{release_id}` (rounded up) and links to `discogs.com/sell/history/{release_id}`.
- `collection.html` also lets the owner remove items from the collection directly (no listing involved), via the same `DELETE .../instances/{instance_id}` endpoint used post-listing: a per-row "Remove" button (`removeSingleItem`) with a confirm dialog, or a bulk "Remove from collection" button in the bulk-bar (`removeSelected`) that confirms once for the whole selection and deletes each selected item sequentially, tolerating individual failures (reports removed/failed counts via toast). Both paths share `removeItemFromState`/`afterRemoval` to update `_items`/`_allItems`/`_selected` and re-render (filtered page, current page, or bump back a page if it's now empty) the same way `submitModal` does after a listing.
- `collection.html` columns are sortable by clicking the header (toggles asc/desc). Artist/Title/Format/Label/Cat#/Year/Rating/Added map to the API's `sort` query param (server-side, works across the whole paginated collection); Folder/Notes have no API sort support, so those are sorted client-side and only affect the currently loaded page. There is no Low/Median/High price column — the Discogs API does not expose historical sold-price statistics anywhere.
- `collection.html` supports multi-column sort: Shift+click a header to add it as a tie-breaker after the primary sort (badges show priority order, e.g. `Artist ▲¹ Year ▲²`). Only the primary key is requested from the API; secondary+ keys are applied client-side and only reorder the currently loaded page, since Discogs has no multi-column `sort` param. A plain click resets back to a single-column sort.
- `inventory.html` mirrors `collection.html`'s sort system on the `/users/{username}/inventory` endpoint's own `sort` enum (`artist, item, price, catno, audio, status, location, listed`; `label` is a valid API key too but has no field to attach a column to, so it's omitted); Format/Year/Condition/Qty are client-side/page-only, same caveat as Folder/Notes on `collection.html`.
- Both `collection.html` and `inventory.html` remember the last-used sort per browser via `localStorage` (`discogs_collection_sort` / `discogs_inventory_sort`, written in `onHeaderClick`) and restore it on load — an explicit `?sort=` in the URL still takes priority over the stored value.
- Artist/Title/Label are clickable on both pages, but the two APIs differ: `collection.html`'s `CollectionBasicInformation` includes real `artists[].id`/`labels[].id`, so those link directly to `discogs.com/artist|label/{id}`. `inventory.html`'s `ListingReleaseRef` only has a flat `artist` name string and no label field at all — Artist/Cat# there are lazy-linked: clicking calls `GET /releases/{release_id}` (which does have full `artists[]`/`labels[]` with ids) on demand and opens the resolved page in a new tab, rather than eagerly fetching it for every row (avoids an N+1 burst on a 100-row page). Results are cached per `release_id` in an in-memory `Map` for the tab's lifetime — see `fetchReleaseCached`/`openArtistPage`/`openLabelPage`.
- `collection.html`'s `basic_information.formats` is a real array (a release can have more than one distinct format entry, e.g. mixed bitrate files or a box set). `renderFormatCell` renders each entry as its own block: the name/qty/text stays a single header line, but that entry's own `descriptions` (e.g. `7", 45 RPM, Limited Edition, Stereo`) are each broken out into a `<li>` instead of one comma-joined run-on line — multiple format entries stack as multiple header+list blocks. `releaseFormats`/`releaseFormat` (the flat " · "-joined single-line summary, used for the sort key and the modal's release-info line) are unaffected. `inventory.html`'s `release.format` (`ListingReleaseRef.format` in the OpenAPI spec) is instead a single, already-flattened string from Discogs (e.g. `"Vinyl, LP, Album, Limited Edition, Stereo"`) with no structured array underneath — `formatParts(rel)` splits it on comma/trims as the best available substitute, and `renderFormatCell(rel)` renders the first token as the header line and the rest as `<li>` bullets, mirroring `collection.html`'s cell visually despite the different underlying data shape. The raw joined `rel.format` string itself (used for sorting and the modal's release-info line) is unaffected.
- Both pages support **global filters** (Artist/Format/Label/Year/Rating on `collection.html`; Artist/Format/Condition/Location/Audio on `inventory.html`) — opt-in via an "Enable filters" button, since Discogs has no filter-by-column query param and making these global (rather than page-scoped, unlike sort) means fetching the *entire* folder/status-filtered inventory into memory first (`enableFilters()`, paginated fetch loop at `per_page=100`). Once loaded, `_allItems`/`_allListings` holds everything, filter dropdowns are populated from its actual distinct values (`buildFilterOptions`), and `renderFilteredPage(page)` client-side filters + sorts + paginates that in-memory set — `load()`/`renderFilteredPage()` share rendering via `renderTablePage()`. Changing username/folder (`collection.html`) or username/status (`inventory.html`) invalidates the loaded set (`resetFilters()`, wired through `onFolderChange`/`onStatusChange`); per-page changes just re-paginate whichever mode is active (`onControlChange` → `goToPage`). `collection.html`'s Format filter matches against every part of a format entry (name, text, and each description), not just the top-level name — so e.g. "45 RPM" or "Limited Edition" are valid filter values, not only "Vinyl"/"CD". `inventory.html`'s Format filter does the analogous thing for its flat `rel.format` string via the same `formatParts()` comma-split (see above), so individual tokens are independently selectable there too, not just the whole joined string as one option.
- Each filter dropdown is a **custom searchable combobox** (`combo.js`'s `initCombo`, markup + CSS classes `.combo`/`.combo-toggle`/`.combo-menu`/`.combo-search`/`.combo-list`/`.combo-option` in `theme.css`), not a native `<select>` — native select popups cap their own visible height at a fixed browser constant regardless of available screen space (confirmed empirically: shrinking the select's font bought only one extra visible row out of ~100px of unused space below), so long option lists (e.g. Artist/Label on a large collection) got stuck showing a handful of rows with no way to see the rest. The combo renders its own list under CSS control (`.combo-list { max-height: 260px; overflow-y: auto; }`) with a type-to-filter search box on top. Both `collection.html` and `inventory.html` generate their filter row's `<label>`+combo markup from `FILTER_COLUMNS` at load time (`initFilterCombos()`, inserted via `insertAdjacentHTML` into `#filters-row`, ahead of that bar's static action buttons) and keep a `_filterCombos` map of `{key: comboController}`; `buildFilterOptions()` calls `_filterCombos[key].setOptions(sortedValues)` and `clearFilters()` calls `_filterCombos[key].setValue('')` — `onFilterChange(key, value)` itself is unchanged, just now invoked from the combo's `onChange` callback instead of a `<select>`'s `onchange`.
- `collection.html`'s full-collection fetch for filters is cached client-side in `localStorage` per `(username, folder)`, keyed `discogs_collection_filter_cache:{username}::{folderId}` with a 1-hour TTL (`FILTER_CACHE_TTL_MS`) — `enableFilters()` reads this cache first and only falls back to the paginated fetch loop on a miss/expiry, so re-enabling filters for the same folder is instant instead of re-walking every page. A "↻ Refresh" button in the filters bar (`refreshFilterData()`) forces a bypass + refetch, since Discogs data can change outside this UI. `removeItemFromState`/`afterRemoval` (shared by `removeSingleItem`, `removeSelected`, and `submitModal` after a successful listing) keep both `_allItems` and this cache (`syncFilterCacheAfterMutation`) in sync with every mutation, so it doesn't go stale mid-session regardless of whether the item left the collection via a listing or a direct removal.
- All server-side redirects (`oauth_revoke`, `oauth_start`→Discogs, the `success.html` auto-forward) build the target from `BASE_URL`, never a bare `/` — the app is mounted at `zelgray.work/discogs` behind nginx, so a bare-root redirect would escape the subpath and land on the domain root instead of the app's home page.
- Post-login flow: `oauth_callback` renders `success.html`, which shows a "Connected as X" confirmation and then auto-forwards to `/collection` after ~1.5s via JS (`Continue →` button as the immediate-click fallback) — landing straight in Collection is the intended default after signing in. `home.html` itself never auto-redirects on its own — it's a stable hub page that, once authorized, shows the header dropdown plus the same 3 links (API/Inventory/Collection) rendered as buttons in the page body (`nav.js`'s `initDiscogsNav(headerContainerId, linksContainerId)` — the second, optional arg populates a body-level links panel, used only by `home.html`).
- `inventory.html` mirrors `collection.html`'s column/sort system: Artist/Item/Cat#/Price/Status/Audio/Location/Listed map to the `/users/{username}/inventory` endpoint's `sort` enum (server-side); Format/Year/Condition/Qty have no API sort support and are sorted client-side, current page only. The API also has a `label` sort key, but there's no label-name field in `InventoryListingItem`/`ListingReleaseRef` to attach a column to, so it's intentionally omitted. Same Shift+click multi-column behavior as `collection.html`.
- All 5 pages (`home.html`, `docs.html`, `success.html`, `inventory.html`, `collection.html`) are plain HTML files under `sources/`, served via `_render_static_page()` — none of proxy.py's HTML is inlined as Python f-strings anymore. They share one dark theme (`theme.css`, palette matches zelgray.work's personal-card site) and one header (`<header class="site-header">` + `<div id="site-nav">`, populated by `nav.js`'s `initDiscogsNav('site-nav')`). The header shows a "Sign in with Discogs" button when unauthorized, or the username + a page-switcher `<select>` (API/Inventory/Collection) + "Sign out" once authorized — Inventory/Collection/API are only reachable through that dropdown once signed in; `home.html` itself has no other content, everything functional lives on the other 4 pages.
- `_render_static_page(path, **placeholders)` reads an HTML file and replaces literal `__TOKEN__` tokens: `__THEME_CSS_VERSION__`/`__NAV_JS_VERSION__`/`__COMBO_JS_VERSION__` always (an 8-char content hash, recomputed fresh on every request — cache-busting without a build step or server restart), plus any extra tokens the caller passes as kwargs (e.g. `success.html` also takes `__BASE_URL__`/`__USERNAME__`, since `oauth_callback` needs to fill in the signed-in username and — being nested two segments deep at `/oauth/callback` — can't use `theme.css`'s plain relative-path trick the other pages use). `home.html`/`docs.html`/`inventory.html`/`collection.html` all link `theme.css`/`nav.js`/`combo.js` with plain relative hrefs (`href="theme.css?v=__THEME_CSS_VERSION__"`), which resolve correctly because every one of those routes is exactly one path segment deep under whatever prefix nginx mounts the app at.
