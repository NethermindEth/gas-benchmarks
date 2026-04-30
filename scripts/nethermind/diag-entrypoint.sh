#!/bin/bash
set -eo pipefail

start_dottrace() {
  echo "Starting dotTrace..."
  exec dottrace start \
    --framework=netcore \
    --profiling-type=timeline \
    --propagate-exit-code \
    --save-to=/nethermind/diag/dottrace \
    --service-output=on \
    -- ./nethermind "$@"
}

start_dotmemory() {
  echo "Starting dotMemory..."
  exec dotmemory start \
    --save-to-dir=/nethermind/diag/dotmemory \
    --service-output \
    ./nethermind -- "$@"
}

start_dotnet_trace() {
  echo "Starting dotnet-trace..."
  exec dotnet-trace collect \
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
