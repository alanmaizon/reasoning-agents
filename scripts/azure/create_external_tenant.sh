#!/usr/bin/env bash
set -euo pipefail

# Create an Entra External ID tenant via ARM.
#
# Required:
#   RESOURCE_GROUP
#   TENANT_PREFIX          # e.g. condorx0223
#
# Optional:
#   DISPLAY_NAME           # default: "Condor External ID"
#   COUNTRY_CODE           # default: "IE"
#   DIRECTORY_LOCATION     # default: "Europe" (geo label for External ID tenant)
#   RESOURCE_GROUP_LOCATION # default: "swedencentral" (regional Azure location)
#   RESOURCE_TYPE          # optional override, e.g. "Microsoft.AzureActiveDirectory/ciamDirectories"
#   API_VERSION            # optional override; auto-detected by default
#
# Output:
#   Prints tenant domain + created tenantId (when returned by ARM).
#
# Notes:
# - Requires owner/contributor rights in the subscription.
# - If provider is unregistered, this script registers
#   Microsoft.AzureActiveDirectory and waits for readiness.

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${TENANT_PREFIX:?TENANT_PREFIX is required}"

DISPLAY_NAME="${DISPLAY_NAME:-Condor External ID}"
COUNTRY_CODE="${COUNTRY_CODE:-IE}"
# Backward compatibility: if LOCATION is passed, treat it as directory location.
DIRECTORY_LOCATION="${DIRECTORY_LOCATION:-${LOCATION:-Europe}}"
RESOURCE_GROUP_LOCATION="${RESOURCE_GROUP_LOCATION:-swedencentral}"
API_VERSION="${API_VERSION:-}"
RESOURCE_TYPE="${RESOURCE_TYPE:-}"
TENANT_DOMAIN="${TENANT_PREFIX}.onmicrosoft.com"

# Some environments cannot write under ~/.azure/commands.
if [[ -z "${AZURE_CONFIG_DIR:-}" ]]; then
  export AZURE_CONFIG_DIR="/tmp/azcfg-condor"
fi
if [[ ! -f "${AZURE_CONFIG_DIR}/azureProfile.json" && -d "${HOME}/.azure" ]]; then
  mkdir -p "${AZURE_CONFIG_DIR}"
  cp -R "${HOME}/.azure/"* "${AZURE_CONFIG_DIR}/" 2>/dev/null || true
fi

echo "Using subscription:"
az account show --query "{name:name,id:id,tenantId:tenantId}" -o table

echo "Ensuring provider Microsoft.AzureActiveDirectory is registered..."
az provider register -n Microsoft.AzureActiveDirectory >/dev/null

for _ in $(seq 1 60); do
  state="$(az provider show -n Microsoft.AzureActiveDirectory --query registrationState -o tsv || true)"
  if [[ "$state" == "Registered" ]]; then
    break
  fi
  sleep 2
done

state="$(az provider show -n Microsoft.AzureActiveDirectory --query registrationState -o tsv)"
if [[ "$state" != "Registered" ]]; then
  echo "Provider is not registered yet (state=$state)." >&2
  exit 1
fi

provider_json="$(az provider show -n Microsoft.AzureActiveDirectory -o json)"

