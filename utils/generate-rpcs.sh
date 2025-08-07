#!/usr/bin/env bash
# usage: ./generate-rpcs.sh path/to/json/files
set -euo pipefail

DIR="$1"

for f in "$DIR"/*.json; do
  out="${f%.json}.rpc.json"
  jq -f make_rpc.jq "$f" > "$out"
  echo "→ $out"
done
