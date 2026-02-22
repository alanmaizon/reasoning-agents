#!/usr/bin/env bash
set -euo pipefail

# Observability bootstrap for VM-hosted API deployments.
#
# Required:
#   RESOURCE_GROUP
#   LOCATION
#   VM_NAME
#   HEALTHCHECK_URL
#
# Optional:
#   LOG_ANALYTICS_WORKSPACE (default: law-mdt-obsv)
#   APP_INSIGHTS_NAME (default: appi-mdt-obsv)
#   DCR_NAME (default: dcr-mdt-obsv)
#   DCR_DEST_NAME (default: lawdest)
#   ACTION_GROUP_NAME (default: ag-mdt-obsv)
#   ACTION_GROUP_SHORT_NAME (default: mdtops)
#   ALERT_EMAIL (default: current az account user)
#   WEBTEST_NAME (default: wt-mdt-healthz)
#   WEBTEST_LOCATION_IDS (default: emea-nl-ams-azr,us-fl-mia-edge)
#   CPU_ALERT_NAME (default: alert-vm-cpu-high)
#   HEARTBEAT_ALERT_NAME (default: alert-vm-heartbeat-miss)
#   API_ERROR_ALERT_NAME (default: alert-api-error-rate)
#   API_DOWN_ALERT_NAME (default: alert-api-down)

: "${RESOURCE_GROUP:?RESOURCE_GROUP is required}"
: "${LOCATION:?LOCATION is required}"
: "${VM_NAME:?VM_NAME is required}"
: "${HEALTHCHECK_URL:?HEALTHCHECK_URL is required}"

LOG_ANALYTICS_WORKSPACE="${LOG_ANALYTICS_WORKSPACE:-law-mdt-obsv}"
APP_INSIGHTS_NAME="${APP_INSIGHTS_NAME:-appi-mdt-obsv}"
DCR_NAME="${DCR_NAME:-dcr-mdt-obsv}"
DCR_DEST_NAME="${DCR_DEST_NAME:-lawdest}"
ACTION_GROUP_NAME="${ACTION_GROUP_NAME:-ag-mdt-obsv}"
ACTION_GROUP_SHORT_NAME="${ACTION_GROUP_SHORT_NAME:-mdtops}"
ALERT_EMAIL="${ALERT_EMAIL:-$(az account show --query user.name -o tsv)}"
WEBTEST_NAME="${WEBTEST_NAME:-wt-mdt-healthz}"
WEBTEST_LOCATION_IDS="${WEBTEST_LOCATION_IDS:-emea-nl-ams-azr,us-fl-mia-edge}"
CPU_ALERT_NAME="${CPU_ALERT_NAME:-alert-vm-cpu-high}"
HEARTBEAT_ALERT_NAME="${HEARTBEAT_ALERT_NAME:-alert-vm-heartbeat-miss}"
API_ERROR_ALERT_NAME="${API_ERROR_ALERT_NAME:-alert-api-error-rate}"
API_DOWN_ALERT_NAME="${API_DOWN_ALERT_NAME:-alert-api-down}"

echo "Creating/updating Log Analytics workspace..."
az monitor log-analytics workspace create \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS_WORKSPACE" \
  --location "$LOCATION" \
  >/dev/null

LAW_ID="$(az monitor log-analytics workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS_WORKSPACE" \
  --query id -o tsv)"

echo "Creating/updating Application Insights (workspace-based)..."
if ! az monitor app-insights component show \
  --resource-group "$RESOURCE_GROUP" \
  --app "$APP_INSIGHTS_NAME" >/dev/null 2>&1; then
  az monitor app-insights component create \
    --resource-group "$RESOURCE_GROUP" \
    --app "$APP_INSIGHTS_NAME" \
    --location "$LOCATION" \
    --workspace "$LAW_ID" \
    --application-type web \
    >/dev/null
fi

APPI_ID="$(az monitor app-insights component show \
  --resource-group "$RESOURCE_GROUP" \
  --app "$APP_INSIGHTS_NAME" \
  --query id -o tsv)"

echo "Installing/updating Azure Monitor Agent extension on VM..."
az vm extension set \
  --resource-group "$RESOURCE_GROUP" \
  --vm-name "$VM_NAME" \
  --publisher Microsoft.Azure.Monitor \
  --name AzureMonitorLinuxAgent \
  --enable-auto-upgrade true \
  >/dev/null

