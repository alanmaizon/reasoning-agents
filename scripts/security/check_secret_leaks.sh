#!/usr/bin/env bash
set -euo pipefail

# Scans tracked files for obvious secret leakage patterns.
# Intended as a lightweight CI guardrail (not a replacement for full secret scanners).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if ! command -v rg >/dev/null 2>&1; then
  echo "ripgrep (rg) is required for secret scanning." >&2
  exit 2
fi

declare -a findings

scan() {
  local pattern="$1"
  local label="$2"
  local out

  out="$(
    git ls-files -z \
      | xargs -0 rg -n -H -e "$pattern" \
      || true
  )"

  if [[ -n "$out" ]]; then
    findings+=("[$label]"$'\n'"$out")
  fi
}

# Private keys or cert material.
scan "-----BEGIN [A-Z ]*PRIVATE KEY-----" "private-key-material"

# Direct secret assignments in tracked files.
scan "^[[:space:]]*(AZURE_OPENAI_API_KEY|POSTGRES_PASSWORD|DATABASE_URL|AZURE_STORAGE_CONNECTION_STRING|ENTRA_CLIENT_SECRET)[[:space:]]*=[[:space:]]*[^#[:space:]]+" "inline-secret-assignment"

# Azure Storage connection strings containing account keys.
scan "DefaultEndpointsProtocol=[^[:space:]]*;[^[:space:]]*AccountKey=" "azure-storage-account-key"

# DSN with embedded password in plain text.
scan "postgres(ql)?://[^[:space:]]+:[^[:space:]@]+@" "postgres-dsn-with-password"

if ((${#findings[@]} > 0)); then
  echo "Potential secret leaks detected in tracked files:" >&2
  printf '%s\n\n' "${findings[@]}" >&2
  exit 1
fi

echo "Secret scan passed (no obvious leaks in tracked files)."
