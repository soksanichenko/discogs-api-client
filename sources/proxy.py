#!/usr/bin/env python3
"""
Local Swagger UI proxy for interactive Discogs API testing.

Serves Swagger UI at http://localhost:PORT/docs and transparently proxies all
requests to https://api.discogs.com. The site root (/) is just a sign-in
landing page linking to /docs, /inventory, and /collection.

OAuth 1.0a: set DISCOGS_CONSUMER_KEY + DISCOGS_CONSUMER_SECRET (in .env or
environment), then visit /oauth/start. Once authorized, all proxied requests
are signed automatically — no need to touch Swagger UI's Authorize dialog.

The access token is kept in a signed, httpOnly session cookie (not on the
server), so each browser/user authorizes and proxies independently. Set
DISCOGS_SECRET_KEY to a stable random value in production so sessions survive
restarts; without it, an ephemeral key is generated at startup.

Manual auth still works: click "Authorize" in Swagger UI and fill one of:
  discogsToken     →  Discogs token=YOUR_PERSONAL_ACCESS_TOKEN
  discogsKeySecret →  Discogs key=YOUR_KEY, secret=YOUR_SECRET

Set DISCOGS_REDIS_URL (e.g. redis://redis:6379) to cache GET /releases/{id}
lookups — release metadata rarely changes and this is hit a lot for on-demand
artist/label link resolution. Caching is skipped entirely if unset.
"""

import hashlib
import html
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import redis.asyncio as aioredis
import uvicorn
import yaml
from dotenv import load_dotenv
from oauthlib.oauth1 import Client as OAuth1Client
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')
logger = logging.getLogger(__name__)

DISCOGS_BASE = 'https://api.discogs.com'
PORT = 8777
SPEC_PATH = Path(__file__).parent / 'discogs-openapi.yaml'
HOME_PATH = Path(__file__).parent / 'home.html'
DOCS_PATH = Path(__file__).parent / 'docs.html'
SUCCESS_PATH = Path(__file__).parent / 'success.html'
INVENTORY_PATH = Path(__file__).parent / 'inventory.html'
COLLECTION_PATH = Path(__file__).parent / 'collection.html'
THEME_CSS_PATH = Path(__file__).parent / 'theme.css'
NAV_JS_PATH = Path(__file__).parent / 'nav.js'

CONSUMER_KEY = os.environ.get('DISCOGS_CONSUMER_KEY', '')
CONSUMER_SECRET = os.environ.get('DISCOGS_CONSUMER_SECRET', '')
BASE_URL = os.environ.get('DISCOGS_BASE_URL', f'http://localhost:{PORT}')

SECRET_KEY = os.environ.get('DISCOGS_SECRET_KEY', '')
if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        'DISCOGS_SECRET_KEY not set — using an ephemeral key; sessions will not '
        'survive a restart'
    )

REDIS_URL = os.environ.get('DISCOGS_REDIS_URL', '')
CACHE_TTL = (
    60 * 60 * 24 * 30
)  # 30 days — release metadata (esp. artist/label ids) is effectively static
_redis: aioredis.Redis | None = None

# GET requests to these Discogs paths are cached in Redis (when configured) —
# release lookups are hit a lot for on-demand artist/label link resolution
# (see inventory.html's openArtistPage/openLabelPage) and rarely change.
_CACHEABLE_GET_PATHS = (re.compile(r'^releases/\d+$'),)


async def _cache_get(key: str) -> bytes | None:
    if not _redis:
        return None
    try:
        return await _redis.get(key)
    except Exception:
        logger.warning('Redis GET failed for %s — treating as cache miss', key)
        return None


async def _cache_set(key: str, value: bytes) -> None:
    if not _redis:
        return
    try:
        await _redis.set(key, value, ex=CACHE_TTL)
    except Exception:
        logger.warning('Redis SET failed for %s — continuing without caching', key)


@asynccontextmanager
async def lifespan(app):
    global _redis
    if REDIS_URL:
        _redis = aioredis.from_url(REDIS_URL)
        try:
            await _redis.ping()
            logger.info('Connected to Redis cache at %s', REDIS_URL)
        except Exception:
            logger.warning(
                'Could not reach Redis at %s — proceeding without caching', REDIS_URL
            )
            _redis = None
    else:
        logger.info('DISCOGS_REDIS_URL not set — release lookups will not be cached')
    yield
    if _redis:
        await _redis.aclose()


_pending_tokens: dict[str, str] = {}  # request oauth_token → oauth_token_secret


def _asset_version(path: Path) -> str:
    """Short content hash for cache-busting a static asset's URL (?v=...)."""
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Load spec and override server URL to point at this proxy
# ---------------------------------------------------------------------------
with SPEC_PATH.open() as _f:
    _spec = yaml.safe_load(_f)

_spec['servers'] = [
    {'url': BASE_URL, 'description': 'Local proxy → api.discogs.com'},
]
_SPEC_JSON = json.dumps(_spec)