choose_resource_type() {
  local candidate
  for candidate in ciamDirectories b2cDirectories b2ctenants; do
    if echo "$provider_json" | jq -e --arg rt "$candidate" '.resourceTypes[] | select(.resourceType == $rt)' >/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

choose_api_version() {
  local resource_type_short="$1"
  local candidate
  local -a preferred_versions=(
    "2025-08-01-preview"
    "2023-05-17-preview"
    "2023-01-18-preview"
    "2022-03-01-preview"
    "2021-04-01-preview"
    "2021-04-01"
    "2020-05-01-preview"
    "2019-01-01-preview"
  )

  for candidate in "${preferred_versions[@]}"; do
    if echo "$provider_json" | jq -e --arg rt "$resource_type_short" --arg v "$candidate" '.resourceTypes[] | select(.resourceType == $rt and (.apiVersions | index($v)))' >/dev/null; then
      echo "$candidate"
      return 0
    fi
  done

  echo "$provider_json" | jq -r --arg rt "$resource_type_short" '.resourceTypes[] | select(.resourceType == $rt) | .apiVersions[0] // empty'
}

if [[ -z "$RESOURCE_TYPE" ]]; then
  resource_type_short="$(choose_resource_type || true)"
  if [[ -z "$resource_type_short" ]]; then
    echo "Could not find supported External ID resource type in Microsoft.AzureActiveDirectory provider." >&2
    exit 1
  fi
  RESOURCE_TYPE="Microsoft.AzureActiveDirectory/${resource_type_short}"
else
  resource_type_short="${RESOURCE_TYPE##*/}"
fi

if [[ -z "$API_VERSION" ]]; then
  API_VERSION="$(choose_api_version "$resource_type_short")"
fi

if [[ -z "$API_VERSION" ]]; then
  echo "Could not determine a supported API version for resource type ${RESOURCE_TYPE}." >&2
  exit 1
fi

echo "Using External ID resource type: ${RESOURCE_TYPE}"
echo "Using API version: ${API_VERSION}"

if [[ "$(az group exists -n "$RESOURCE_GROUP" -o tsv)" == "true" ]]; then
  rg_location="$(az group show -n "$RESOURCE_GROUP" --query location -o tsv)"
  echo "Using existing resource group: ${RESOURCE_GROUP} (${rg_location})"
else
  echo "Creating resource group: ${RESOURCE_GROUP} (${RESOURCE_GROUP_LOCATION})"
  az group create -n "$RESOURCE_GROUP" -l "$RESOURCE_GROUP_LOCATION" >/dev/null
fi

echo "Checking tenant domain availability: ${TENANT_DOMAIN}"
check_body="$(jq -nc --arg n "$TENANT_DOMAIN" \
  --arg t "$RESOURCE_TYPE" \
  '{name:$n,type:$t}')"

check_api_version="$API_VERSION"
if ! echo "$provider_json" | jq -e --arg v "$check_api_version" '.resourceTypes[] | select(.resourceType == "checkNameAvailability" and (.apiVersions | index($v)))' >/dev/null; then
  check_api_version="$(
    echo "$provider_json" | jq -r '.resourceTypes[] | select(.resourceType == "checkNameAvailability") | .apiVersions[0] // empty'
  )"
fi

if [[ -z "$check_api_version" ]]; then
  echo "Could not determine API version for checkNameAvailability." >&2
  exit 1
fi

check_result="$(
  az rest \
    --method post \
    --url "https://management.azure.com/providers/Microsoft.AzureActiveDirectory/checkNameAvailability?api-version=${check_api_version}" \
    --body "$check_body" \
    -o json
)"

available="$(echo "$check_result" | jq -r '.nameAvailable // false')"
if [[ "$available" != "true" ]]; then
  echo "Tenant domain is not available: ${TENANT_DOMAIN}" >&2
  echo "$check_result" | jq . >&2
  exit 1
fi

echo "Creating external tenant resource: ${TENANT_DOMAIN}"
create_props="$(jq -nc \
  --arg display "$DISPLAY_NAME" \
  --arg cc "$COUNTRY_CODE" \
  '{createTenantProperties:{displayName:$display,countryCode:$cc},sku:{name:"Standard",tier:"A0"}}')"

create_result="$(
  az resource create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$TENANT_DOMAIN" \
    --resource-type "$RESOURCE_TYPE" \
    --api-version "$API_VERSION" \
    --location "$DIRECTORY_LOCATION" \
    --properties "$create_props" \
    -o json
)"

tenant_id="$(echo "$create_result" | jq -r '.properties.tenantId // empty')"
echo
echo "Created external tenant resource."
echo "Tenant domain: ${TENANT_DOMAIN}"
if [[ -n "$tenant_id" ]]; then
  echo "External tenant ID: ${tenant_id}"
else
  echo "External tenant ID not returned immediately."
  echo "Check with:"
  echo "az resource show -g \"$RESOURCE_GROUP\" -n \"$TENANT_DOMAIN\" --resource-type \"$RESOURCE_TYPE\" --api-version \"$API_VERSION\" --query properties.tenantId -o tsv"
fi
