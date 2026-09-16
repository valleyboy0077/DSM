#!/usr/bin/env bash
# Start DSM through Compose after confirming its required runtime secrets exist.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository_root"

# Compose reads .env itself. Read only the required values here so validation
# does not execute arbitrary shell syntax from that file. Shell values win.
dotenv_value() {
    local key="$1"
    local line value
    local pattern="^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=(.*)$"

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        if [[ "$line" =~ $pattern ]]; then
            value="${BASH_REMATCH[2]}"
            value="${value#"${value%%[![:space:]]*}"}"
            value="${value%"${value##*[![:space:]]}"}"
            if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
                value="${value:1:-1}"
            elif [[ "$value" == \'*\' && ${#value} -ge 2 ]]; then
                value="${value:1:-1}"
            fi
            printf '%s' "$value"
            return 0
        fi
    done < .env

    return 1
}

if [[ -f .env ]]; then
    if [[ -z "${DSM_ENCRYPTION_KEY-}" ]]; then
        export DSM_ENCRYPTION_KEY="$(dotenv_value DSM_ENCRYPTION_KEY || true)"
    fi
    if [[ -z "${DSM_BOOTSTRAP_ADMIN_PASSWORD-}" ]]; then
        export DSM_BOOTSTRAP_ADMIN_PASSWORD="$(dotenv_value DSM_BOOTSTRAP_ADMIN_PASSWORD || true)"
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

if [[ ! "$DSM_ENCRYPTION_KEY" =~ ^[0-9A-Fa-f]{32}$ ]]; then
    printf 'DSM_ENCRYPTION_KEY must contain exactly 32 hexadecimal characters.\n' >&2
    exit 1
fi

exec docker compose up --build --detach "$@"
