#!/bin/bash
# Toggle TOPSTEPX_USE_RUST in repo root .env (on | off | status)

set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO/.env"

usage() {
  echo "Usage: $0 on|off|status" >&2
  exit 2
}

set_rust_in_env() {
  local desired="$1"
  # Double-quoted value
  sed -i '' -e "s/^TOPSTEPX_USE_RUST=\"[^\"]*\"/TOPSTEPX_USE_RUST=\"${desired}\"/" "$ENV_FILE"
  # Single-quoted value
  sed -i '' -e "s/^TOPSTEPX_USE_RUST='[^']*'/TOPSTEPX_USE_RUST='${desired}'/" "$ENV_FILE"
  # Unquoted value (only if not already double- or single-quoted form)
  sed -i '' "/^TOPSTEPX_USE_RUST=\"/!{
/^TOPSTEPX_USE_RUST='/!{
/^TOPSTEPX_USE_RUST=/s/^TOPSTEPX_USE_RUST=.*/TOPSTEPX_USE_RUST=${desired}/
}
}" "$ENV_FILE"
}

case "${1:-}" in
on)
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: .env not found at $ENV_FILE" >&2
    exit 1
  fi
  cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
  set_rust_in_env "true"
  if ! grep -q '^TOPSTEPX_USE_RUST=' "$ENV_FILE"; then
    printf '%s\n' "TOPSTEPX_USE_RUST=true" >>"$ENV_FILE"
  fi
  grep '^TOPSTEPX_USE_RUST=' "$ENV_FILE" | tail -n1
  ;;
off)
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: .env not found at $ENV_FILE" >&2
    exit 1
  fi
  cp "$ENV_FILE" "${ENV_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
  set_rust_in_env "false"
  if ! grep -q '^TOPSTEPX_USE_RUST=' "$ENV_FILE"; then
    printf '%s\n' "TOPSTEPX_USE_RUST=false" >>"$ENV_FILE"
  fi
  grep '^TOPSTEPX_USE_RUST=' "$ENV_FILE" | tail -n1
  ;;
status)
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: .env not found at $ENV_FILE" >&2
    exit 1
  fi
  if grep -q '^TOPSTEPX_USE_RUST=' "$ENV_FILE"; then
    grep '^TOPSTEPX_USE_RUST=' "$ENV_FILE"
  else
    echo "TOPSTEPX_USE_RUST is not set in .env"
  fi
  ;;
*)
  usage
  ;;
esac
