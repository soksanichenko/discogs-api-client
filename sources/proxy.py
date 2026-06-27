#!/usr/bin/env python3
"""
Local Swagger UI proxy for interactive Discogs API testing.

Serves Swagger UI at http://localhost:PORT and transparently proxies all
requests to https://api.discogs.com.

OAuth 1.0a: set DISCOGS_CONSUMER_KEY + DISCOGS_CONSUMER_SECRET (in .env or
environment), then visit /oauth/start. Once authorized, all proxied requests
are signed automatically — no need to touch Swagger UI's Authorize dialog.

Manual auth still works: click "Authorize" in Swagger UI and fill one of:
  discogsToken     →  Discogs token=YOUR_PERSONAL_ACCESS_TOKEN
  discogsKeySecret →  Discogs key=YOUR_KEY, secret=YOUR_SECRET
"""
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import uvicorn
import yaml
from dotenv import load_dotenv
from oauthlib.oauth1 import Client as OAuth1Client
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')
logger = logging.getLogger(__name__)

DISCOGS_BASE = 'https://api.discogs.com'
PORT = 8777
SPEC_PATH = Path(__file__).parent / 'discogs-openapi.yaml'
INVENTORY_PATH = Path(__file__).parent / 'inventory.html'

CONSUMER_KEY = os.environ.get('DISCOGS_CONSUMER_KEY', '')
CONSUMER_SECRET = os.environ.get('DISCOGS_CONSUMER_SECRET', '')
BASE_URL = os.environ.get('DISCOGS_BASE_URL', f'http://localhost:{PORT}')
TOKEN_PATH = Path(os.environ.get('DISCOGS_TOKEN_DIR', str(Path(__file__).parent))) / '.oauth_token.json'

_pending_tokens: dict[str, str] = {}  # request oauth_token → oauth_token_secret


def _load_token() -> dict | None:
    """Load persisted OAuth access token from disk, if present."""
    try:
        return json.loads(TOKEN_PATH.read_text())
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning('Could not read %s — ignoring', TOKEN_PATH)
        return None


def _save_token(token: dict) -> None:
    """Persist OAuth access token to disk with owner-only permissions."""
    TOKEN_PATH.write_text(json.dumps(token))
    TOKEN_PATH.chmod(0o600)
    logger.info('OAuth token saved to %s', TOKEN_PATH)


def _clear_token() -> None:
    """Delete the persisted OAuth token file."""
    TOKEN_PATH.unlink(missing_ok=True)
    logger.info('OAuth token cleared')


_oauth_access: dict | None = _load_token()  # {'token', 'secret', 'username'}

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
# Swagger UI HTML
# ---------------------------------------------------------------------------
_SWAGGER_HTML = f"""<!DOCTYPE html>
<html>
<head>
  <title>Discogs API — local proxy</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    #oauth-banner {{
      padding: 10px 20px;
      font-family: sans-serif;
      font-size: 14px;
      border-bottom: 1px solid #e0e0e0;
    }}
    #oauth-banner a {{ font-weight: bold; color: #1565c0; }}
    #oauth-banner code {{ background: #f5f5f5; padding: 1px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
<div id="oauth-banner">Checking OAuth status…</div>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
  SwaggerUIBundle({{
    url: '{BASE_URL}/openapi.json',
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: 'BaseLayout',
    persistAuthorization: true,
    tryItOutEnabled: true,
  }});

  fetch('{BASE_URL}/oauth/status').then(r => r.json()).then(data => {{
    const banner = document.getElementById('oauth-banner');
    if (data.authorized) {{
      banner.style.background = '#e8f5e9';
      banner.innerHTML = '&#10003; OAuth: connected as <strong>' + data.username + '</strong> — requests are signed automatically &nbsp;&middot;&nbsp; <a href="{BASE_URL}/inventory">Inventory &rarr;</a> &nbsp;&middot;&nbsp; <a href="{BASE_URL}/oauth/revoke">Revoke</a>';
    }} else if (data.configured) {{
      banner.style.background = '#fff3e0';
      banner.innerHTML = 'OAuth app configured. <a href="{BASE_URL}/oauth/start">Authorize with Discogs &rarr;</a> &nbsp;&middot;&nbsp; <a href="{BASE_URL}/inventory">Inventory &rarr;</a>';
    }} else {{
      banner.style.background = '#f5f5f5';
      banner.innerHTML = 'Set <code>DISCOGS_CONSUMER_KEY</code> + <code>DISCOGS_CONSUMER_SECRET</code> to enable OAuth, or use Authorize below. &nbsp;&middot;&nbsp; <a href="{BASE_URL}/inventory">Inventory &rarr;</a>';
    }}
  }});
</script>
</body>
</html>
"""

