#!/bin/bash
set -eo pipefail

resolve_tool() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then echo "$name"; return; fi
  for dir in /opt/diag-tools /root/.dotnet/tools /usr/local/bin; do
    if [ -x "$dir/$name" ]; then echo "$dir/$name"; return; fi
  done
  echo "[diag] Searching for $name in image:" >&2
  find / -name "$name" -type f 2>/dev/null | head -3 >&2
  echo "$name"
}

start_dottrace() {
  local tool; tool=$(resolve_tool dottrace)
  echo "Starting dotTrace ($tool)..."
  exec "$tool" start \
    --framework=netcore \
    --profiling-type=timeline \
    --propagate-exit-code \
    --save-to=/nethermind/diag/dottrace \
    --service-output=on \
    -- ./nethermind "$@"
}

start_dotmemory() {
  local tool; tool=$(resolve_tool dotmemory)
  echo "Starting dotMemory ($tool)..."
  exec "$tool" start \
    --save-to-dir=/nethermind/diag/dotmemory \
    --service-output \
    ./nethermind -- "$@"
}

start_dotnet_trace() {
  local tool; tool=$(resolve_tool dotnet-trace)
  echo "Starting dotnet-trace ($tool)..."
  exec "$tool" collect \
    -o /nethermind/diag/dotnet.nettrace \
    --show-child-io \
    -- ./nethermind "$@"
}

case "${DIAG_WITH:-}" in
  "")
    exec ./nethermind "$@"
    ;;
  dottrace)
    start_dottrace "$@"
    ;;
  dotmemory)
    start_dotmemory "$@"
    ;;
  dotnet-trace)
    start_dotnet_trace "$@"
    ;;
  *)
    printf '\e[31mUnknown DIAG_WITH value: %q\e[0m\n' "$DIAG_WITH" >&2
    exit 2
    ;;
esac
