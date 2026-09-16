#!/usr/bin/env bash
# Build the local DSM deployment image without reading runtime secrets.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repository_root"

image_tag="${DSM_IMAGE_TAG:-dell-server-manager:local}"
exec docker build --file Dockerfile --tag "$image_tag" "$@" .
