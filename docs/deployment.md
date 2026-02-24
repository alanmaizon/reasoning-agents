# Deployment and Hosting (Optional)

This guide is only needed when hosting Condor in Azure.
For local usage, follow `README.md` Quick Start.

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | For online mode | Azure AI Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | For online mode | Model deployment name (for example, `gpt-4o`) |
| `AZURE_OPENAI_API_KEY` | Optional | API key for key-based auth (managed identity can be used instead) |
| `APP_LOG_LEVEL` | Optional | Runtime log level (default: `INFO`) |
| `APP_LOG_FORMAT` | Optional | `json` (default) or `plain` |
| `ENTRA_AUTH_ENABLED` | Optional | Enables Microsoft Entra ID bearer token auth on `/v1/*` routes |
| `ENTRA_TENANT_ID` | Required if auth enabled | Entra tenant ID used for OpenID metadata discovery |
| `ENTRA_AUDIENCE` / `ENTRA_AUDIENCES` | Required if auth enabled | Accepted token audience(s), comma-separated |
| `ENTRA_REQUIRED_SCOPES` | Optional | Required delegated scopes, comma-separated |
| `ENTRA_REQUIRED_ROLES` | Optional | Required app roles, comma-separated |
| `ENTRA_ISSUER` / `ENTRA_ISSUERS` | Optional | Override allowed issuer(s), comma-separated when multiple |
| `ENTRA_TENANT_DOMAIN` | Optional | External ID tenant domain (for CIAM issuer variants) |
| `ENTRA_JWKS_URI` | Optional | Override JWKS endpoint URL |
| `FRONTEND_CLIENT_ID` | Recommended when auth enabled | SPA app registration client ID used by built-in frontend |
| `FRONTEND_AUTHORITY` | Optional | Frontend authority URL (for CIAM: `https://<tenant>.ciamlogin.com`) |
| `FRONTEND_API_SCOPE` | Recommended when auth enabled | Scope requested by frontend (for example, `api://<api-app-id>/api.access`) |
| `FRONTEND_IDP_HINT` | Optional | Identity provider hint passed as `idp` (for Google-first UX, for example `Google-OAUTH`) |
| `FRONTEND_DOMAIN_HINT` | Optional | Domain hint passed as `domain_hint` (for CIAM Google-first UX use `Google`) |
| `API_RATE_LIMIT_REQUESTS_PER_MINUTE` | Optional | Max requests per identity per minute for `/v1/*` (default: `60`, set `0` to disable) |
| `API_RATE_LIMIT_WINDOW_SECONDS` | Optional | Rate-limit sliding window in seconds (default: `60`) |
| `MCP_PROJECT_CONNECTION_NAME` | Optional | MCP connection name if required |
| `POSTGRES_DSN` / `DATABASE_URL` | Optional | PostgreSQL DSN for primary state persistence |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE` | Optional | PostgreSQL discrete connection settings (used when DSN is absent) |
| `STATE_PG_TABLE` | Optional | PostgreSQL table name for user state (default: `student_state`) |
| `AZURE_STORAGE_CONNECTION_STRING` | Optional | Enables Azure Blob fallback for state/cache |
| `AZURE_STORAGE_CONTAINER` | Optional | Blob container name (default: `mdt-data`) |
| `STATE_BLOB_PREFIX` | Optional | Blob prefix for student state (default: `state`) |
| `CACHE_BLOB_NAME` | Optional | Blob path for cache JSON (default: `cache/cache.json`) |
| `STATE_DIR` | Optional | Local fallback state directory (default: `.data/state`) |

State persistence priority in hosted mode:
1. PostgreSQL (if configured)
2. Azure Blob Storage (if configured)
3. Local disk fallback (`STATE_DIR`)

Authentication behavior:
1. `/healthz` stays public
2. `/v1/*` is protected only when `ENTRA_AUTH_ENABLED=true`
3. In auth mode, backend state is bound to token identity (`oid`/`sub`); client `user_id` is ignored
4. `/` serves the built-in frontend shell

## Deploy to Azure App Service

1. Set deployment variables in your shell:
   - `RESOURCE_GROUP`, `LOCATION`, `ACR_NAME`, `APP_SERVICE_PLAN`, `WEBAPP_NAME`
   - `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`
   - Optional: `MCP_PROJECT_CONNECTION_NAME`, PostgreSQL vars, `AZURE_STORAGE_CONNECTION_STRING`
2. Run deployment:

```bash
bash scripts/azure/deploy_webapp.sh
```

Health endpoint:

```text
https://<WEBAPP_NAME>.azurewebsites.net/healthz
```

## Deploy to Azure VM

Manual deploy from your machine:

```bash
export VM_HOST=<your-vm-ip-or-dns>
export VM_USER=azureuser
export HEALTHCHECK_URL=https://<your-domain>/healthz
bash scripts/azure/deploy_vm_code.sh
```

Optional overrides:

- `VM_PORT` (default `22`)
- `APP_DIR` (default `/home/<VM_USER>/app`)
- `SERVICE_NAME` (default `mdt-api`)

Tune VM runtime:

```bash
export VM_HOST=<vm-fqdn>
export VM_USER=azureuser
export HEALTHCHECK_URL=https://<vm-fqdn>/healthz
bash scripts/azure/tune_vm_runtime.sh
```

## Entra External ID User Flow (CLI)

Create external tenant (one-time):

```bash
export RESOURCE_GROUP=rg-mdt-data-sc
export TENANT_PREFIX=condorx$(date +%m%d%H%M%S)
export DISPLAY_NAME="Condor External ID"
export COUNTRY_CODE=IE
export RESOURCE_GROUP_LOCATION=swedencentral
export DIRECTORY_LOCATION=Europe
bash scripts/azure/create_external_tenant.sh
```

Then create/reuse sign-up and sign-in flow:

```bash
export EXTERNAL_TENANT_ID=<external-tenant-guid>
export USER_FLOW_ID=B2C_1_condor_signup_signin   # optional
bash scripts/azure/setup_external_id_user_flow.sh
```

Recommended app-only Graph mode:

```bash
export EXTERNAL_TENANT_ID=<external-tenant-guid>
export GRAPH_CLIENT_ID=<app-registration-client-id>
export GRAPH_CLIENT_SECRET=<app-registration-client-secret>
export USER_FLOW_ID=B2C_1_condor_signup_signin   # optional
bash scripts/azure/setup_external_id_user_flow.sh
```

Force Google-only provider experience:

```bash
export EXTERNAL_TENANT_ID=<external-tenant-guid>
export GRAPH_CLIENT_ID=<app-registration-client-id>
export GRAPH_CLIENT_SECRET=<app-registration-client-secret>
export USER_FLOW_ID=B2C_1_condor_signup_signin
export CIAM_GOOGLE_ONLY=true
export CIAM_SYNC_IDENTITY_PROVIDERS=true
bash scripts/azure/setup_external_id_user_flow.sh
```

Notes:

- `CIAM_GOOGLE_ONLY=true` resolves desired providers to only `Google-OAUTH`.
- `CIAM_SYNC_IDENTITY_PROVIDERS=true` updates existing flow providers to match the desired set.
- Use `FRONTEND_DOMAIN_HINT=Google` (and optionally `FRONTEND_IDP_HINT=Google-OAUTH`) for Google-first sign-in UX.

## GitHub Actions CI/CD to VM

Workflow file: `.github/workflows/deploy_vm.yml`

Triggers:
1. `pull_request` to `main`: CI only
2. `push` to `main`: CI then VM deploy
3. `workflow_dispatch`: CI then VM deploy

Repository secrets:

- `VM_HOST` (required)
- `VM_SSH_PRIVATE_KEY` (required)
- `VM_USER` (optional, default `azureuser`)
- `VM_PORT` (optional, default `22`)
- `VM_APP_DIR` (optional, default `/home/<VM_USER>/app`)
- `VM_SERVICE_NAME` (optional, default `mdt-api`)
- `HEALTHCHECK_URL` (optional, recommended)

Runtime secrets (recommended in GitHub Environment `production`):

- `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- `AZURE_OPENAI_API_KEY` (optional)
- `ENTRA_AUTH_ENABLED`, `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` (or `ENTRA_AUDIENCES`)
- `ENTRA_REQUIRED_SCOPES`, `ENTRA_REQUIRED_ROLES` (optional)
- `ENTRA_ISSUER` (or `ENTRA_ISSUERS`), `ENTRA_JWKS_URI` (optional)
- `FRONTEND_CLIENT_ID`, `FRONTEND_AUTHORITY`, `FRONTEND_API_SCOPE`
- `POSTGRES_DSN` (or `DATABASE_URL` / `POSTGRES_*`)
- `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER` (optional)
- `APP_LOG_LEVEL`, `APP_LOG_FORMAT`, `API_RATE_LIMIT_REQUESTS_PER_MINUTE`, `API_RATE_LIMIT_WINDOW_SECONDS` (optional)

## Observability Setup (VM)

```bash
export RESOURCE_GROUP=<resource-group>
export LOCATION=swedencentral
export VM_NAME=<vm-name>
export HEALTHCHECK_URL=https://<your-domain>/healthz
export ALERT_EMAIL=<your-email>
bash scripts/azure/setup_observability.sh
```

This provisions:

- Log Analytics workspace
- Application Insights component + health web test
- Azure Monitor Agent + data collection rule on VM
- Action group + baseline alerts

For day-2 operations, see `docs/runbook_ops.md`.

