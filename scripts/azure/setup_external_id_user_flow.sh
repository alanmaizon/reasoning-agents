#!/usr/bin/env bash
set -euo pipefail

# Create (or reuse) a Sign-up and Sign-in user flow in an Entra External ID tenant.
#
# Required:
#   EXTERNAL_TENANT_ID   Tenant ID of the External ID tenant
#
# Optional:
#   USER_FLOW_ID         Default: B2C_1_condor_signup_signin
#   GRAPH_TOKEN          Pre-acquired Graph bearer token
#   GRAPH_CLIENT_ID      App registration client ID (for client credentials auth)
#   GRAPH_CLIENT_SECRET  App registration client secret (for client credentials auth)
#   CIAM_IDENTITY_PROVIDER_ID  Preferred CIAM provider id (default: EmailPassword-OAUTH)
#
# Notes:
# - For CIAM tenants, this script uses authenticationEventsFlows APIs
#   and requires EventListener.ReadWrite.All to create flows.
# - For B2C tenants, this script uses b2cUserFlows APIs
#   and requires IdentityUserFlow.ReadWrite.All to create flows.
# - For reliable automation, prefer app-only auth
#   (GRAPH_CLIENT_ID + GRAPH_CLIENT_SECRET).
# - If permissions are missing, this script prints a clear error and exits.

: "${EXTERNAL_TENANT_ID:?EXTERNAL_TENANT_ID is required}"

USER_FLOW_ID="${USER_FLOW_ID:-B2C_1_condor_signup_signin}"
GRAPH_BASE="https://graph.microsoft.com"
TOKEN_SOURCE=""
INPUT_GRAPH_TOKEN="${GRAPH_TOKEN:-}"
GRAPH_TOKEN=""
CIAM_IDENTITY_PROVIDER_ID="${CIAM_IDENTITY_PROVIDER_ID:-EmailPassword-OAUTH}"

# Some environments (CI/sandbox) cannot write under ~/.azure/commands.
# Use a writable AZURE_CONFIG_DIR by default and copy cached profile if present.
if [[ -z "${AZURE_CONFIG_DIR:-}" ]]; then
  export AZURE_CONFIG_DIR="/tmp/azcfg-condor"
fi
if [[ ! -f "${AZURE_CONFIG_DIR}/azureProfile.json" && -d "${HOME}/.azure" ]]; then
  mkdir -p "${AZURE_CONFIG_DIR}"
  cp -R "${HOME}/.azure/"* "${AZURE_CONFIG_DIR}/" 2>/dev/null || true
fi

resolve_graph_token() {
  if [[ -n "${INPUT_GRAPH_TOKEN}" ]]; then
    TOKEN_SOURCE="env-token"
    GRAPH_TOKEN="${INPUT_GRAPH_TOKEN}"
    return 0
  fi

  if [[ -n "${GRAPH_CLIENT_ID:-}" && -n "${GRAPH_CLIENT_SECRET:-}" ]]; then
    TOKEN_SOURCE="client-credentials"
    local token_response
    token_response="$(
      curl -sS \
        -X POST \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "client_id=${GRAPH_CLIENT_ID}" \
        --data-urlencode "client_secret=${GRAPH_CLIENT_SECRET}" \
        --data-urlencode "scope=https://graph.microsoft.com/.default" \
        --data-urlencode "grant_type=client_credentials" \
        "https://login.microsoftonline.com/${EXTERNAL_TENANT_ID}/oauth2/v2.0/token"
    )"

    local access_token
    access_token="$(echo "$token_response" | jq -r '.access_token // empty')"
    if [[ -z "$access_token" ]]; then
      echo "Failed to acquire Graph token via client credentials." >&2
      echo "$token_response" | jq . >&2 || echo "$token_response" >&2
      return 1
    fi
    GRAPH_TOKEN="$access_token"
    return 0
  fi

  TOKEN_SOURCE="azure-cli"
  GRAPH_TOKEN="$(
    az account get-access-token \
    --tenant "$EXTERNAL_TENANT_ID" \
    --resource-type ms-graph \
    --query accessToken -o tsv
  )"
}

echo "Resolving Microsoft Graph token for tenant: ${EXTERNAL_TENANT_ID}"
resolve_graph_token || true
if [[ -z "${GRAPH_TOKEN}" ]]; then
  echo "Failed to acquire Graph token for tenant ${EXTERNAL_TENANT_ID}." >&2
  if [[ "$TOKEN_SOURCE" == "azure-cli" ]]; then
    echo "Azure CLI token may not include IdentityUserFlow.* in External ID tenants." >&2
    echo "Set GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET for app-only auth, then rerun." >&2
  fi
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

