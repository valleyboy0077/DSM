#!/usr/bin/env bash
# Start DSM through Compose after confirming its required runtime secrets exist.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository_root"

# Compose reads .env itself. Load it here too so this wrapper can validate the
# same inputs without echoing them; explicitly exported values take precedence.
if [[ -f .env ]]; then
    supplied_encryption_key="${DSM_ENCRYPTION_KEY-}"
    supplied_bootstrap_password="${DSM_BOOTSTRAP_ADMIN_PASSWORD-}"
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    if [[ -n "$supplied_encryption_key" ]]; then
        export DSM_ENCRYPTION_KEY="$supplied_encryption_key"
    fi
    if [[ -n "$supplied_bootstrap_password" ]]; then
        export DSM_BOOTSTRAP_ADMIN_PASSWORD="$supplied_bootstrap_password"
    fi
fi

require_secret() {
    local name="$1"
    local value="${!name:-}"
    if [[ -z "$value" || "$value" == replace_with_* ]]; then
        printf '%s must be set to a non-placeholder value in the environment or .env.\n' "$name" >&2
        exit 1
    fi
}

require_secret DSM_ENCRYPTION_KEY
require_secret DSM_BOOTSTRAP_ADMIN_PASSWORD

exec docker compose up --build --detach "$@"
