#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Запустите от root: sudo bash install-single-node.sh" >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
local_installer="$script_dir/scripts/install-home-vpn-single-node.py"
if [[ -f "$local_installer" && -f "$script_dir/VERSION" ]]; then
  exec python3 "$local_installer" "$@"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates git python3
temporary_dir="$(mktemp -d /tmp/home-vpn-installer.XXXXXX)"
cleanup() { rm -rf -- "$temporary_dir"; }
trap cleanup EXIT INT TERM
git clone --depth 1 https://github.com/valuev3000/home-vpn.git "$temporary_dir/home-vpn"
python3 "$temporary_dir/home-vpn/scripts/install-home-vpn-single-node.py" "$@"