_SUCCESS_HTML = f"""<!DOCTYPE html>
<html>
<head><title>Authorized — Discogs</title><meta charset="utf-8"></head>
<body style="font-family:sans-serif;max-width:560px;margin:80px auto;text-align:center;color:#333">
  <div style="font-size:48px">&#10003;</div>
  <h1 style="color:#2e7d32;margin:8px 0">Connected!</h1>
  <p>Authorized as <strong>{{username}}</strong>.<br>
  All API requests through this proxy are now signed automatically.</p>
  <a href="{BASE_URL}" style="display:inline-block;margin-top:24px;padding:10px 28px;
     background:#1565c0;color:#fff;border-radius:4px;text-decoration:none;font-size:15px">
    Open Swagger UI &rarr;
  </a>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Hop-by-hop headers that must not be forwarded
# ---------------------------------------------------------------------------
_HOP_BY_HOP = frozenset([
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding',
])


def _oauth1_client(**extra) -> OAuth1Client:
    return OAuth1Client(client_key=CONSUMER_KEY, client_secret=CONSUMER_SECRET, **extra)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def swagger_ui(request: Request) -> HTMLResponse:
    return HTMLResponse(_SWAGGER_HTML)


async def inventory_page(request: Request) -> HTMLResponse:
    return HTMLResponse(INVENTORY_PATH.read_text(encoding='utf-8'))


async def openapi_spec(request: Request) -> Response:
    return Response(_SPEC_JSON, media_type='application/json')


async def oauth_revoke(request: Request) -> RedirectResponse:
    """Clear the stored OAuth token and redirect to the home page."""
    global _oauth_access
    _oauth_access = None
    _clear_token()
    return RedirectResponse('/')


async def oauth_status(request: Request) -> JSONResponse:
    if _oauth_access:
        username = _oauth_access.get('username', '')
        if not username:
            username = await _fetch_username()
        return JSONResponse({'authorized': True, 'configured': True, 'username': username})
    return JSONResponse({'authorized': False, 'configured': bool(CONSUMER_KEY and CONSUMER_SECRET)})


async def _fetch_username() -> str:
    """Fetch the authenticated user's username from /oauth/identity and cache it."""
    global _oauth_access
    try:
        oauth_client = _oauth1_client(
            resource_owner_key=_oauth_access['token'],
            resource_owner_secret=_oauth_access['secret'],
        )
        _, headers, _ = oauth_client.sign(
            f'{DISCOGS_BASE}/oauth/identity', http_method='GET'
        )
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(f'{DISCOGS_BASE}/oauth/identity', headers=headers)
        if resp.status_code == 200:
            username = resp.json().get('username', '')
            _oauth_access['username'] = username
            _save_token(_oauth_access)
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
    uri, headers, _ = client.sign('https://api.discogs.com/oauth/request_token', http_method='POST')
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
        return HTMLResponse('<h1>Error</h1><p>No oauth_token in Discogs response.</p>', status_code=502)

    _pending_tokens[oauth_token] = oauth_token_secret
    logger.info('OAuth: redirecting to Discogs authorize (token=%s…)', oauth_token[:8])

    return RedirectResponse(f'https://www.discogs.com/oauth/authorize?oauth_token={oauth_token}')


async def oauth_callback(request: Request) -> HTMLResponse:
    """Step 2: exchange request token + verifier for an access token."""
    global _oauth_access

    oauth_token = request.query_params.get('oauth_token', '')
    oauth_verifier = request.query_params.get('oauth_verifier', '')

    if not oauth_token or not oauth_verifier:
        return HTMLResponse('<h1>Error</h1><p>Missing oauth_token or oauth_verifier.</p>', status_code=400)

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
    uri, headers, _ = client.sign('https://api.discogs.com/oauth/access_token', http_method='POST')
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
    _oauth_access = {
        'token': params.get('oauth_token', ''),
        'secret': params.get('oauth_token_secret', ''),
        'username': params.get('oauth_username', ''),
    }
    _save_token(_oauth_access)
    logger.info('OAuth: authorized as %s', _oauth_access['username'])

    return HTMLResponse(_SUCCESS_HTML.format(username=_oauth_access['username']))


async def proxy(request: Request) -> Response:
    """Transparent proxy: forward request → Discogs → return response."""
    path = request.path_params.get('path', '')
    url = f'{DISCOGS_BASE}/{path}'

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'host', 'content-length', 'accept-encoding'}
    }
    headers.setdefault('User-Agent', 'discogs-api-client-proxy/1.0 +https://github.com/you/discogs-api-client')

    # Auto-sign with OAuth when authorized and no manual Authorization header is present.
    if _oauth_access and 'authorization' not in {k.lower() for k in headers}:
        params = dict(request.query_params)
        sign_url = url + ('?' + urlencode(params) if params else '')
        oauth_client = _oauth1_client(
            resource_owner_key=_oauth_access['token'],
            resource_owner_secret=_oauth_access['secret'],
        )
        _, oauth_headers, _ = oauth_client.sign(sign_url, http_method=request.method.upper())
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
        k: v for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'content-length'}
    }

    logger.info('← %s %s', resp.status_code, url)
    return Response(resp.content, status_code=resp.status_code, headers=resp_headers)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Starlette(routes=[
    Route('/',                swagger_ui),
    Route('/docs',            swagger_ui),
    Route('/openapi.json',    openapi_spec),
    Route('/inventory',       inventory_page),
    Route('/oauth/status',    oauth_status),
    Route('/oauth/start',     oauth_start),
    Route('/oauth/callback',  oauth_callback),
    Route('/oauth/revoke',    oauth_revoke),
    Route('/{path:path}',     proxy, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD']),
])

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['*'],
)

if __name__ == '__main__':
    print(f'\n  Swagger UI  → http://localhost:{PORT}/')
    print(f'  Inventory   → http://localhost:{PORT}/inventory')
    if CONSUMER_KEY:
        print(f'  OAuth start → http://localhost:{PORT}/oauth/start')
    else:
        print('  OAuth: set DISCOGS_CONSUMER_KEY + DISCOGS_CONSUMER_SECRET to enable')
    print()
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')
