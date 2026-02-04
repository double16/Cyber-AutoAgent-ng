#!/usr/bin/env bash

set -euo pipefail

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

# Extract (tool_name, command_binary, canary_command) tuples
while IFS=$'\t' read -r tool_name cmd canary; do
  # Skip empty lines just in case
  [[ -z "$cmd" ]] && continue

  COUNT=$((COUNT + 1))

  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("$tool_name")
    continue
  fi

  # If a canary command is provided, run it; non-zero exit means the tool is missing/broken.
  # Use `bash -c` so the canary can be a compound shell command.
  if [[ -n "${canary:-}" ]]; then
    if ! bash -c "$canary" >/dev/null 2>&1; then
      broken+=("$tool_name")
    fi
  fi

done < <(
  yq -r '.cyber_tools
         | to_entries[]
         | "\(.key)\t\(.value.command // .key)\t\(.value.canary // "")"' "$ENV_FILE"
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

if (( ${#missing[@]} > 0 )) || (( ${#broken[@]} > 0 )); then
  exit 1
fi

echo "${COUNT} tools in ${ENV_FILE} found."