echo "Creating/updating Data Collection Rule..."
if ! az monitor data-collection rule show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DCR_NAME" >/dev/null 2>&1; then
  dcr_file="$(mktemp)"
  cat >"$dcr_file" <<EOF
{
  "properties": {
    "dataSources": {
      "syslog": [
        {
          "name": "syslogBase",
          "streams": ["Microsoft-Syslog"],
          "facilityNames": ["auth", "authpriv", "cron", "daemon", "syslog", "user"],
          "logLevels": ["Warning", "Error", "Critical", "Alert", "Emergency"]
        }
      ],
      "performanceCounters": [
        {
          "name": "perfBase",
          "streams": ["Microsoft-Perf"],
          "samplingFrequencyInSeconds": 60,
          "counterSpecifiers": [
            "\\\\Processor(_Total)\\\\% Processor Time",
            "\\\\Memory\\\\Available MBytes",
            "\\\\LogicalDisk(_Total)\\\\% Free Space"
          ]
        }
      ]
    },
    "destinations": {
      "logAnalytics": [
        {
          "workspaceResourceId": "$LAW_ID",
          "name": "$DCR_DEST_NAME"
        }
      ]
    },
    "dataFlows": [
      {
        "streams": ["Microsoft-Syslog"],
        "destinations": ["$DCR_DEST_NAME"]
      },
      {
        "streams": ["Microsoft-Perf"],
        "destinations": ["$DCR_DEST_NAME"]
      }
    ]
  }
}
EOF
  az monitor data-collection rule create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DCR_NAME" \
    --location "$LOCATION" \
    --kind Linux \
    --rule-file "$dcr_file" \
    >/dev/null
  rm -f "$dcr_file"
fi