# ---------------------------------------------------------------------------
# Hop-by-hop headers that must not be forwarded
# ---------------------------------------------------------------------------
_HOP_BY_HOP = frozenset(
    [
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailers',
        'transfer-encoding',
        'upgrade',
        'content-encoding',
    ]
)


def _oauth1_client(**extra) -> OAuth1Client:
    return OAuth1Client(client_key=CONSUMER_KEY, client_secret=CONSUMER_SECRET, **extra)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _render_static_page(path: Path, **placeholders: str) -> str:
    """Read an HTML page and substitute its __PLACEHOLDER__ tokens —
    theme.css/nav.js are always stamped with a cache-busting content hash,
    recomputed fresh on every request; callers may pass extra tokens."""
    content = path.read_text(encoding='utf-8')
    content = content.replace('__THEME_CSS_VERSION__', _asset_version(THEME_CSS_PATH))
    content = content.replace('__NAV_JS_VERSION__', _asset_version(NAV_JS_PATH))
    for name, value in placeholders.items():
        content = content.replace(f'__{name}__', value)
    return content


async def home_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_static_page(HOME_PATH))


async def swagger_ui(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_static_page(DOCS_PATH))


async def inventory_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_static_page(INVENTORY_PATH))


async def collection_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_render_static_page(COLLECTION_PATH))


async def theme_css(request: Request) -> Response:
    return Response(THEME_CSS_PATH.read_text(encoding='utf-8'), media_type='text/css')


async def nav_js(request: Request) -> Response:
    return Response(
        NAV_JS_PATH.read_text(encoding='utf-8'), media_type='application/javascript'
    )


async def openapi_spec(request: Request) -> Response:
    return Response(_SPEC_JSON, media_type='application/json')


async def oauth_revoke(request: Request) -> RedirectResponse:
    """Clear this browser's OAuth session and redirect to the home page."""
    request.session.clear()
    return RedirectResponse(f'{BASE_URL}/')


async def oauth_status(request: Request) -> JSONResponse:
    if request.session.get('token'):
        username = request.session.get('username', '')
        if not username:
            username = await _fetch_username(request)
        return JSONResponse(
            {'authorized': True, 'configured': True, 'username': username}
        )
    return JSONResponse(
        {'authorized': False, 'configured': bool(CONSUMER_KEY and CONSUMER_SECRET)}
    )


async def _fetch_username(request: Request) -> str:
    """Fetch the authenticated user's username from /oauth/identity and cache it in the session."""
    try:
        oauth_client = _oauth1_client(
            resource_owner_key=request.session['token'],
            resource_owner_secret=request.session['secret'],
        )
        _, headers, _ = oauth_client.sign(
            f'{DISCOGS_BASE}/oauth/identity', http_method='GET'
        )
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(f'{DISCOGS_BASE}/oauth/identity', headers=headers)
        if resp.status_code == 200:
            username = resp.json().get('username', '')
            request.session['username'] = username
            return username
    except Exception:
        logger.warning('Could not fetch username from /oauth/identity')
    return ''


async def oauth_start(request: Request) -> Response:
    """Step 1: get a request token and redirect user to Discogs authorization page."""
    if not (CONSUMER_KEY and CONSUMER_SECRET):
        return HTMLResponse(
            '<h1>OAuth not configured</h1>'
            '<p>Set <code>DISCOGS_CONSUMER_KEY</code> and <code>DISCOGS_CONSUMER_SECRET</code>.</p>',
            status_code=500,
        )

    callback_url = f'{BASE_URL}/oauth/callback'
    client = _oauth1_client(callback_uri=callback_url)
    uri, headers, _ = client.sign(
        'https://api.discogs.com/oauth/request_token', http_method='POST'
    )
    headers['User-Agent'] = 'discogs-proxy/1.0'

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(uri, headers=headers)

    if resp.status_code != 200:
        logger.error('Request token failed %s: %s', resp.status_code, resp.text)
        return HTMLResponse(
            f'<h1>Error</h1><p>Discogs returned {resp.status_code}: {resp.text}</p>',
            status_code=502,
        )

    params = dict(parse_qsl(resp.text))
    oauth_token = params.get('oauth_token', '')
    oauth_token_secret = params.get('oauth_token_secret', '')

    if not oauth_token:
        return HTMLResponse(
            '<h1>Error</h1><p>No oauth_token in Discogs response.</p>', status_code=502
        )

    _pending_tokens[oauth_token] = oauth_token_secret
    logger.info('OAuth: redirecting to Discogs authorize (token=%s…)', oauth_token[:8])

    return RedirectResponse(
        f'https://www.discogs.com/oauth/authorize?oauth_token={oauth_token}'
    )


