#!/bin/bash

# Resolve docker CLI path. Prefer real system docker when a local wrapper is first in PATH.
resolve_docker_bin() {
  local docker_bin="${DOCKER_BIN:-}"
  if [ -z "$docker_bin" ]; then
    docker_bin="$(command -v docker 2>/dev/null || true)"
  fi

  if [ -n "$docker_bin" ] && [[ "$docker_bin" == *"/.native-bin/"* ]] && [ -x "/usr/bin/docker" ]; then
    docker_bin="/usr/bin/docker"
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
  "$docker_bin" "$@"
}

compose_detect() {
  if [ "${COMPOSE_CMD_INITIALIZED:-0}" = "1" ]; then
    return 0
  fi

  local docker_bin
  docker_bin="$(resolve_docker_bin)"
  if [ -n "$docker_bin" ] && "$docker_bin" compose version >/dev/null 2>&1; then
    COMPOSE_BACKEND="docker"
    COMPOSE_DOCKER_BIN="$docker_bin"
    COMPOSE_CMD_INITIALIZED=1
    return 0
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_BACKEND="docker-compose"
    COMPOSE_DOCKER_COMPOSE_BIN="$(command -v docker-compose)"
    COMPOSE_CMD_INITIALIZED=1
    return 0
  fi

  echo "ERROR: Docker Compose is not available (neither 'docker compose' nor 'docker-compose')." >&2
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