if [[ -z "$(az monitor data-collection rule show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DCR_NAME" \
  --query "destinations.logAnalytics[?name=='$DCR_DEST_NAME'].name | [0]" -o tsv)" ]]; then
  az monitor data-collection rule log-analytics add \
    --resource-group "$RESOURCE_GROUP" \
    --rule-name "$DCR_NAME" \
    --name "$DCR_DEST_NAME" \
    --resource-id "$LAW_ID" \
    >/dev/null
fi

if [[ -z "$(az monitor data-collection rule show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DCR_NAME" \
  --query "dataSources.syslog[?name=='syslogBase'].name | [0]" -o tsv)" ]]; then
  az monitor data-collection rule syslog add \
    --resource-group "$RESOURCE_GROUP" \
    --rule-name "$DCR_NAME" \
    --name syslogBase \
    --facility-names auth authpriv cron daemon syslog user \
    --log-levels Warning Error Critical Alert Emergency \
    --streams Microsoft-Syslog \
    >/dev/null
fi

if [[ -z "$(az monitor data-collection rule show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DCR_NAME" \
  --query "dataSources.performanceCounters[?name=='perfBase'].name | [0]" -o tsv)" ]]; then
  az monitor data-collection rule performance-counter add \
    --resource-group "$RESOURCE_GROUP" \
    --rule-name "$DCR_NAME" \
    --name perfBase \
    --streams Microsoft-Perf \
    --counter-specifiers "\\Processor(_Total)\\% Processor Time" "\\Memory\\Available MBytes" "\\LogicalDisk(_Total)\\% Free Space" \
    --sampling-frequency 60 \
    >/dev/null
fi

az monitor data-collection rule data-flow add \
  --resource-group "$RESOURCE_GROUP" \
  --rule-name "$DCR_NAME" \
  --streams Microsoft-Syslog \
  --destinations "$DCR_DEST_NAME" \
  >/dev/null 2>&1 || true

az monitor data-collection rule data-flow add \
  --resource-group "$RESOURCE_GROUP" \
  --rule-name "$DCR_NAME" \
  --streams Microsoft-Perf \
  --destinations "$DCR_DEST_NAME" \
  >/dev/null 2>&1 || true

DCR_ID="$(az monitor data-collection rule show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DCR_NAME" \
  --query id -o tsv)"
VM_ID="$(az vm show --resource-group "$RESOURCE_GROUP" --name "$VM_NAME" --query id -o tsv)"

if [[ -z "$(az monitor data-collection rule association list-by-resource \
  --resource "$VM_ID" \
  --query "[?dataCollectionRuleId=='$DCR_ID'].name | [0]" -o tsv)" ]]; then
  az monitor data-collection rule association create \
    --name "assoc-${VM_NAME}" \
    --resource "$VM_ID" \
    --rule-id "$DCR_ID" \
    >/dev/null
fi

echo "Creating/updating Action Group..."
if ! az monitor action-group show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACTION_GROUP_NAME" >/dev/null 2>&1; then
  az monitor action-group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACTION_GROUP_NAME" \
    --short-name "$ACTION_GROUP_SHORT_NAME" \
    --action email primary "$ALERT_EMAIL" \
    >/dev/null
fi
ACTION_GROUP_ID="$(az monitor action-group show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACTION_GROUP_NAME" \
  --query id -o tsv)"

echo "Creating/updating API availability web test..."
if ! az monitor app-insights web-test show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WEBTEST_NAME" >/dev/null 2>&1; then
  created=0
  IFS=',' read -r -a location_ids <<<"$WEBTEST_LOCATION_IDS"
  for loc_id in "${location_ids[@]}"; do
    loc_id="$(echo "$loc_id" | xargs)"
    [[ -z "$loc_id" ]] && continue
    if az monitor app-insights web-test create \
      --resource-group "$RESOURCE_GROUP" \
      --name "$WEBTEST_NAME" \
      --location "$LOCATION" \
      --web-test-kind standard \
      --defined-web-test-name "$WEBTEST_NAME" \
      --synthetic-monitor-id "$WEBTEST_NAME" \
      --request-url "$HEALTHCHECK_URL" \
      --http-verb GET \
      --expected-status-code 200 \
      --retry-enabled true \
      --enabled true \
      --frequency 300 \
      --timeout 30 \
      --locations "Id=${loc_id}" \
      --tags "hidden-link:${APPI_ID}=Resource" \
      >/dev/null 2>&1; then
      created=1
      break
    fi
  done

  if [[ "$created" != "1" ]]; then
    echo "Failed to create web test with provided WEBTEST_LOCATION_IDS=${WEBTEST_LOCATION_IDS}" >&2
    exit 1
  fi
fi

echo "Creating/updating alerts..."
if ! az monitor metrics alert show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CPU_ALERT_NAME" >/dev/null 2>&1; then
  az monitor metrics alert create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CPU_ALERT_NAME" \
    --scopes "$VM_ID" \
    --condition "avg Percentage CPU > 85" \
    --window-size 5m \
    --evaluation-frequency 1m \
    --severity 2 \
    --description "VM CPU is above 85% for 5 minutes." \
    --action "$ACTION_GROUP_ID" \
    >/dev/null
fi

HB_QUERY="Heartbeat | where TimeGenerated > ago(10m) | where _ResourceId =~ '$VM_ID'"
if ! az monitor scheduled-query show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$HEARTBEAT_ALERT_NAME" >/dev/null 2>&1; then
  az monitor scheduled-query create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$HEARTBEAT_ALERT_NAME" \
    --location "$LOCATION" \
    --scopes "$LAW_ID" \
    --condition "count 'HBQ' < 1" \
    --condition-query HBQ="$HB_QUERY" \
    --evaluation-frequency 5m \
    --window-size 10m \
    --severity 1 \
    --description "VM heartbeat missing for 10 minutes." \
    --action-groups "$ACTION_GROUP_ID" \
    >/dev/null
fi

ERR_QUERY="Syslog | where TimeGenerated > ago(10m) | where ProcessName has 'mdt-api' | where SyslogMessage has_any ('ERROR','Exception','Traceback')"
if ! az monitor scheduled-query show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$API_ERROR_ALERT_NAME" >/dev/null 2>&1; then
  az monitor scheduled-query create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$API_ERROR_ALERT_NAME" \
    --location "$LOCATION" \
    --scopes "$LAW_ID" \
    --condition "count 'ERRQ' > 5" \
    --condition-query ERRQ="$ERR_QUERY" \
    --evaluation-frequency 5m \
    --window-size 10m \
    --severity 2 \
    --description "API error volume is elevated in VM logs." \
    --action-groups "$ACTION_GROUP_ID" \
    >/dev/null
fi

DOWN_QUERY="AppAvailabilityResults | where TimeGenerated > ago(10m) | where Name =~ '$WEBTEST_NAME' | where Success == false"
if ! az monitor scheduled-query show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$API_DOWN_ALERT_NAME" >/dev/null 2>&1; then
  az monitor scheduled-query create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$API_DOWN_ALERT_NAME" \
    --location "$LOCATION" \
    --scopes "$LAW_ID" \
    --condition "count 'DOWNQ' > 0" \
    --condition-query DOWNQ="$DOWN_QUERY" \
    --evaluation-frequency 5m \
    --window-size 10m \
    --severity 1 \
    --description "API availability test is failing." \
    --action-groups "$ACTION_GROUP_ID" \
    >/dev/null
fi

echo "Observability setup complete."
echo "Log Analytics Workspace: $LOG_ANALYTICS_WORKSPACE"
echo "Application Insights: $APP_INSIGHTS_NAME"
echo "Action Group: $ACTION_GROUP_NAME (${ALERT_EMAIL})"
echo "Alerts:"
echo "  - $CPU_ALERT_NAME"
echo "  - $HEARTBEAT_ALERT_NAME"
echo "  - $API_ERROR_ALERT_NAME"
echo "  - $API_DOWN_ALERT_NAME"
echo "Web Test: $WEBTEST_NAME"