async def oauth_callback(request: Request) -> HTMLResponse:
    """Step 2: exchange request token + verifier for an access token."""
    oauth_token = request.query_params.get('oauth_token', '')
    oauth_verifier = request.query_params.get('oauth_verifier', '')

    if not oauth_token or not oauth_verifier:
        return HTMLResponse(
            '<h1>Error</h1><p>Missing oauth_token or oauth_verifier.</p>',
            status_code=400,
        )

    oauth_token_secret = _pending_tokens.pop(oauth_token, '')
    if not oauth_token_secret:
        return HTMLResponse(
            '<h1>Error</h1><p>Unknown oauth_token — please <a href="/oauth/start">start again</a>.</p>',
            status_code=400,
        )

    client = _oauth1_client(
        resource_owner_key=oauth_token,
        resource_owner_secret=oauth_token_secret,
        verifier=oauth_verifier,
    )
    uri, headers, _ = client.sign(
        'https://api.discogs.com/oauth/access_token', http_method='POST'
    )
    headers['User-Agent'] = 'discogs-proxy/1.0'

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(uri, headers=headers)

    if resp.status_code != 200:
        logger.error('Access token failed %s: %s', resp.status_code, resp.text)
        return HTMLResponse(
            f'<h1>Error</h1><p>Discogs returned {resp.status_code}: {resp.text}</p>',
            status_code=502,
        )

    params = dict(parse_qsl(resp.text))
    username = params.get('oauth_username', '')
    request.session['token'] = params.get('oauth_token', '')
    request.session['secret'] = params.get('oauth_token_secret', '')
    request.session['username'] = username
    logger.info('OAuth: authorized as %s', username)

    return HTMLResponse(
        _render_static_page(
            SUCCESS_PATH, BASE_URL=BASE_URL, USERNAME=html.escape(username)
        )
    )


async def proxy(request: Request) -> Response:
    """Transparent proxy: forward request → Discogs → return response."""
    path = request.path_params.get('path', '')
    url = f'{DISCOGS_BASE}/{path}'

    cache_key = None
    if request.method == 'GET' and any(p.match(path) for p in _CACHEABLE_GET_PATHS):
        qs = urlencode(sorted(request.query_params.items()))
        cache_key = f'discogs:{path}' + (f'?{qs}' if qs else '')
        cached = await _cache_get(cache_key)
        if cached is not None:
            return Response(cached, media_type='application/json')

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'host', 'content-length', 'accept-encoding'}
    }
    headers.setdefault(
        'User-Agent',
        'discogs-api-client-proxy/1.0 +https://github.com/you/discogs-api-client',
    )

    # Auto-sign with OAuth when this browser's session is authorized and no
    # manual Authorization header is present.
    token = request.session.get('token')
    secret = request.session.get('secret')
    if token and secret and 'authorization' not in {k.lower() for k in headers}:
        params = dict(request.query_params)
        sign_url = url + ('?' + urlencode(params) if params else '')
        oauth_client = _oauth1_client(
            resource_owner_key=token, resource_owner_secret=secret
        )
        _, oauth_headers, _ = oauth_client.sign(
            sign_url, http_method=request.method.upper()
        )
        headers['Authorization'] = oauth_headers['Authorization']

    body = await request.body()
    logger.info('%s %s', request.method, url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
        resp = await http.request(
            method=request.method,
            url=url,
            headers=headers,
            params=dict(request.query_params),
            content=body or None,
        )

    resp_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'content-length'}
    }

    logger.info('← %s %s', resp.status_code, url)
    if cache_key and resp.status_code == 200:
        await _cache_set(cache_key, resp.content)
    return Response(resp.content, status_code=resp.status_code, headers=resp_headers)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Starlette(
    lifespan=lifespan,
    routes=[
        Route('/', home_page),
        Route('/docs', swagger_ui),
        Route('/openapi.json', openapi_spec),
        Route('/inventory', inventory_page),
        Route('/collection', collection_page),
        Route('/theme.css', theme_css),
        Route('/nav.js', nav_js),
        Route('/oauth/status', oauth_status),
        Route('/oauth/start', oauth_start),
        Route('/oauth/callback', oauth_callback),
        Route('/oauth/revoke', oauth_revoke),
        Route(
            '/{path:path}',
            proxy,
            methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'],
        ),
    ],
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie='discogs_session',
    max_age=60 * 60 * 24 * 365,  # 1 year — Discogs access tokens don't expire
    same_site='lax',
    https_only=BASE_URL.startswith('https://'),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['*'],
)

if __name__ == '__main__':
    print(f'\n  Home        → http://localhost:{PORT}/')
    print(f'  Swagger UI  → http://localhost:{PORT}/docs')
    print(f'  Inventory   → http://localhost:{PORT}/inventory')
    print(f'  Collection  → http://localhost:{PORT}/collection')
    if CONSUMER_KEY:
        print(f'  OAuth start → http://localhost:{PORT}/oauth/start')
    else:
        print('  OAuth: set DISCOGS_CONSUMER_KEY + DISCOGS_CONSUMER_SECRET to enable')
    if REDIS_URL:
        print(f'  Cache       → {REDIS_URL}')
    else:
        print('  Cache: set DISCOGS_REDIS_URL to cache release lookups')
    print()
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')
