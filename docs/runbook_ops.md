# Operations Runbook (VM + API)

This runbook covers day-2 operations for the VM-hosted MDT API deployment in Azure.

## Scope

- FastAPI service (`mdt-api`) on Linux VM
- Nginx reverse proxy + TLS termination
- PostgreSQL Flexible Server backend
- Entra-protected `/v1/*` endpoints
- Azure Monitor + Application Insights observability

## Key Endpoints

- API health: `https://<vm-fqdn>/healthz`
- Frontend shell: `https://<vm-fqdn>/`

## Core Commands

### VM power controls

```bash
az vm start -g <resource-group> -n <vm-name>
az vm deallocate -g <resource-group> -n <vm-name>
az vm show -d -g <resource-group> -n <vm-name> --query "{power:powerState,ip:publicIps}" -o json
```

### Service controls (inside VM)

```bash
ssh azureuser@<vm-fqdn>
sudo systemctl status mdt-api
sudo systemctl restart mdt-api
sudo systemctl status nginx
sudo systemctl restart nginx
```

### Recent logs

```bash
ssh azureuser@<vm-fqdn>
sudo journalctl -u mdt-api -n 200 --no-pager
sudo journalctl -u nginx -n 120 --no-pager
```

### Redeploy code

```bash
export VM_HOST=<vm-fqdn>
export VM_USER=azureuser
export HEALTHCHECK_URL=https://<vm-fqdn>/healthz
bash scripts/azure/deploy_vm_code.sh
```

## Observability Provisioning

Re-run setup safely (idempotent):

```bash
export RESOURCE_GROUP=<resource-group>
export LOCATION=swedencentral
export VM_NAME=<vm-name>
export HEALTHCHECK_URL=https://<vm-fqdn>/healthz
export ALERT_EMAIL=<notification-email>
bash scripts/azure/setup_observability.sh
```

Default resources created by the script:

- Log Analytics workspace: `law-mdt-obsv`
- App Insights component: `appi-mdt-obsv`
- Data Collection Rule: `dcr-mdt-obsv`
- Action group: `ag-mdt-obsv`
- Web test: `wt-mdt-healthz`
- Alerts:
  - `alert-vm-cpu-high`
  - `alert-vm-heartbeat-miss`
  - `alert-api-error-rate`
  - `alert-api-down`

## Common Incidents

### Incident: API returns 401 for all `/v1/*` calls

1. Verify Entra variables on VM:

```bash
ssh azureuser@<vm-fqdn> "sudo grep '^ENTRA_' /etc/mdt-api.env"
```

2. Acquire a fresh token and retry:

```bash
API_APP_ID=<api-app-id>
TOKEN=$(az account get-access-token --scope "api://$API_APP_ID/api.access" --query accessToken -o tsv)
curl -H "Authorization: Bearer $TOKEN" https://<vm-fqdn>/v1/state/test
```

3. Restart `mdt-api` if config changed.

### Incident: 5xx or startup failure

1. Check service logs:

```bash
ssh azureuser@<vm-fqdn> "sudo journalctl -u mdt-api -n 200 --no-pager"
```

2. Validate env file is present and readable:

```bash
ssh azureuser@<vm-fqdn> "sudo ls -l /etc/mdt-api.env"
```

3. Restart service and re-check health.

### Incident: DB connectivity errors

1. Validate PostgreSQL settings in `/etc/mdt-api.env`.
2. Check DB server state:

```bash
az postgres flexible-server show -g <data-rg> -n <pg-server> --query "{state:state,version:version}" -o json
```

3. If password was rotated, sync VM env and restart service.

### Incident: TLS issues

1. Check certbot timer and certificate:

```bash
ssh azureuser@<vm-fqdn> "systemctl status certbot.timer --no-pager"
ssh azureuser@<vm-fqdn> "sudo certbot certificates"
```

2. Reload nginx if cert files changed.

## Log Analytics Queries

### API errors from syslog

```kusto
Syslog
| where TimeGenerated > ago(30m)
| where ProcessName has "mdt-api"
| where SyslogMessage has_any ("ERROR", "Exception", "Traceback")
| project TimeGenerated, Computer, ProcessName, SyslogMessage
| order by TimeGenerated desc
```

### VM heartbeat

```kusto
Heartbeat
| where TimeGenerated > ago(30m)
| summarize LastSeen=max(TimeGenerated) by Computer, _ResourceId
| order by LastSeen desc
```

### Availability test failures

```kusto
AppAvailabilityResults
| where TimeGenerated > ago(30m)
| where Name =~ "wt-mdt-healthz"
| where Success == false
| project TimeGenerated, Name, Location, Message, DurationMs
| order by TimeGenerated desc
```

## Recovery Checklist

1. VM running and reachable.
2. `mdt-api` active and `/healthz` returns `200`.
3. Authenticated `/v1/state/<user>` returns `200`.
4. PostgreSQL read/write path validated.
5. Alerts and action group still present.
