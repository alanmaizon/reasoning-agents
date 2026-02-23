#!/usr/bin/env bash
set -euo pipefail

# Create an Entra External ID tenant (B2C directory resource) via ARM.
#
# Required:
#   RESOURCE_GROUP
#   TENANT_PREFIX          # e.g. condorx0223
#
# Optional:
#   DISPLAY_NAME           # default: "Condor External ID"
#   COUNTRY_CODE           # default: "IE"
#   DIRECTORY_LOCATION     # default: "Europe" (geo label for b2cDirectories)
#   RESOURCE_GROUP_LOCATION # default: "swedencentral" (regional Azure location)
#   API_VERSION            # default: "2019-01-01-preview"
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
API_VERSION="${API_VERSION:-2019-01-01-preview}"
RESOURCE_TYPE="Microsoft.AzureActiveDirectory/b2cDirectories"
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

if [[ "$(az group exists -n "$RESOURCE_GROUP" -o tsv)" == "true" ]]; then
  rg_location="$(az group show -n "$RESOURCE_GROUP" --query location -o tsv)"
  echo "Using existing resource group: ${RESOURCE_GROUP} (${rg_location})"
else
  echo "Creating resource group: ${RESOURCE_GROUP} (${RESOURCE_GROUP_LOCATION})"
  az group create -n "$RESOURCE_GROUP" -l "$RESOURCE_GROUP_LOCATION" >/dev/null
fi

echo "Checking tenant domain availability: ${TENANT_DOMAIN}"
check_body="$(jq -nc --arg n "$TENANT_DOMAIN" \
  '{name:$n,type:"Microsoft.AzureActiveDirectory/b2cDirectories"}')"
check_result="$(
  az rest \
    --method post \
    --url "https://management.azure.com/providers/Microsoft.AzureActiveDirectory/checkNameAvailability?api-version=2019-01-01-preview" \
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
