#!/usr/bin/env bash

set -euo pipefail

FAIL_ALL=false
if [[ "${1:-}" == "--all" ]]; then
  FAIL_ALL=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--all]" >&2
  exit 1
fi

command -v jq >/dev/null || exit 1
command -v yq >/dev/null || exit 1

ENV_FILE="src/modules/config/system/environment.yaml"
if [ ! -s "${ENV_FILE}" ]; then
  ENV_FILE="/app/${ENV_FILE}"
fi
if [ ! -s "${ENV_FILE}" ]; then
  ENV_FILE="/tmp/environment.yaml"
fi
test -s "${ENV_FILE}"

missing=()
broken=()
COUNT=0
missing_fallback=()
broken_fallback=()

# Extract (tool_name, command_binary, canary_command) tuples
while IFS=$'\t' read -r tool_name cmd preference canary; do
  # Skip empty lines just in case
  [[ -z "$cmd" ]] && continue

  COUNT=$((COUNT + 1))

  if ! command -v "$cmd" >/dev/null 2>&1; then
    if [[ "$FAIL_ALL" == true || "${preference:-}" != "fallback" ]]; then
      missing+=("$tool_name")
    else
      missing_fallback+=("$tool_name")
    fi
    continue
  fi

  # If a canary command is provided, run it; non-zero exit means the tool is missing/broken.
  # Use `bash -c` so the canary can be a compound shell command.
  if [[ -n "${canary:-}" ]]; then
    if ! bash -c "$canary" >/dev/null 2>&1; then
      if [[ "$FAIL_ALL" == true || "${preference:-}" != "fallback" ]]; then
        broken+=("$tool_name")
      else
        broken_fallback+=("$tool_name")
      fi
    fi
  fi

done < <(
  yq -r '.cyber_tools
         | to_entries[]
         | "\(.key)\t\(.value.command // .key)\t\(.value.preference // "")\t\(.value.canary // "")"' "$ENV_FILE"
)

if [ "${COUNT}" = "0" ]; then
  echo "No tools found" >&2
  exit 1
fi

if (( ${#missing[@]} > 0 )); then
  echo "Missing tools:" >&2
  printf '  %s\n' "${missing[@]}" >&2
fi

if (( ${#broken[@]} > 0 )); then
  echo "Broken tools:" >&2
  printf '  %s\n' "${broken[@]}" >&2
fi

if (( ${#missing_fallback[@]} > 0 )); then
  echo "Missing fallback tools:" >&2
  printf '  %s\n' "${missing_fallback[@]}" >&2
fi

if (( ${#broken_fallback[@]} > 0 )); then
  echo "Broken fallback tools:" >&2
  printf '  %s\n' "${broken_fallback[@]}" >&2
fi

if (( ${#missing[@]} > 0 )) || (( ${#broken[@]} > 0 )); then
  exit 1
fi

echo "${COUNT} tools in ${ENV_FILE} found."
