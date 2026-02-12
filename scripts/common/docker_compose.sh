#!/bin/bash

version_ge() {
  local lhs="$1"
  local rhs="$2"
  [ "$lhs" = "$rhs" ] && return 0
  [ "$(printf '%s\n%s\n' "$lhs" "$rhs" | sort -V | tail -n 1)" = "$lhs" ]
}

normalize_docker_api_env() {
  local required="${MIN_DOCKER_API_VERSION:-1.44}"
  if [ -n "${DOCKER_API_VERSION:-}" ] && ! version_ge "${DOCKER_API_VERSION}" "$required"; then
    echo "WARN: DOCKER_API_VERSION=${DOCKER_API_VERSION} is below required API ${required}; unsetting it." >&2
    unset DOCKER_API_VERSION
  fi
}

check_docker_client_api() {
  local docker_bin="$1"
  local required="${MIN_DOCKER_API_VERSION:-1.44}"
  local client_api=""
  client_api="$("$docker_bin" version --format '{{.Client.APIVersion}}' 2>/dev/null || true)"

  # If client API cannot be read here, let the actual docker call report details.
  if [ -z "$client_api" ]; then
    return 0
  fi

  if ! version_ge "$client_api" "$required"; then
    echo "ERROR: Docker client API version ${client_api} is older than required ${required}." >&2
    echo "ERROR: Upgrade docker CLI on the runner or point DOCKER_BIN to a newer binary." >&2
    return 1
  fi
}

# Resolve docker CLI path. Prefer real system docker when a local wrapper is first in PATH.
resolve_docker_bin() {
  local docker_bin="${DOCKER_BIN:-}"
  normalize_docker_api_env
  if [ -z "$docker_bin" ]; then
    docker_bin="$(command -v docker 2>/dev/null || true)"
  fi

  if [ -n "$docker_bin" ] && [[ "$docker_bin" == *"/.native-bin/"* ]]; then
    local candidate
    while IFS= read -r candidate; do
      if [ -n "$candidate" ] && [[ "$candidate" != *"/.native-bin/"* ]] && [ -x "$candidate" ]; then
        docker_bin="$candidate"
        break
      fi
    done < <(which -a docker 2>/dev/null | awk '!seen[$0]++')
  fi

  if [ -n "$docker_bin" ] && [[ "$docker_bin" == *"/.native-bin/"* ]]; then
    for candidate in /usr/bin/docker /usr/local/bin/docker /bin/docker; do
      if [ -x "$candidate" ]; then
        docker_bin="$candidate"
        break
      fi
    done
  fi

  echo "$docker_bin"
}

docker_cmd() {
  local docker_bin
  docker_bin="$(resolve_docker_bin)"
  if [ -z "$docker_bin" ]; then
    echo "ERROR: docker is not available in PATH." >&2
    return 1
  fi
  check_docker_client_api "$docker_bin" || return 1
  "$docker_bin" "$@"
}

compose_detect() {
  if [ "${COMPOSE_CMD_INITIALIZED:-0}" = "1" ]; then
    return 0
  fi

  local docker_bin
  docker_bin="$(resolve_docker_bin)"
  if [ -n "$docker_bin" ] && check_docker_client_api "$docker_bin" && "$docker_bin" compose version >/dev/null 2>&1; then
    COMPOSE_BACKEND="docker"
    COMPOSE_DOCKER_BIN="$docker_bin"
    COMPOSE_CMD_INITIALIZED=1
    return 0
  fi

  if [ "${ALLOW_DOCKER_COMPOSE_V1:-0}" = "1" ] && command -v docker-compose >/dev/null 2>&1; then
    echo "WARN: Falling back to legacy docker-compose v1. This can cause API compatibility issues." >&2
    COMPOSE_BACKEND="docker-compose"
    COMPOSE_DOCKER_COMPOSE_BIN="$(command -v docker-compose)"
    COMPOSE_CMD_INITIALIZED=1
    return 0
  fi

  echo "ERROR: Docker Compose plugin is required but unavailable for docker binary: ${docker_bin:-<none>}." >&2
  echo "ERROR: Install docker compose plugin and ensure 'docker compose version' works." >&2
  return 1
}

compose_cmd() {
  compose_detect || return 1

  if [ "$COMPOSE_BACKEND" = "docker" ]; then
    "$COMPOSE_DOCKER_BIN" compose "$@"
  else
    "$COMPOSE_DOCKER_COMPOSE_BIN" "$@"
  fi
}
