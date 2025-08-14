#!/usr/bin/env bash
#
# compact-json.sh
#
# For each .txt file in a given directory (default: cwd),
# read it as JSON, then output one JSON object per line
# by compacting and, if it’s an array, iterating its elements.
#

set -euo pipefail

DIR="${1:-.}"
shopt -s nullglob

for file in "$DIR"/*.txt; do
  # make sure it’s a regular file
  [ -f "$file" ] || continue

  # build a temp file
  tmp=$(mktemp)

  # parse & compact:
  #  - if top-level is an array, emit each element
  #  - otherwise emit the object itself
  jq -c 'if type=="array" then .[] else . end' "$file" > "$tmp"

  # overwrite the original
  mv -- "$tmp" "$file"
  echo "✔ Compacted: $file"
done
