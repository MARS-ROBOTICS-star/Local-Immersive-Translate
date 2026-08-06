#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MARS-ROBOTICS-star/Local-Immersive-Translate.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/Local-Immersive-Translate}"
REPO_REF="${REPO_REF:-}"
BABELDOC_URL="${BABELDOC_URL:-https://github.com/funstory-ai/BabelDOC.git}"
BABELDOC_REF="${BABELDOC_REF:-v0.6.4}"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
ASSUME_YES="${ASSUME_YES:-0}"
SKIP_PROJECT_UPDATE="${SKIP_PROJECT_UPDATE:-0}"
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

github_archive_url() {
  local repo_url="$1"
  local repo_ref="${2:-main}"
  local normalized_url

  normalized_url="${repo_url%.git}"
  if [[ "$normalized_url" == git@github.com:* ]]; then
    normalized_url="https://github.com/${normalized_url#git@github.com:}"
  fi
  printf '%s/archive/refs/heads/%s.tar.gz\n' "$normalized_url" "$repo_ref"
}

install_from_archive() {
  local repo_url="$1"
  local target_dir="$2"
  local repo_ref="${3:-main}"
  local marker_file="${4:-}"
  local archive_url
  local archive_file
  local extract_dir

  if [[ -d "$target_dir" ]]; then
    if [[ -n "$marker_file" && -f "$target_dir/$marker_file" ]]; then
      printf '%s already exists and is not a Git repository; using it as-is.\n' "$target_dir"
      return
    fi
    die "$target_dir already exists but is not a recognized installation directory."
  elif [[ -e "$target_dir" ]]; then
    die "$target_dir already exists but is not a directory."
  fi

  require_command curl
  require_command tar
  archive_url="$(github_archive_url "$repo_url" "$repo_ref")"
  archive_file="$(mktemp "${TMPDIR:-/tmp}/local-immersive-translate.XXXXXX.tar.gz")"
  extract_dir="$(mktemp -d "${TMPDIR:-/tmp}/local-immersive-translate.XXXXXX")"

  printf 'git was not found; downloading archive from: %s\n' "$archive_url"
  curl -LsSf "$archive_url" -o "$archive_file"
  mkdir -p "$target_dir"
  tar -xzf "$archive_file" -C "$extract_dir"
  local extracted_root
  extracted_root="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -n "$extracted_root" ]] || die "Downloaded archive did not contain a project directory."
  cp -R "$extracted_root"/. "$target_dir"/
  rm -rf "$archive_file" "$extract_dir"
}

clone_or_update() {
  local repo_url="$1"
  local target_dir="$2"
  local repo_ref="${3:-}"
  local marker_file="${4:-}"
  local origin_url

  if ! command -v git >/dev/null 2>&1; then
    install_from_archive "$repo_url" "$target_dir" "${repo_ref:-main}" "$marker_file"
    return
  fi

  if [[ -d "$target_dir/.git" ]]; then
    origin_url="$(git -C "$target_dir" remote get-url origin 2>/dev/null)" || die "$target_dir already contains a Git repository, but its origin remote could not be read."
    if [[ "$origin_url" != "$repo_url" ]]; then
      die "$target_dir already contains a different Git repository. Expected origin: $repo_url; actual origin: $origin_url"
    fi
  elif [[ -e "$target_dir" ]]; then
    if [[ -n "$marker_file" && -f "$target_dir/$marker_file" ]]; then
      printf '%s already exists and is not a Git repository; using it as-is.\n' "$target_dir"
      return
    fi
    die "$target_dir already exists but is not a recognized installation directory."
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

if [[ "$SKIP_PROJECT_UPDATE" != "1" && "$SKIP_PROJECT_UPDATE" != "true" ]]; then
  clone_or_update "$REPO_URL" "$INSTALL_DIR" "$REPO_REF" "package.json"
fi

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

clone_or_update "$BABELDOC_URL" "$INSTALL_DIR/BabelDOC" "$BABELDOC_REF" "pyproject.toml"
uv --directory "$INSTALL_DIR/BabelDOC" sync
babeldoc_python="$(uv --directory "$INSTALL_DIR/BabelDOC" run python -c 'import sys; print(sys.executable)')"
uv pip install --python "$babeldoc_python" -r "$INSTALL_DIR/local_babeldoc_server/requirements.txt"
"$babeldoc_python" -c 'from google import genai; assert genai is not None'
"$babeldoc_python" -c 'from rapidocr import RapidOCR; RapidOCR()'

install_agent_skill() {
  local skill_src="$INSTALL_DIR/.opencode/skills/zotero-translate-triage/SKILL.md"
  local skill_dir="${OPENCODE_SKILLS_DIR:-$HOME/.config/opencode/skills}"
  if [[ -f "$skill_src" ]]; then
    mkdir -p "$skill_dir/zotero-translate-triage"
    cp -f "$skill_src" "$skill_dir/zotero-translate-triage/SKILL.md"
    printf 'Agent debugging skill installed to: %s\n' "$skill_dir/zotero-translate-triage/SKILL.md"
    printf 'Restart opencode (if running) for the skill to take effect.\n'
  else
    printf 'Skill file not found in %s; skipping agent skill installation.\n' "$INSTALL_DIR"
  fi
}
install_agent_skill

printf '\nProject directory: %s\n' "$INSTALL_DIR"
printf 'uv path: %s\n' "$(command -v uv)"
printf 'Open Zotero preferences, then click Start / Test for the local backend.\n'
