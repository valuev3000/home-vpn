#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Запустите: sudo bash install.sh" >&2
  exit 1
fi

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export HOME_VPN_SOURCE_ROOT="$root_dir"
exec python3 "$root_dir/scripts/home-vpn-manager.py" menu
