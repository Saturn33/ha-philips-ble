#!/usr/bin/env bash
# Development only: copy the integration to the Home Assistant VM and restart HA.
#
#   tools/deploy_dev.sh               copy, then restart Home Assistant
#   tools/deploy_dev.sh --no-restart  copy only (then reload the entry, e.g. after a translation change)
#
# Settings: tools/deploy.env (copy tools/deploy.env.example; never committed).
# Needs SSH access to HA: the "Advanced SSH & Web Terminal" or "Terminal & SSH" add-on with your public key.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(dirname "$here")"
env_file="${DEPLOY_ENV:-$here/deploy.env}"

restart=1
for arg in "$@"; do
    case "$arg" in
        --no-restart) restart=0 ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ ! -f "$env_file" ]; then
    echo "Missing $env_file: copy tools/deploy.env.example to tools/deploy.env and fill it in." >&2
    exit 1
fi
# shellcheck source=/dev/null
. "$env_file"
: "${HA_SSH:?HA_SSH is not set in $env_file}"
port="${HA_SSH_PORT:-22}"
src="$repo/custom_components/ble_mesh_lights/"
dest="${HA_CONFIG:-/config}/custom_components/ble_mesh_lights"

sync="${DEPLOY_SYNC:-auto}"
if [ "$sync" = auto ]; then
    # The SSH add-ons do not always have rsync; tar over SSH works everywhere.
    if command -v rsync > /dev/null && ssh -p "$port" "$HA_SSH" command -v rsync > /dev/null 2>&1; then
        sync=rsync
    else
        sync=tar
    fi
fi

echo "Copying to $HA_SSH:$dest ($sync)"
if [ "$sync" = rsync ]; then
    rsync -a --delete --exclude=__pycache__ -e "ssh -p $port" "$src" "$HA_SSH:$dest/"
else
    tar -C "$src" --exclude=__pycache__ -cf - . |
        ssh -p "$port" "$HA_SSH" "rm -rf $dest && mkdir -p $dest && tar -C $dest -xf -"
fi

if [ "$restart" = 1 ]; then
    echo "Restarting Home Assistant"
    if [ -n "${HA_TOKEN:-}" ] && [ -n "${HA_URL:-}" ]; then
        curl -fsS -X POST -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/services/homeassistant/restart" > /dev/null
    else
        ssh -p "$port" "$HA_SSH" ha core restart
    fi
fi
