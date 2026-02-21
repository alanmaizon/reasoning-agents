#!/usr/bin/env bash
set -euo pipefail

# Required environment variables:
#   RESOURCE_GROUP
#   LOCATION
#   APP_SERVICE_PLAN
#   WEBAPP_NAME
#   AZURE_AI_PROJECT_ENDPOINT
#   AZURE_AI_MODEL_DEPLOYMENT_NAME
#
# Optional:
#   APP_SERVICE_SKU (default: B1)
#   PYTHON_RUNTIME (default: PYTHON:3.11)
#   MCP_PROJECT_CONNECTION_NAME
#   AZURE_STORAGE_CONNECTION_STRING
#   AZURE_STORAGE_CONTAINER (default: mdt-data)
#   STATE_BLOB_PREFIX (default: state)
#   CACHE_BLOB_NAME (default: cache/cache.json)

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${LOCATION:?LOCATION is required}"
: "${APP_SERVICE_PLAN:?APP_SERVICE_PLAN is required}"
: "${WEBAPP_NAME:?WEBAPP_NAME is required}"
: "${AZURE_AI_PROJECT_ENDPOINT:?AZURE_AI_PROJECT_ENDPOINT is required}"
: "${AZURE_AI_MODEL_DEPLOYMENT_NAME:?AZURE_AI_MODEL_DEPLOYMENT_NAME is required}"

APP_SERVICE_SKU="${APP_SERVICE_SKU:-B1}"
PYTHON_RUNTIME="${PYTHON_RUNTIME:-PYTHON:3.11}"
AZURE_STORAGE_CONTAINER="${AZURE_STORAGE_CONTAINER:-mdt-data}"
STATE_BLOB_PREFIX="${STATE_BLOB_PREFIX:-state}"
CACHE_BLOB_NAME="${CACHE_BLOB_NAME:-cache/cache.json}"

echo "Creating/updating resource group..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null

echo "Creating/updating App Service plan..."
az appservice plan create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_SERVICE_PLAN" \
  --is-linux \
  --sku "$APP_SERVICE_SKU" >/dev/null

echo "Creating/updating web app..."
if az webapp show --resource-group "$RESOURCE_GROUP" --name "$WEBAPP_NAME" >/dev/null 2>&1; then
  echo "Web app exists, skipping create."
else
  az webapp create \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$APP_SERVICE_PLAN" \
    --name "$WEBAPP_NAME" \
    --runtime "$PYTHON_RUNTIME" >/dev/null
fi

echo "Applying app settings..."
SETTINGS=(
  "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
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

echo "Setting startup command..."
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEBAPP_NAME" \
  --startup-file "uvicorn src.api:app --host 0.0.0.0 --port \$PORT" >/dev/null

echo "Deploying source package..."
TMP_ZIP="/tmp/${WEBAPP_NAME}-$(date +%s).zip"
rm -f "$TMP_ZIP"
zip -qr "$TMP_ZIP" . \
  -x ".git/*" \
  -x ".venv/*" \
  -x "venv/*" \
  -x "env/*" \
  -x "__pycache__/*" \
  -x ".pytest_cache/*" \
  -x ".data/*" \
  -x "*.pyc" \
  -x ".DS_Store"

az webapp deploy \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEBAPP_NAME" \
  --type zip \
  --src-path "$TMP_ZIP" >/dev/null

echo "Deployment complete."
echo "Health URL: https://${WEBAPP_NAME}.azurewebsites.net/healthz"
