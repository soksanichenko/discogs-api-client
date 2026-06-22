#!/usr/bin/env python3
"""
Local Swagger UI proxy for interactive Discogs API testing.

Serves Swagger UI at http://localhost:8765 and transparently proxies all
requests to https://api.discogs.com, forwarding auth headers as-is.

Authentication in Swagger UI — click "Authorize" and fill ONE of:
  discogsToken    →  value: Discogs token=YOUR_PERSONAL_ACCESS_TOKEN
  discogsKeySecret →  value: Discogs key=YOUR_KEY, secret=YOUR_SECRET
"""
import json
import logging
from pathlib import Path

import httpx
import uvicorn
import yaml
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')
logger = logging.getLogger(__name__)

DISCOGS_BASE = 'https://api.discogs.com'
PORT = 8777
SPEC_PATH = Path(__file__).parent / 'discogs-openapi.yaml'

# ---------------------------------------------------------------------------
# Load spec and override server URL to point at this proxy
# ---------------------------------------------------------------------------
with SPEC_PATH.open() as _f:
    _spec = yaml.safe_load(_f)

_spec['servers'] = [
    {'url': f'http://localhost:{PORT}', 'description': 'Local proxy → api.discogs.com'},
]
_SPEC_JSON = json.dumps(_spec)

# ---------------------------------------------------------------------------
# Swagger UI HTML (assets from CDN)
# ---------------------------------------------------------------------------
_SWAGGER_HTML = f"""<!DOCTYPE html>
<html>
<head>
  <title>Discogs API — local proxy</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
  SwaggerUIBundle({{
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: 'BaseLayout',
    persistAuthorization: true,
    tryItOutEnabled: true,
    requestInterceptor: function(req) {{
      // Strip the proxy host so the request goes to the right place
      req.url = req.url.replace('http://localhost:{PORT}', 'http://localhost:{PORT}');
      return req;
    }},
  }});
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Hop-by-hop headers that must not be forwarded
# ---------------------------------------------------------------------------
_HOP_BY_HOP = frozenset([
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding',
])


async def swagger_ui(request: Request) -> HTMLResponse:
    return HTMLResponse(_SWAGGER_HTML)


async def openapi_spec(request: Request) -> Response:
    return Response(_SPEC_JSON, media_type='application/json')


async def proxy(request: Request) -> Response:
    """Transparent proxy: forward request → Discogs → return response."""
    path = request.path_params.get('path', '')
    url = f'{DISCOGS_BASE}/{path}'

    # Forward headers, drop hop-by-hop and compression negotiation.
    # We strip Accept-Encoding so Discogs returns uncompressed content;
    # otherwise httpx decompresses silently but the original Content-Length
    # (compressed size) would mismatch the actual body length in the browser.
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'host', 'content-length', 'accept-encoding'}
    }
    headers.setdefault('User-Agent', 'discogs-api-client-proxy/1.0 +https://github.com/you/discogs-api-client')

    body = await request.body()

    logger.info('%s %s', request.method, url)

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            params=dict(request.query_params),
            content=body or None,
        )

    # Also strip content-length: Starlette will set it correctly from the body.
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in _HOP_BY_HOP | {'content-length'}
    }

    logger.info('← %s %s', resp.status_code, url)
    return Response(resp.content, status_code=resp.status_code, headers=resp_headers)


app = Starlette(routes=[
    Route('/',              swagger_ui),
    Route('/docs',          swagger_ui),
    Route('/openapi.json',  openapi_spec),
    Route('/{path:path}',   proxy, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD']),
])

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['*'],
)

if __name__ == '__main__':
    print(f'\n  Swagger UI →  http://localhost:{PORT}/docs\n')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')
