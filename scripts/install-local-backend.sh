#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/Local-Immersive-Translate}"
REPO_REF="${REPO_REF:-}"
BABELDOC_URL="${BABELDOC_URL:-https://github.com/funstory-ai/BabelDOC.git}"
BABELDOC_REF="${BABELDOC_REF:-}"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
ASSUME_YES="${ASSUME_YES:-0}"
uv_install_script=""

cleanup() {
  if [[ -n "$uv_install_script" && -f "$uv_install_script" ]]; then
    rm -f "$uv_install_script"
  fi
}

trap cleanup EXIT

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"

  command -v "$command_name" >/dev/null 2>&1 || die "'$command_name' is required but was not found in PATH."
}

clone_or_update() {
  local repo_url="$1"
  local target_dir="$2"
  local repo_ref="${3:-}"
  local origin_url

  if [[ -d "$target_dir/.git" ]]; then
    origin_url="$(git -C "$target_dir" remote get-url origin 2>/dev/null)" || die "$target_dir already contains a Git repository, but its origin remote could not be read."
    if [[ "$origin_url" != "$repo_url" ]]; then
      die "$target_dir already contains a different Git repository. Expected origin: $repo_url; actual origin: $origin_url"
    fi
  elif [[ -e "$target_dir" ]]; then
    die "$target_dir already exists but is not a git repository."
  else
    git clone "$repo_url" "$target_dir"
  fi

  git -C "$target_dir" fetch --tags origin
  if [[ -n "$repo_ref" ]]; then
    git -C "$target_dir" checkout "$repo_ref"
  else
    git -C "$target_dir" pull --ff-only
  fi
}

require_command git

clone_or_update "$REPO_URL" "$INSTALL_DIR" "$REPO_REF"

if ! command -v uv >/dev/null 2>&1; then
  require_command curl
  if [[ "$ASSUME_YES" != "1" && "$ASSUME_YES" != "true" ]]; then
    printf 'uv is not installed.\n'
    printf 'This installer will download and execute the official uv installer from:\n%s\n' "$UV_INSTALL_URL"
    printf 'Continue? [y/N] '
    read -r answer
    case "$answer" in
      y | Y | yes | YES) ;;
      *)
        die "Aborted. Install uv manually from https://docs.astral.sh/uv/ or rerun with ASSUME_YES=1."
        ;;
    esac
  fi
  uv_install_script="$(mktemp "${TMPDIR:-/tmp}/uv-install.XXXXXX.sh")"
  printf 'Downloading uv installer from: %s\n' "$UV_INSTALL_URL"
  curl -LsSf "$UV_INSTALL_URL" -o "$uv_install_script"
  printf 'Executing uv installer from temporary file: %s\n' "$uv_install_script"
  sh "$uv_install_script"
  export PATH="$HOME/.local/bin:$PATH"
fi

command -v uv >/dev/null 2>&1 || die "uv installation failed or uv is not available in PATH."

clone_or_update "$BABELDOC_URL" "$INSTALL_DIR/BabelDOC" "$BABELDOC_REF"
uv --directory "$INSTALL_DIR/BabelDOC" sync

printf '\nProject directory: %s\n' "$INSTALL_DIR"
printf 'uv path: %s\n' "$(command -v uv)"
printf 'Open Zotero preferences, then click Start / Test for the local backend.\n'
