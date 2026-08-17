#!/usr/bin/env bash
set -euo pipefail

readonly container_name="jx-postgres-dev"
readonly volume_name="jx-postgres-dev-data"
readonly image_name="postgres:16.14-alpine3.24@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
readonly project_label_key="io.jixia-debate.foundation.postgres"
readonly project_label_value="jx-postgres-dev-v1"
readonly data_destination="/var/lib/postgresql/data"
readonly host_port="${POSTGRES_PORT:-5432}"

refuse_resource() {
  echo "Refusing to operate on ${1}: it is not an owned Jixia foundation resource." >&2
  exit 1
}

if ! [[ "$host_port" =~ ^[0-9]+$ ]] || ((host_port < 1 || host_port > 65535)); then
  echo "POSTGRES_PORT must be an integer between 1 and 65535." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is required for the local PostgreSQL helper." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is unavailable or cannot be accessed." >&2
  exit 1
fi

container_exists() {
  docker container inspect "$container_name" >/dev/null 2>&1
}

volume_exists() {
  docker volume inspect "$volume_name" >/dev/null 2>&1
}

validate_volume() {
  local actual_label
  actual_label="$(docker volume inspect \
    --format "{{ with .Labels }}{{ index . \"${project_label_key}\" }}{{ end }}" \
    "$volume_name")"
  if [[ "$actual_label" != "$project_label_value" ]]; then
    refuse_resource "volume ${volume_name}"
  fi
}

validate_container() {
  local actual_image actual_label actual_mount
  actual_label="$(docker inspect \
    --format "{{ with .Config.Labels }}{{ index . \"${project_label_key}\" }}{{ end }}" \
    "$container_name")"
  if [[ "$actual_label" != "$project_label_value" ]]; then
    refuse_resource "container ${container_name}"
  fi

  actual_image="$(docker inspect --format '{{.Config.Image}}' "$container_name")"
  if [[ "$actual_image" != "$image_name" ]]; then
    refuse_resource "container ${container_name}"
  fi

  actual_mount="$(docker inspect \
    --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Type}}|{{.Name}}|{{.Destination}}{{end}}{{end}}' \
    "$container_name")"
  if [[ "$actual_mount" != "volume|${volume_name}|${data_destination}" ]]; then
    refuse_resource "container ${container_name}"
  fi

  if ! volume_exists; then
    refuse_resource "volume ${volume_name}"
  fi
  validate_volume
}

validate_existing_resources() {
  if container_exists; then
    validate_container
  elif volume_exists; then
    validate_volume
  fi
}

ensure_owned_volume() {
  if volume_exists; then
    validate_volume
    return
  fi

  docker volume create \
    --label "${project_label_key}=${project_label_value}" \
    "$volume_name" >/dev/null
  validate_volume
}

wait_until_ready() {
  local attempt
  for attempt in $(seq 1 30); do
    if docker exec "$container_name" pg_isready -U jx -d jx_debate >/dev/null 2>&1; then
      echo "PostgreSQL is ready: ${container_name}"
      return 0
    fi
    sleep 1
  done
  echo "PostgreSQL did not become ready within 30 seconds." >&2
  docker logs --tail 80 "$container_name" >&2 || true
  exit 1
}

readonly action="${1:-}"
case "$action" in
  start | stop | status | logs)
    validate_existing_resources
    ;;
  *)
    echo "Usage: $0 {start|stop|status|logs}" >&2
    exit 2
    ;;
esac

case "$action" in
  start)
    if container_exists; then
      state="$(docker inspect --format '{{.State.Status}}' "$container_name")"
      if [[ "$state" != "running" ]]; then
        docker start "$container_name" >/dev/null
      else
        echo "PostgreSQL container is already running: ${container_name}"
      fi
    else
      ensure_owned_volume
      docker run --detach \
        --name "$container_name" \
        --label "${project_label_key}=${project_label_value}" \
        --publish "127.0.0.1:${host_port}:5432" \
        --volume "${volume_name}:${data_destination}" \
        --env POSTGRES_USER=jx \
        --env POSTGRES_PASSWORD=change-me \
        --env POSTGRES_DB=jx_debate \
        "$image_name" >/dev/null
      validate_container
    fi
    wait_until_ready
    ;;
  stop)
    if container_exists; then
      docker stop "$container_name" >/dev/null
      echo "Stopped ${container_name}; data volume was preserved."
    else
      echo "PostgreSQL container does not exist: ${container_name}"
    fi
    ;;
  status)
    if ! container_exists; then
      echo "PostgreSQL container does not exist: ${container_name}" >&2
      exit 1
    fi
    state="$(docker inspect --format '{{.State.Status}}' "$container_name")"
    if [[ "$state" != "running" ]]; then
      echo "PostgreSQL container is not running: ${container_name}" >&2
      exit 1
    fi
    if ! docker exec "$container_name" pg_isready -U jx -d jx_debate >/dev/null; then
      echo "PostgreSQL is not ready: ${container_name}" >&2
      exit 1
    fi
    echo "name=${container_name} status=running database=ready"
    ;;
  logs)
    if ! container_exists; then
      echo "PostgreSQL container does not exist: ${container_name}" >&2
      exit 1
    fi
    docker logs --tail 200 "$container_name"
    ;;
esac
