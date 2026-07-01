# discogs-api-client

Deploys the discogs-api-client proxy as a Docker container on `zelgray.work`, served at a configurable nginx subpath.

## What it does

- Creates build context directory and copies `sources/` from the control machine
- Copies `Dockerfile` from role `files/` into the build context
- Builds the Docker image locally on the target host
- Creates a data directory for OAuth token persistence (`/app/data`)
- Runs the container on the `active` Docker network with secrets injected as env vars
- Deploys nginx upstream and location configs

## Variables

| Variable | Default | Description |
|---|---|---|
| `discogs_api_client_container_name` | `discogs-api-client` | Docker container and image name |
| `discogs_api_client_port` | `8777` | Port the app listens on inside the container |
| `discogs_api_client_data_path` | `{{ docker_volumes_directory }}/discogs-api-client` | Base path for build context and data dir on the host |
| `discogs_api_client_consumer_key` | _(empty)_ | Discogs OAuth consumer key |
| `discogs_api_client_consumer_secret` | _(empty)_ | Discogs OAuth consumer secret |
| `discogs_api_client_base_url` | _(empty)_ | Public base URL (e.g. `https://zelgray.work/discogs`) |
| `discogs_api_client_nginx_location` | `/discogs` | nginx subpath location prefix |
| `discogs_api_client_nginx_custom_locations_path` | `{{ nginx_custom_locations_path }}` | Directory where the nginx location config is deployed |
| `discogs_api_client_upstream_name` | `discogs_api_client_upstream` | nginx upstream block name |

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
- OAuth token is persisted to `discogs_api_client_data_path/data/.oauth_token.json` via bind mount.
- Secrets (`discogs-consumer-key`, `discogs-consumer-secret`) must exist in Infisical under `/hosts/zelgray-work`.
