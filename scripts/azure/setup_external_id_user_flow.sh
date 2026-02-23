#!/usr/bin/env bash
set -euo pipefail

# Create (or reuse) a Sign-up and Sign-in user flow in an Entra External ID tenant.
#
# Required:
#   EXTERNAL_TENANT_ID   Tenant ID of the External ID tenant
#
# Optional:
#   USER_FLOW_ID         Default: B2C_1_condor_signup_signin
#
# Notes:
# - Requires Microsoft Graph delegated permissions:
#   IdentityUserFlow.Read.All or IdentityUserFlow.ReadWrite.All
# - If permissions are missing, this script prints a clear error and exits.

: "${EXTERNAL_TENANT_ID:?EXTERNAL_TENANT_ID is required}"

USER_FLOW_ID="${USER_FLOW_ID:-B2C_1_condor_signup_signin}"
GRAPH_BASE="https://graph.microsoft.com"

# Some environments (CI/sandbox) cannot write under ~/.azure/commands.
# Use a writable AZURE_CONFIG_DIR by default and copy cached profile if present.
if [[ -z "${AZURE_CONFIG_DIR:-}" ]]; then
  export AZURE_CONFIG_DIR="/tmp/azcfg-condor"
fi
if [[ ! -f "${AZURE_CONFIG_DIR}/azureProfile.json" && -d "${HOME}/.azure" ]]; then
  mkdir -p "${AZURE_CONFIG_DIR}"
  cp -R "${HOME}/.azure/"* "${AZURE_CONFIG_DIR}/" 2>/dev/null || true
fi

echo "Resolving Microsoft Graph token for tenant: ${EXTERNAL_TENANT_ID}"
GRAPH_TOKEN="$(
  az account get-access-token \
    --tenant "$EXTERNAL_TENANT_ID" \
    --resource-type ms-graph \
    --query accessToken -o tsv
)"

if [[ -z "${GRAPH_TOKEN}" ]]; then
  echo "Failed to acquire Graph token for tenant ${EXTERNAL_TENANT_ID}." >&2
  exit 1
fi

tmp_body="$(mktemp /tmp/external-id-flow-body.XXXXXX.json)"
trap 'rm -f "$tmp_body"' EXIT

graph_call() {
  local method="$1"
  local url="$2"
  local body="${3:-}"

  if [[ -n "$body" ]]; then
    curl -sS \
      -o "$tmp_body" \
      -w "%{http_code}" \
      -X "$method" \
      -H "Authorization: Bearer $GRAPH_TOKEN" \
      -H "Content-Type: application/json" \
      "$url" \
      -d "$body"
  else
    curl -sS \
      -o "$tmp_body" \
      -w "%{http_code}" \
      -X "$method" \
      -H "Authorization: Bearer $GRAPH_TOKEN" \
      "$url"
  fi
}

echo "Checking tenant organization metadata..."
status="$(graph_call GET "${GRAPH_BASE}/v1.0/organization")"
if [[ "$status" != "200" ]]; then
  echo "Graph call failed (${status}) while reading organization metadata." >&2
  cat "$tmp_body" >&2
  exit 1
fi

tenant_name="$(jq -r '.value[0].displayName // "unknown"' "$tmp_body")"
tenant_type="$(jq -r '.value[0].tenantType // "unknown"' "$tmp_body")"
echo "Tenant: ${tenant_name} (${tenant_type})"

echo "Looking for existing user flow: ${USER_FLOW_ID}"
status="$(graph_call GET "${GRAPH_BASE}/beta/identity/b2cUserFlows/${USER_FLOW_ID}")"
if [[ "$status" == "200" ]]; then
  echo "User flow already exists: ${USER_FLOW_ID}"
  jq '{id, userFlowType, userFlowTypeVersion}' "$tmp_body"
  exit 0
fi

if [[ "$status" == "403" ]]; then
  echo "Missing Graph permission to manage user flows." >&2
  cat "$tmp_body" >&2
  echo >&2
  echo "Grant delegated permission IdentityUserFlow.ReadWrite.All, then rerun." >&2
  exit 1
fi

if [[ "$status" != "404" ]]; then
  echo "Unexpected response (${status}) while checking user flow." >&2
  cat "$tmp_body" >&2
  exit 1
fi

echo "Creating user flow: ${USER_FLOW_ID}"
create_payload="$(jq -nc \
  --arg id "$USER_FLOW_ID" \
  '{id: $id, userFlowType: "signUpOrSignIn", userFlowTypeVersion: 1}')"

status="$(graph_call POST "${GRAPH_BASE}/beta/identity/b2cUserFlows" "$create_payload")"
if [[ "$status" != "201" && "$status" != "200" ]]; then
  echo "Failed to create user flow (${status})." >&2
  cat "$tmp_body" >&2
  exit 1
fi

echo "Created user flow successfully."
jq '{id, userFlowType, userFlowTypeVersion}' "$tmp_body"
echo
echo "Next steps:"
echo "1) Add identity providers (Google/Microsoft) to this user flow in Entra External ID."
echo "2) Register API and SPA apps in the same external tenant."
echo "3) Set FRONTEND_AUTHORITY / FRONTEND_CLIENT_ID / FRONTEND_API_SCOPE for this app."
