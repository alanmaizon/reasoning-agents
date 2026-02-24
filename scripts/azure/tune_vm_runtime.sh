#!/usr/bin/env bash
set -euo pipefail

# Tune VM runtime for moderate concurrent traffic.
#
# Required:
#   VM_HOST
#
# Optional:
#   VM_USER (default: azureuser)
#   VM_PORT (default: 22)
#   SERVICE_NAME (default: mdt-api)
#   UVICORN_WORKERS (default: 2)
#   UVICORN_KEEPALIVE_TIMEOUT (default: 30)
#   NGINX_PROXY_CONNECT_TIMEOUT (default: 30s)
#   NGINX_PROXY_SEND_TIMEOUT (default: 300s)
#   NGINX_PROXY_READ_TIMEOUT (default: 300s)
#   HEALTHCHECK_URL (default: empty)

: "${VM_HOST:?VM_HOST is required}"

VM_USER="${VM_USER:-azureuser}"
VM_PORT="${VM_PORT:-22}"
SERVICE_NAME="${SERVICE_NAME:-mdt-api}"
UVICORN_WORKERS="${UVICORN_WORKERS:-2}"
UVICORN_KEEPALIVE_TIMEOUT="${UVICORN_KEEPALIVE_TIMEOUT:-30}"
NGINX_PROXY_CONNECT_TIMEOUT="${NGINX_PROXY_CONNECT_TIMEOUT:-30s}"
NGINX_PROXY_SEND_TIMEOUT="${NGINX_PROXY_SEND_TIMEOUT:-300s}"
NGINX_PROXY_READ_TIMEOUT="${NGINX_PROXY_READ_TIMEOUT:-300s}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"

SSH_OPTS=(
  -p "$VM_PORT"
  -o StrictHostKeyChecking=accept-new
)

echo "Applying runtime tuning on ${VM_USER}@${VM_HOST}..."

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_HOST}" \
  "SERVICE_NAME='$SERVICE_NAME' UVICORN_WORKERS='$UVICORN_WORKERS' UVICORN_KEEPALIVE_TIMEOUT='$UVICORN_KEEPALIVE_TIMEOUT' NGINX_PROXY_CONNECT_TIMEOUT='$NGINX_PROXY_CONNECT_TIMEOUT' NGINX_PROXY_SEND_TIMEOUT='$NGINX_PROXY_SEND_TIMEOUT' NGINX_PROXY_READ_TIMEOUT='$NGINX_PROXY_READ_TIMEOUT' bash -s" <<'EOF'
set -euo pipefail

APP_DIR="/home/azureuser/app"
SERVICE_NAME="${SERVICE_NAME:-mdt-api}"
UVICORN_WORKERS="${UVICORN_WORKERS:-2}"
UVICORN_KEEPALIVE_TIMEOUT="${UVICORN_KEEPALIVE_TIMEOUT:-30}"

sudo mkdir -p "/etc/systemd/system/${SERVICE_NAME}.service.d"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service.d/override.conf" >/dev/null <<EOC
[Service]
ExecStart=
ExecStart=${APP_DIR}/.venv/bin/uvicorn src.api:app --host 127.0.0.1 --port 8000 --workers ${UVICORN_WORKERS} --timeout-keep-alive ${UVICORN_KEEPALIVE_TIMEOUT}
EOC

sudo tee /etc/nginx/conf.d/mdt-api-timeouts.conf >/dev/null <<EON
proxy_connect_timeout ${NGINX_PROXY_CONNECT_TIMEOUT};
proxy_send_timeout ${NGINX_PROXY_SEND_TIMEOUT};
proxy_read_timeout ${NGINX_PROXY_READ_TIMEOUT};
send_timeout ${NGINX_PROXY_READ_TIMEOUT};
EON

sudo systemctl daemon-reload
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl is-active "${SERVICE_NAME}" >/dev/null

sudo nginx -t >/dev/null
sudo systemctl reload nginx

echo "Runtime tuning applied."
echo "Service: ${SERVICE_NAME}"
echo "Workers: ${UVICORN_WORKERS}"
echo "KeepAlive: ${UVICORN_KEEPALIVE_TIMEOUT}s"
echo "Nginx timeouts: connect=${NGINX_PROXY_CONNECT_TIMEOUT}, send=${NGINX_PROXY_SEND_TIMEOUT}, read=${NGINX_PROXY_READ_TIMEOUT}"
EOF

if [[ -n "$HEALTHCHECK_URL" ]]; then
  echo "Running health check..."
  ok=0
  for _ in $(seq 1 20); do
    if curl -fsS "$HEALTHCHECK_URL" >/dev/null 2>&1; then
      ok=1
      break
    fi
    sleep 2
  done
  if [[ "$ok" -ne 1 ]]; then
    echo "Health check failed: ${HEALTHCHECK_URL}" >&2
    exit 1
  fi
fi

echo "Runtime tuning succeeded."
