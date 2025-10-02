#!/bin/bash

wait_for_rpc() {
  local url="${1:-http://localhost:8545}"
  local max_attempts="${2:-600}"
  local attempt=1

  if ! command -v curl >/dev/null 2>&1; then
    echo "curl not available; skipping RPC readiness check" >&2
    return 0
  fi

  while [ "$attempt" -le "$max_attempts" ]; do
    if curl -s --max-time 2 -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
      "$url" | grep -q '"result"'; then
      echo "RPC at $url is ready (attempt $attempt/$max_attempts)"
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done

  echo "⚠️  RPC endpoint $url did not become ready after $max_attempts attempts" >&2
  return 1
}
