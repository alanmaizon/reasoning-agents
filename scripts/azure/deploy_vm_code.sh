#!/usr/bin/env bash
set -euo pipefail

# Deploy current repo code to an Azure VM running the mdt-api systemd service.
#
# Required:
#   VM_HOST
#
# Optional:
#   VM_USER (default: azureuser)
#   VM_PORT (default: 22)
#   APP_DIR (default: /home/<VM_USER>/app)
#   SERVICE_NAME (default: mdt-api)
#   HEALTHCHECK_URL (default: empty; when set, curl is executed after deploy)

: "${VM_HOST:?VM_HOST is required}"

VM_USER="${VM_USER:-azureuser}"
VM_PORT="${VM_PORT:-22}"
APP_DIR="${APP_DIR:-/home/${VM_USER}/app}"
SERVICE_NAME="${SERVICE_NAME:-mdt-api}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"

SSH_OPTS=(
  -p "$VM_PORT"
  -o StrictHostKeyChecking=accept-new
)
SCP_OPTS=(
  -P "$VM_PORT"
  -o StrictHostKeyChecking=accept-new
)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_TAR="$(mktemp /tmp/reasoning-agents-deploy.XXXXXX.tar.gz)"
trap 'rm -f "$TMP_TAR"' EXIT

echo "Packaging repository..."
export COPYFILE_DISABLE=1
tar -C "$ROOT_DIR" -czf "$TMP_TAR" \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "venv" \
  --exclude "env" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".data" \
  --exclude "student_state.json" \
  --exclude "*.pyc" \
  --exclude "*.pyo" \
  --exclude ".DS_Store" \
  .

echo "Uploading package to ${VM_USER}@${VM_HOST}..."
scp "${SCP_OPTS[@]}" "$TMP_TAR" "${VM_USER}@${VM_HOST}:/tmp/reasoning-agents-deploy.tar.gz"

echo "Applying deploy on VM..."
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_HOST}" \
  "APP_DIR='$APP_DIR' SERVICE_NAME='$SERVICE_NAME' bash -s" <<'EOF'
set -euo pipefail

mkdir -p "$APP_DIR"
tar --warning=no-unknown-keyword -xzf /tmp/reasoning-agents-deploy.tar.gz -C "$APP_DIR"

cd "$APP_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt >/dev/null

sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active "$SERVICE_NAME" >/dev/null
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

echo "Deploy succeeded."
echo "Target: ${VM_USER}@${VM_HOST}"
if [[ -n "$HEALTHCHECK_URL" ]]; then
  echo "Health: ${HEALTHCHECK_URL}"
fi
