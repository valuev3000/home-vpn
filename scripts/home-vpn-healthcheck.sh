#!/bin/sh
set -eu

setting() {
    key="$1"
    value="$(sed -n "s/^${key}=//p" /etc/vpn-shop.env 2>/dev/null | tail -n 1)"
    if [ -z "$value" ]; then
        value="$(systemctl show vpn-shop.service -p Environment --value | tr ' ' '\n' | sed -n "s/^${key}=//p" | tail -n 1)"
    fi
    printf '%s' "$value"
}

port="$(setting APP_PORT)"; port="${port:-8080}"
if [ -n "$(setting TLS_CERT)" ]; then scheme=https; insecure=--insecure; else scheme=http; insecure=; fi
health_url="${scheme}://127.0.0.1:${port}/health"

if /usr/bin/curl $insecure --fail --silent --show-error --max-time 8 "$health_url" >/dev/null; then
    exit 0
fi

/usr/bin/systemctl restart vpn-shop.service
/bin/sleep 3
/usr/bin/curl $insecure --fail --silent --show-error --max-time 8 "$health_url" >/dev/null
