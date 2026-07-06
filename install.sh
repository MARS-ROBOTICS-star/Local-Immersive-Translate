#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
export INSTALL_DIR="${INSTALL_DIR:-$script_dir}"
export SKIP_PROJECT_UPDATE=1

bash "$script_dir/scripts/install-local-backend.sh"