setup_ciam_user_flow() {
  local flow_name="$1"
  local status
  local filter
  local existing_id
  local provider
  local -a providers
  local payload

  echo "Detected CIAM tenant. Using authenticationEventsFlows API."

  filter="$(jq -nr --arg v "displayName eq '${flow_name}'" '$v|@uri')"
  status="$(graph_call GET "${GRAPH_BASE}/v1.0/identity/authenticationEventsFlows?\$filter=${filter}")"
  if [[ "$status" == "200" ]]; then
    existing_id="$(
      jq -r '
        .value[]
        | select(."@odata.type" == "#microsoft.graph.externalUsersSelfServiceSignUpEventsFlow")
        | .id
      ' "$tmp_body" | head -n1
    )"
    if [[ -n "$existing_id" ]]; then
      echo "User flow already exists: ${flow_name}"
      jq -n --arg id "$existing_id" --arg displayName "$flow_name" \
        '{id:$id, displayName:$displayName, userFlowType:"externalUsersSelfServiceSignUpEventsFlow"}'
      return 0
    fi
  elif [[ "$status" == "403" ]]; then
    echo "Missing Graph permission to manage CIAM user flows." >&2
    cat "$tmp_body" >&2
    echo >&2
    echo "Grant application permission EventListener.ReadWrite.All (Microsoft Graph), then admin-consent it." >&2
    return 1
  else
    echo "Unexpected response (${status}) while listing CIAM user flows." >&2
    cat "$tmp_body" >&2
    return 1
  fi

  providers=("$CIAM_IDENTITY_PROVIDER_ID")
  if [[ "$CIAM_IDENTITY_PROVIDER_ID" != "EmailPassword-OAUTH" ]]; then
    providers+=("EmailPassword-OAUTH")
  fi
  if [[ "$CIAM_IDENTITY_PROVIDER_ID" != "EmailOtpSignup-OAUTH" ]]; then
    providers+=("EmailOtpSignup-OAUTH")
  fi
  echo "Creating CIAM user flow: ${flow_name}"
  for provider in "${providers[@]}"; do
    payload="$(jq -nc \
      --arg display "$flow_name" \
      --arg provider "$provider" \
      '{
         "@odata.type":"#microsoft.graph.externalUsersSelfServiceSignUpEventsFlow",
         displayName:$display,
         onAuthenticationMethodLoadStart:{
           "@odata.type":"#microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp",
           identityProviders:[{id:$provider}]
         },
         onInteractiveAuthFlowStart:{
           "@odata.type":"#microsoft.graph.onInteractiveAuthFlowStartExternalUsersSelfServiceSignUp",
           isSignUpAllowed:true
         }
       }')"

    status="$(graph_call POST "${GRAPH_BASE}/v1.0/identity/authenticationEventsFlows" "$payload")"
    if [[ "$status" == "201" || "$status" == "200" ]]; then
      echo "Created CIAM user flow successfully."
      jq '{id, displayName, "@odata.type":."@odata.type"}' "$tmp_body"
      return 0
    fi

    if [[ "$status" == "403" ]]; then
      echo "Missing Graph permission to create CIAM user flows." >&2
      cat "$tmp_body" >&2
      echo >&2
      echo "Grant application permission EventListener.ReadWrite.All (Microsoft Graph), then admin-consent it." >&2
      return 1
    fi

    echo "Create attempt with provider '${provider}' failed (${status}), trying next provider if available..." >&2
  done

  echo "Failed to create CIAM user flow after trying built-in providers." >&2
  cat "$tmp_body" >&2
  return 1
}

echo "Checking tenant organization metadata..."
status="$(graph_call GET "${GRAPH_BASE}/v1.0/organization")"
if [[ "$status" == "200" ]]; then
  tenant_name="$(jq -r '.value[0].displayName // "unknown"' "$tmp_body")"
  tenant_type="$(jq -r '.value[0].tenantType // "unknown"' "$tmp_body")"
  echo "Tenant: ${tenant_name} (${tenant_type})"
else
  echo "Could not read organization metadata (HTTP ${status}); continuing." >&2
fi

echo "Looking for existing user flow: ${USER_FLOW_ID}"
status="$(graph_call GET "${GRAPH_BASE}/beta/identity/b2cUserFlows/${USER_FLOW_ID}")"
if [[ "$status" == "200" ]]; then
  echo "User flow already exists: ${USER_FLOW_ID}"
  jq '{id, userFlowType, userFlowTypeVersion}' "$tmp_body"
  exit 0
fi

if [[ "$status" == "403" ]]; then
  if jq -e '.error.message // "" | test("Azure AD CIAM directory"; "i")' "$tmp_body" >/dev/null; then
    setup_ciam_user_flow "$USER_FLOW_ID"
    exit $?
  fi
  echo "Missing Graph permission to manage user flows." >&2
  cat "$tmp_body" >&2
  echo >&2
  if [[ "$TOKEN_SOURCE" == "azure-cli" ]]; then
    echo "Azure CLI token does not have required IdentityUserFlow permissions in this tenant." >&2
    echo "Recommended: use app-only auth (GRAPH_CLIENT_ID + GRAPH_CLIENT_SECRET) with Graph application permission IdentityUserFlow.ReadWrite.All and admin consent." >&2
  else
    echo "Grant permission IdentityUserFlow.ReadWrite.All (delegated or application as applicable), then rerun." >&2
  fi
  exit 1
fi

if [[ "$status" != "404" ]]; then
  if jq -e '.error.message // "" | test("Azure AD CIAM directory"; "i")' "$tmp_body" >/dev/null; then
    setup_ciam_user_flow "$USER_FLOW_ID"
    exit $?
  fi
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
