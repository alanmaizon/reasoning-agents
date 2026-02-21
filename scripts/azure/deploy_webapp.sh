#!/usr/bin/env bash
set -euo pipefail

# Required environment variables:
#   RESOURCE_GROUP
#   LOCATION
#   ACR_NAME
#   APP_SERVICE_PLAN
#   WEBAPP_NAME
#   AZURE_AI_PROJECT_ENDPOINT
#   AZURE_AI_MODEL_DEPLOYMENT_NAME
#
# Optional:
#   IMAGE_NAME (default: mdt-api)
#   IMAGE_TAG (default: latest)
#   MCP_PROJECT_CONNECTION_NAME
#   AZURE_STORAGE_CONNECTION_STRING
#   AZURE_STORAGE_CONTAINER (default: mdt-data)
#   STATE_BLOB_PREFIX (default: state)
#   CACHE_BLOB_NAME (default: cache/cache.json)

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${LOCATION:?LOCATION is required}"
: "${ACR_NAME:?ACR_NAME is required}"
: "${APP_SERVICE_PLAN:?APP_SERVICE_PLAN is required}"
: "${WEBAPP_NAME:?WEBAPP_NAME is required}"
: "${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT is required}"
: "${AZURE_AI_MODEL_DEPLOYMENT_NAME:?AZURE_AI_MODEL_DEPLOYMENT_NAME is required}"

IMAGE_NAME="${IMAGE_NAME:-mdt-api}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
AZURE_STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-mdt-data}"
STATE_BLOB_PREFIX="${STATE_BLOB_PREFIX:-state}"
CACHE_BLOB_NAME="${CACHE_BLOB_NAME:-cache/cache.json}"

echo "Creating/updating resource group..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null

echo "Creating/updating Azure Container Registry..."
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled false >/dev/null

echo "Building image in ACR..."
az acr build --registry "$ACR_NAME" --image "${IMAGE_NAME}:${IMAGE_TAG}" .

ACR_LOGIN_SERVER="$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)"
IMAGE_URI="${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "Creating/updating App Service plan..."
az appservice plan create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_SERVICE_PLAN" \
  --is-linux \
  --sku B1 >/dev/null

echo "Creating/updating web app..."
if az webapp show --resource-group "$RESOURCE_GROUP" --name "$WEBAPP_NAME" >/dev/null 2>&1; then
  az webapp config container set \
    --resource-group "$RESOURCE_GROUP" \
    --name "$WEBAPP_NAME" \
    --container-image-name "$IMAGE_URI" >/dev/null
else
  az webapp create \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$APP_SERVICE_PLAN" \
    --name "$WEBAPP_NAME" \
    --deployment-container-image-name "$IMAGE_URI" >/dev/null
fi

echo "Assigning managed identity..."
az webapp identity assign \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEBAPP_NAME" >/dev/null

echo "Granting AcrPull to web app identity..."
ACR_ID="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
PRINCIPAL_ID="$(az webapp identity show --resource-group "$RESOURCE_GROUP" --name "$WEBAPP_NAME" --query principalId -o tsv)"
az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$ACR_ID" \
  --role AcrPull >/dev/null || true

echo "Applying application settings..."
SETTINGS=(
  "WEBSITES_PORT=8000"
  "AZURE_AI_PROJECT_ENDPOINT=$AZURE_AI_PROJECT_ENDPOINT"
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=$AZURE_AI_MODEL_DEPLOYMENT_NAME"
  "AZURE_STORAGE_CONTAINER=$AZURE_STORAGE_CONTAINER"
  "STATE_BLOB_PREFIX=$STATE_BLOB_PREFIX"
  "CACHE_BLOB_NAME=$CACHE_BLOB_NAME"
)

if [[ -n "${MCP_PROJECT_CONNECTION_NAME:-}" ]]; then
  SETTINGS+=("MCP_PROJECT_CONNECTION_NAME=$MCP_PROJECT_CONNECTION_NAME")
fi

if [[ -n "${AZURE_STORAGE_CONNECTION_STRING:-}" ]]; then
  SETTINGS+=("AZURE_STORAGE_CONNECTION_STRING=$AZURE_STORAGE_CONNECTION_STRING")
fi

az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEBAPP_NAME" \
  --settings "${SETTINGS[@]}" >/dev/null

echo "Deployment complete."
echo "URL: https://${WEBAPP_NAME}.azurewebsites.net/healthz"
