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
#   CIAM_IDENTITY_PROVIDER_IDS Comma-separated provider ids to enforce
#                              (e.g. "Google-OAUTH")
#   CIAM_SYNC_IDENTITY_PROVIDERS true/false (default: false)
#                              Sync providers on existing CIAM flow to match desired list
#   CIAM_GOOGLE_ONLY      true/false (default: false)
#                         Shortcut for CIAM_IDENTITY_PROVIDER_IDS=Google-OAUTH
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
CIAM_IDENTITY_PROVIDER_IDS="${CIAM_IDENTITY_PROVIDER_IDS:-}"
CIAM_SYNC_IDENTITY_PROVIDERS="${CIAM_SYNC_IDENTITY_PROVIDERS:-false}"
CIAM_GOOGLE_ONLY="${CIAM_GOOGLE_ONLY:-false}"

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

is_truthy() {
  local value="${1:-}"
  value="$(echo "$value" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

build_desired_ciam_providers() {
  local input_ids="$1"
  local -n out_ref="$2"
  out_ref=()

  if [[ -n "$input_ids" ]]; then
    while IFS= read -r item; do
      item="$(echo "$item" | xargs)"
      [[ -n "$item" ]] && out_ref+=("$item")
    done < <(echo "$input_ids" | tr ',' '\n')
    return 0
  fi

  if is_truthy "$CIAM_GOOGLE_ONLY"; then
    out_ref+=("Google-OAUTH")
    return 0
  fi

  out_ref+=("$CIAM_IDENTITY_PROVIDER_ID")
}

ciam_list_identity_providers() {
  local flow_id="$1"
  graph_call GET "${GRAPH_BASE}/v1.0/identity/authenticationEventsFlows/${flow_id}/microsoft.graph.externalUsersSelfServiceSignUpEventsFlow/onAuthenticationMethodLoadStart/microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp/identityProviders"
}

ciam_add_identity_provider() {
  local flow_id="$1"
  local provider_id="$2"
  local body
  body="$(jq -nc --arg odata_id "${GRAPH_BASE}/v1.0/identityProviders/${provider_id}" '{"@odata.id": $odata_id}')"
  graph_call POST "${GRAPH_BASE}/v1.0/identity/authenticationEventsFlows/${flow_id}/microsoft.graph.externalUsersSelfServiceSignUpEventsFlow/onAuthenticationMethodLoadStart/microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp/identityProviders/\$ref" "$body"
}

ciam_delete_identity_provider() {
  local flow_id="$1"
  local provider_id="$2"
  graph_call DELETE "${GRAPH_BASE}/v1.0/identity/authenticationEventsFlows/${flow_id}/microsoft.graph.externalUsersSelfServiceSignUpEventsFlow/onAuthenticationMethodLoadStart/microsoft.graph.onAuthenticationMethodLoadStartExternalUsersSelfServiceSignUp/identityProviders/${provider_id}/\$ref"
}

sync_ciam_identity_providers() {
  local flow_id="$1"
  local -a desired_providers=()
  local -a existing_providers=()
  local -a to_add=()
  local -a to_remove=()
  local status
  local provider

  build_desired_ciam_providers "$CIAM_IDENTITY_PROVIDER_IDS" desired_providers
  if [[ "${#desired_providers[@]}" -eq 0 ]]; then
    echo "No desired CIAM identity providers were supplied." >&2
    return 1
  fi

  status="$(ciam_list_identity_providers "$flow_id")"
  if [[ "$status" != "200" ]]; then
    echo "Failed to list CIAM identity providers (${status})." >&2
    cat "$tmp_body" >&2
    return 1
  fi

  mapfile -t existing_providers < <(jq -r '.value[]?.id' "$tmp_body")

  for provider in "${desired_providers[@]}"; do
    if ! printf '%s\n' "${existing_providers[@]}" | grep -Fxq "$provider"; then
      to_add+=("$provider")
    fi
  done

  for provider in "${existing_providers[@]}"; do
    if ! printf '%s\n' "${desired_providers[@]}" | grep -Fxq "$provider"; then
      to_remove+=("$provider")
    fi
  done

  for provider in "${to_remove[@]}"; do
    echo "Removing CIAM identity provider: ${provider}"
    status="$(ciam_delete_identity_provider "$flow_id" "$provider")"
    if [[ "$status" != "204" && "$status" != "200" ]]; then
      echo "Failed removing provider ${provider} (${status})." >&2
      cat "$tmp_body" >&2
      return 1
    fi
  done

  for provider in "${to_add[@]}"; do
    echo "Adding CIAM identity provider: ${provider}"
    status="$(ciam_add_identity_provider "$flow_id" "$provider")"
    if [[ "$status" != "204" && "$status" != "200" ]]; then
      echo "Failed adding provider ${provider} (${status})." >&2
      cat "$tmp_body" >&2
      return 1
    fi
  done

  status="$(ciam_list_identity_providers "$flow_id")"
  if [[ "$status" == "200" ]]; then
    echo "Current CIAM identity providers:"
    jq '{identityProviders:[.value[] | .id]}' "$tmp_body"
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
      if is_truthy "$CIAM_SYNC_IDENTITY_PROVIDERS" || is_truthy "$CIAM_GOOGLE_ONLY" || [[ -n "$CIAM_IDENTITY_PROVIDER_IDS" ]]; then
        echo "Syncing CIAM identity providers on existing flow..."
        sync_ciam_identity_providers "$existing_id"
      fi
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

  build_desired_ciam_providers "$CIAM_IDENTITY_PROVIDER_IDS" providers
  if [[ "${#providers[@]}" -eq 0 ]]; then
    echo "No CIAM identity providers requested." >&2
    return 1
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

  echo "Failed to create CIAM user flow after trying requested providers." >&2
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
