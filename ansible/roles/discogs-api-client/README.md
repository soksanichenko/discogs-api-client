# discogs-api-client

Deploys the discogs-api-client proxy as a Docker container on `zelgray.work`, served at a configurable nginx subpath.

## What it does

- Creates build context directory and copies `sources/` from the control machine
- Copies `Dockerfile` from role `files/` into the build context
- Builds the Docker image locally on the target host
- Runs the container on the `active` Docker network with secrets injected as env vars
- Deploys nginx upstream and location configs

## Variables

| Variable | Default | Description |
|---|---|---|
| `discogs_api_client_container_name` | `discogs-api-client` | Docker container and image name |
| `discogs_api_client_port` | `8777` | Port the app listens on inside the container |
| `discogs_api_client_data_path` | `{{ docker_volumes_directory }}/discogs-api-client` | Base path for the Docker build context on the host |
| `discogs_api_client_consumer_key` | _(empty)_ | Discogs OAuth consumer key |
| `discogs_api_client_consumer_secret` | _(empty)_ | Discogs OAuth consumer secret |
| `discogs_api_client_secret_key` | _(empty)_ | Key used to sign OAuth session cookies (`DISCOGS_SECRET_KEY`) — must stay stable across deploys or existing sessions are invalidated |
| `discogs_api_client_redis_url` | `redis://{{ redis_container_name }}:6379` | Redis URL (`DISCOGS_REDIS_URL`) for caching `GET /releases/{id}` lookups; points at the shared `redis` role's container, not a dedicated one |
| `discogs_api_client_base_url` | _(empty)_ | Public base URL (e.g. `https://zelgray.work/discogs`) |
| `discogs_api_client_nginx_location` | `/discogs` | nginx subpath location prefix |
| `discogs_api_client_nginx_custom_locations_path` | `{{ nginx_custom_locations_path }}` | Directory where the nginx location config is deployed |
| `discogs_api_client_upstream_name` | `{{ discogs_api_client_container_name }}` | nginx upstream block name |

## Tags

| Tag | Effect |
|---|---|
| `discogs-api-client` | Run all tasks |

## Usage

```bash
cd ansible
ansible-playbook playbooks/deploy.yml
```

## Notes

- Deployment is from the local working copy — no git push required before deploying.
- The nginx location config is placed in `discogs_api_client_nginx_custom_locations_path` (not `nginx_locations_path`), which is included inside the `zelgray.work` server block only.
- OAuth tokens live in a signed, httpOnly session cookie in each visitor's browser (not on the server) — each browser authorizes and proxies independently.
- Secrets (`discogs-consumer-key`, `discogs-consumer-secret`, `discogs-secret-key`) must exist in Infisical under `/hosts/zelgray-work`.
- Requires the shared `redis` role deployed first (`ansible-playbook playbooks/redis.yml` in `infra`) — this role does not deploy its own Redis container. Caching is best-effort: if Redis is unreachable, the app logs a warning and proxies every request uncached instead of failing.
