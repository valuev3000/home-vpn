#!/usr/bin/env python3
"""Rebuild the HOME-VPN test topology on two Proxmox LXC containers.

RU is the management plane with no local VPN inbounds. PL contains ten
3X-UI_KIT protocols plus the existing Emergency MTProto inbound. Connections
can remain direct or enter through RU and be forwarded to PL.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
KIT_VERSION = "v1.2"
KIT_URL = f"https://raw.githubusercontent.com/itsnotkubrick/3X-UI_KIT/{KIT_VERSION}/scripts/3x-ui.sh"
KIT_SHA256 = "2d7a0f0dcc26ea4f8bab3d60e1900e7046a9673a88fef85c03630502120961f0"
KIT_PROTOCOLS = "reality,hy2,xhttp,ws,trojan,vmess,ss,tuic,awg,awg3"
EXPECTED_REMARKS = (
    "REALITY", "Hysteria2", "XHTTP", "VLESS-WS", "Trojan-gRPC",
    "VMess-WS", "Shadowsocks", "TUIC", "AmneziaWG", "AmneziaWG-3.1",
)


def run(arguments, *, input_text=None, capture=False, timeout=None):
    return subprocess.run(
        [str(x) for x in arguments], input=input_text, text=True,
        check=True, capture_output=capture, timeout=timeout,
    )


def pct_script(host, ctid, script, *, capture=False, timeout=None):
    return run(
        ["ssh", host, "pct", "exec", str(ctid), "--", "bash", "-s"],
        input_text=script, capture=capture, timeout=timeout,
    )


def pct_python(host, ctid, script, *, capture=False):
    return run(
        ["ssh", host, "pct", "exec", str(ctid), "--", "python3", "-"],
        input_text=script, capture=capture,
    )


def download_verified_installer(target):
    with urllib.request.urlopen(KIT_URL, timeout=60) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != KIT_SHA256:
        raise RuntimeError(f"Unexpected 3X-UI_KIT installer SHA-256: {digest}")
    target.write_bytes(payload)
    target.chmod(0o700)


def create_rollback(host, ctid):
    result = pct_script(host, ctid, r'''set -Eeuo pipefail
stamp=$(date +%Y%m%d-%H%M%S)
target=/root/pre-home-vpn-topology-${stamp}
install -d -m 700 "$target"
python3 - /etc/x-ui/x-ui.db "$target/x-ui.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
destination = sqlite3.connect(sys.argv[2])
source.backup(destination)
destination.close(); source.close()
PY
tar -czf "$target/config.tar.gz" /etc/x-ui /etc/home-vpn /etc/vpn-shop.env 2>/dev/null || true
chmod -R go-rwx "$target"
printf '%s\n' "$target"
''', capture=True)
    return result.stdout.strip().splitlines()[-1]


def clean_ru(host, ctid):
    code = r'''import sqlite3
path = "/etc/x-ui/x-ui.db"
database = sqlite3.connect(path)
database.execute("DELETE FROM inbounds WHERE COALESCE(node_id, 0) = 0")
database.commit(); database.close()
'''
    pct_script(host, ctid, "set -Eeuo pipefail\nsystemctl stop x-ui\n")
    try:
        pct_python(host, ctid, code)
    finally:
        pct_script(host, ctid, "set -Eeuo pipefail\nsystemctl start x-ui\n")
    pct_script(host, ctid, r'''set -Eeuo pipefail
systemctl disable --now nginx kit-sub kit-update.timer kit-limit.timer 2>/dev/null || true
''')


def clean_pl_except_emergency(host, ctid, emergency_remark):
    code = r'''import json, sqlite3, sys
path, remark = "/etc/x-ui/x-ui.db", sys.argv[1]
database = sqlite3.connect(path)
rows = database.execute("SELECT id, remark, protocol, port FROM inbounds WHERE remark = ?", (remark,)).fetchall()
if len(rows) != 1 or rows[0][2] != "mtproto":
    raise SystemExit("Expected exactly one Emergency MTProto inbound")
emergency_id = rows[0][0]
database.execute("DELETE FROM inbounds WHERE id != ?", (emergency_id,))
database.commit()
print(json.dumps({"id": emergency_id, "port": rows[0][3]}, ensure_ascii=False))
database.close()
'''
    pct_script(host, ctid, "set -Eeuo pipefail\nsystemctl stop x-ui\n")
    try:
        result = run(
            ["ssh", host, "pct", "exec", str(ctid), "--", "python3", "-", emergency_remark],
            input_text=code, capture=True,
        )
    finally:
        pct_script(host, ctid, "set -Eeuo pipefail\nsystemctl start x-ui\n")
    return json.loads(result.stdout)


def prepare_lxc_override(host, ctid):
    pct_script(host, ctid, r'''set -Eeuo pipefail
getent group kitsub >/dev/null || groupadd --system kitsub
getent passwd kitsub >/dev/null || useradd --system --gid kitsub --home-dir /nonexistent --shell /usr/sbin/nologin kitsub
install -d -m 0755 /etc/systemd/system/kit-sub.service.d
cat > /etc/systemd/system/kit-sub.service.d/lxc.conf <<'EOF'
[Service]
LoadCredential=
DynamicUser=no
User=kitsub
Group=kitsub
Environment=KIT_SUB_CONFIG=/etc/kit-sub/config.json
ExecStartPre=+/bin/chgrp kitsub /etc/kit-sub
ExecStartPre=+/bin/chmod 0750 /etc/kit-sub
ExecStartPre=+/bin/chgrp kitsub /etc/kit-sub/config.json
ExecStartPre=+/bin/chmod 0640 /etc/kit-sub/config.json
ProtectSystem=false
ProtectHome=false
PrivateTmp=false
PrivateDevices=false
ProtectProc=default
ProtectKernelTunables=false
ProtectKernelModules=false
ProtectControlGroups=false
EOF
''')


def install_kit(host, ctid, address, sni, cert, key):
    quoted = {name: shlex.quote(value) for name, value in {
        "address": address, "sni": sni, "cert": cert, "key": key,
    }.items()}
    command = (
        "set -o pipefail\n"
        "timeout --signal=INT 600 bash /root/3x-ui-kit-v1.2.sh "
        f"--host {quoted['address']} --sni {quoted['sni']} --cert {quoted['cert']} --key {quoted['key']} "
        f"--protocols {KIT_PROTOCOLS} --user homevpn-bootstrap --no-ufw -y "
        ">/root/kit-topology-install.log 2>&1 || rc=$?\n"
        "chmod 600 /root/kit-topology-install.log\n"
        "if [ \"${rc:-0}\" -ne 0 ] && [ \"${rc:-0}\" -ne 124 ] && [ \"${rc:-0}\" -ne 130 ]; then exit \"$rc\"; fi\n"
    )
    pct_script(host, ctid, command, timeout=660)
    pct_script(host, ctid, r'''set -Eeuo pipefail
systemctl daemon-reload
systemctl restart kit-sub x-ui
/usr/local/bin/kit user del homevpn-bootstrap >/root/kit-bootstrap-cleanup.log 2>&1 || true
/usr/local/x-ui/x-ui setting -listenIP 0.0.0.0 >/root/xui-listen-change.log
systemctl restart x-ui
''')


def push_file(host, ctid, source, destination, mode):
    remote = f"/tmp/home-vpn-topology-{os.getpid()}-{source.name}"
    run(["scp", str(source), f"{host}:{remote}"])
    try:
        run(["ssh", host, "pct", "push", str(ctid), remote, destination])
        pct_script(host, ctid, f"chmod {mode:o} {destination}\n")
    finally:
        run(["ssh", host, "rm", "-f", "--", remote])


def install_backup(host, ctid):
    for name, destination, mode in (
        ("home-vpn-kit-backup", "/usr/local/sbin/home-vpn-kit-backup", 0o700),
        ("home-vpn-kit-backup.service", "/etc/systemd/system/home-vpn-kit-backup.service", 0o644),
        ("home-vpn-kit-backup.timer", "/etc/systemd/system/home-vpn-kit-backup.timer", 0o644),
    ):
        push_file(host, ctid, ROOT / "scripts" / name, destination, mode)
    pct_script(host, ctid, r'''set -Eeuo pipefail
systemctl daemon-reload
systemctl enable --now home-vpn-kit-backup.timer
systemctl start home-vpn-kit-backup.service
''')


def cascade_ports(items, emergency_remark):
    """Return public KIT ports that RU must forward to PL."""
    by_remark = {item["remark"]: int(item["port"]) for item in items}
    required = set(EXPECTED_REMARKS) | {emergency_remark}
    if not required.issubset(by_remark):
        missing = ", ".join(sorted(required - set(by_remark)))
        raise RuntimeError(f"Cannot configure cascade; missing PL inbounds: {missing}")
    # KIT multiplexes REALITY/XHTTP/WS/gRPC/VMess on nginx TCP/443.
    tcp = {443, by_remark["Shadowsocks"], by_remark[emergency_remark]}
    udp = {
        by_remark["Hysteria2"], by_remark["Shadowsocks"],
        by_remark["TUIC"], by_remark["AmneziaWG"],
        by_remark["AmneziaWG-3.1"],
    }
    return sorted(tcp), sorted(udp)


def render_cascade_nftables(pl_address, tcp_ports, udp_ports):
    tcp_set = "{ " + ", ".join(str(port) for port in tcp_ports) + " }"
    udp_set = "{ " + ", ".join(str(port) for port in udp_ports) + " }"
    tcp_rules = "\n".join(
        f"    tcp dport {port} dnat to {pl_address}:{port}" for port in tcp_ports
    )
    udp_rules = "\n".join(
        f"    udp dport {port} dnat to {pl_address}:{port}" for port in udp_ports
    )
    return f'''table ip home_vpn_cascade {{
  chain prerouting {{
    type nat hook prerouting priority dstnat; policy accept;
{tcp_rules}
{udp_rules}
  }}
  chain forward {{
    type filter hook forward priority filter; policy accept;
    ip daddr {pl_address} tcp dport {tcp_set} accept
    ip daddr {pl_address} udp dport {udp_set} accept
    ip saddr {pl_address} ct state established,related accept
  }}
  chain postrouting {{
    type nat hook postrouting priority srcnat; policy accept;
    ip daddr {pl_address} tcp dport {tcp_set} masquerade
    ip daddr {pl_address} udp dport {udp_set} masquerade
  }}
}}
'''


def set_pl_public_host(host, ctid, public_host):
    code = r'''import json, os, pathlib, re, sqlite3, sys, tempfile
new = sys.argv[1]
config_path = pathlib.Path("/etc/kit-sub/config.json")
config = json.loads(config_path.read_text(encoding="utf-8"))
old = str(config.get("link_host") or config.get("host") or "")
if not old:
    raise SystemExit("KIT public host is empty")
rx = re.compile(r"(?<![0-9.])" + re.escape(old) + r"(?![0-9])")
replace = lambda value: rx.sub(new, value) if isinstance(value, str) else value
database = sqlite3.connect("/etc/x-ui/x-ui.db")
database.create_function("home_vpn_replace", 1, replace)
for table, columns in (("inbounds", ("settings", "stream_settings")),
                       ("settings", ("value",)),
                       ("hosts", ("address", "sni", "host_header", "remark", "server_description"))):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        continue
    available = {row[1] for row in database.execute("PRAGMA table_info(%s)" % table)}
    for column in columns:
        if column in available:
            database.execute("UPDATE %s SET %s=home_vpn_replace(%s)" % (table, column, column))
database.commit(); database.close()
for path in (pathlib.Path("/etc/kit/kit.env"), config_path):
    if not path.exists():
        continue
    content = path.read_text(encoding="utf-8")
    updated = rx.sub(new, content)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(updated)
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)
print(old)
'''
    result = run(
        ["ssh", host, "pct", "exec", str(ctid), "--", "python3", "-", public_host],
        input_text=code, capture=True,
    )
    pct_script(host, ctid, "set -Eeuo pipefail\nsystemctl restart x-ui kit-sub\n")
    return result.stdout.strip().splitlines()[-1]


def configure_cascade(host, ru_ctid, pl_ctid, ru_address, pl_address, items, emergency_remark):
    tcp_ports, udp_ports = cascade_ports(items, emergency_remark)
    nftables = render_cascade_nftables(pl_address, tcp_ports, udp_ports)
    script = f'''set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null
apt-get install -y nftables >/dev/null
systemctl disable --now home-vpn-cascade.service 2>/dev/null || true
nft delete table ip home_vpn_cascade 2>/dev/null || true
cat > /etc/sysctl.d/90-home-vpn-cascade.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl -p /etc/sysctl.d/90-home-vpn-cascade.conf >/dev/null
cat > /etc/nftables.d-home-vpn-cascade.nft <<'EOF'
{nftables}EOF
nft delete table ip home_vpn_cascade 2>/dev/null || true
nft -f /etc/nftables.d-home-vpn-cascade.nft
cat > /etc/systemd/system/home-vpn-cascade.service <<'EOF'
[Unit]
Description=HOME-VPN RU to PL cascade port forwarding
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d-home-vpn-cascade.nft
ExecStop=-/usr/sbin/nft delete table ip home_vpn_cascade

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now home-vpn-cascade.service
'''
    pct_script(host, ru_ctid, script, timeout=300)
    set_pl_public_host(host, pl_ctid, ru_address)


def configure_direct(host, ru_ctid, pl_ctid, pl_address):
    pct_script(host, ru_ctid, r'''set -Eeuo pipefail
systemctl disable --now home-vpn-cascade.service 2>/dev/null || true
nft delete table ip home_vpn_cascade 2>/dev/null || true
rm -f -- /etc/systemd/system/home-vpn-cascade.service /etc/nftables.d-home-vpn-cascade.nft /etc/sysctl.d/90-home-vpn-cascade.conf
systemctl daemon-reload
''')
    set_pl_public_host(host, pl_ctid, pl_address)


def configure_connection_mode(host, mode, ru_ctid, pl_ctid, ru_address, pl_address, items, emergency_remark):
    if mode == "cascade":
        configure_cascade(host, ru_ctid, pl_ctid, ru_address, pl_address, items, emergency_remark)
    else:
        configure_direct(host, ru_ctid, pl_ctid, pl_address)


def validate_connection_mode(host, mode, ru_ctid, pl_ctid, ru_address, pl_address):
    expected_host = ru_address if mode == "cascade" else pl_address
    code = r'''import json, pathlib, sys
config = json.loads(pathlib.Path("/etc/kit-sub/config.json").read_text(encoding="utf-8"))
actual = str(config.get("link_host") or config.get("host") or "")
if actual != sys.argv[1]:
    raise SystemExit("KIT public host mismatch: expected %s, got %s" % (sys.argv[1], actual))
'''
    run(
        ["ssh", host, "pct", "exec", str(pl_ctid), "--", "python3", "-", expected_host],
        input_text=code,
    )
    if mode == "cascade":
        pct_script(
            host, ru_ctid,
            "set -Eeuo pipefail\nsystemctl is-active --quiet home-vpn-cascade.service\nnft list table ip home_vpn_cascade >/dev/null\n",
        )
    else:
        pct_script(
            host, ru_ctid,
            "set -Eeuo pipefail\nif nft list table ip home_vpn_cascade >/dev/null 2>&1; then exit 1; fi\n",
        )


def pl_inventory(host, ctid, emergency_remark):
    code = r'''import json, sqlite3, sys
database = sqlite3.connect("/etc/x-ui/x-ui.db")
database.row_factory = sqlite3.Row
rows = database.execute("SELECT id, remark, protocol, port, enable FROM inbounds ORDER BY id").fetchall()
integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
database.close()
print(json.dumps({"integrity": integrity, "items": [dict(row) for row in rows], "emergency": sys.argv[1]}, ensure_ascii=False))
'''
    result = run(
        ["ssh", host, "pct", "exec", str(ctid), "--", "python3", "-", emergency_remark],
        input_text=code, capture=True,
    )
    return json.loads(result.stdout)


def update_site(host, ctid, pl_ids, emergency_id):
    code = r'''import json, os, pathlib, sys, tempfile
payload = json.loads(sys.argv[1])
config_path = pathlib.Path("/etc/home-vpn/config.json")
data = json.loads(config_path.read_text(encoding="utf-8").removesuffix("\\n"))
metadata = {
 "Emergency MTProto": ("mtproto", "tcp"), "REALITY": ("vless", "tcp"),
 "Hysteria2": ("hysteria2", "udp"), "XHTTP": ("vless", "xhttp"),
 "VLESS-WS": ("vless", "ws"), "Trojan-gRPC": ("trojan", "grpc"),
 "VMess-WS": ("vmess", "ws"), "Shadowsocks": ("shadowsocks", "tcp+udp"),
 "TUIC": ("tuic", "udp"), "AmneziaWG": ("amneziawg", "udp"),
 "AmneziaWG-3.1": ("amneziawg", "udp"),
}
for node in data.get("nodes", []):
    if node.get("id") == "ru-main":
        node.update(name="RU — управляющая панель", inbound_ids=[], routes=[], primary=False, accept_new=False)
    elif node.get("id") == "pl-node":
        node.update(name="PL — основная VPN-нода", inbound_ids=payload["ids"], primary=True, enabled=True, accept_new=True)
        node["routes"] = [{"name": item["remark"], "inbound_id": item["id"], "protocol": metadata[item["remark"]][0], "transport": metadata[item["remark"]][1], "enabled": True} for item in payload["items"]]
fd, temporary = tempfile.mkstemp(prefix="config.", dir=config_path.parent, text=True)
with os.fdopen(fd, "w", encoding="utf-8") as output:
    json.dump(data, output, ensure_ascii=False, indent=2); output.write("\n")
os.chmod(temporary, 0o660); os.replace(temporary, config_path)
env_path = pathlib.Path("/etc/vpn-shop.env")
lines = env_path.read_text(encoding="utf-8").splitlines()
key = "MTPROTO_INBOUND_ID="
lines = [key + str(payload["emergency_id"]) if line.startswith(key) else line for line in lines]
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
'''
    inventory = [{"id": item["id"], "remark": item["remark"]} for item in pl_ids]
    arguments = json.dumps({"ids": [x["id"] for x in pl_ids], "items": inventory, "emergency_id": emergency_id}, ensure_ascii=False)
    run(["ssh", host, "pct", "exec", str(ctid), "--", "python3", "-", arguments], input_text=code)
    pct_script(host, ctid, r'''set -Eeuo pipefail
systemctl restart vpn-shop
for attempt in 1 2 3 4 5; do curl -ksSf https://127.0.0.1:8443/health >/dev/null && exit 0; sleep 1; done
exit 1
''')


def validate(host, ru_ctid, pl_ctid, emergency_remark):
    ru_code = r'''import json, sqlite3
database = sqlite3.connect("/etc/x-ui/x-ui.db")
print(json.dumps({
 "integrity": database.execute("PRAGMA integrity_check").fetchone()[0],
 "local": database.execute("SELECT count(*) FROM inbounds WHERE COALESCE(node_id,0)=0").fetchone()[0],
 "remote": database.execute("SELECT count(*) FROM inbounds WHERE COALESCE(node_id,0)!=0").fetchone()[0],
}))
database.close()
'''
    ru = json.loads(pct_python(host, ru_ctid, ru_code, capture=True).stdout)
    pl = pl_inventory(host, pl_ctid, emergency_remark)
    remarks = {item["remark"] for item in pl["items"]}
    expected = set(EXPECTED_REMARKS) | {emergency_remark}
    if ru["integrity"] != "ok" or ru["local"] != 0:
        raise RuntimeError(f"RU validation failed: {ru}")
    if pl["integrity"] != "ok" or len(pl["items"]) != 11 or remarks != expected:
        raise RuntimeError("PL must contain exactly ten KIT protocols and Emergency MTProto")
    pct_script(host, ru_ctid, "systemctl is-active --quiet x-ui vpn-shop home-vpn-kit-backup.timer\n")
    pct_script(host, pl_ctid, "systemctl is-active --quiet x-ui nginx kit-sub vpn-bot vpn-bot-admin home-vpn-kit-backup.timer\n")
    return ru, pl


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxmox-host", default="proxmox")
    parser.add_argument("--ru-ctid", type=int, default=112)
    parser.add_argument("--pl-ctid", type=int, default=113)
    parser.add_argument("--ru-address", default="192.168.10.231")
    parser.add_argument("--pl-address", default="192.168.10.214")
    parser.add_argument("--pl-sni", default="dl.google.com")
    parser.add_argument("--pl-cert", default="/etc/x-ui/cert/panel.crt")
    parser.add_argument("--pl-key", default="/etc/x-ui/cert/panel.key")
    parser.add_argument("--emergency-remark", default="🆘 Emergency MTProto")
    parser.add_argument("--connection-mode", choices=("direct", "cascade"), default="direct")
    parser.add_argument(
        "--mode-only", action="store_true",
        help="switch direct/cascade mode without rebuilding 3x-ui inbounds",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    if arguments.ru_ctid == arguments.pl_ctid:
        raise SystemExit("RU and PL CTID must be different")
    try:
        ipaddress.ip_address(arguments.ru_address)
        ipaddress.ip_address(arguments.pl_address)
    except ValueError as error:
        raise SystemExit(f"Invalid LXC address: {error}") from error
    operation = "mode switch" if arguments.mode_only else "full topology rebuild"
    print(
        f"Plan: {operation}; mode={arguments.connection_mode}; "
        f"RU=LXC {arguments.ru_ctid} ({arguments.ru_address}); "
        f"PL=LXC {arguments.pl_ctid} ({arguments.pl_address})"
    )
    expected_confirmation = "CONFIGURE CONNECTION MODE" if arguments.mode_only else "REBUILD TEST LXC"
    if not arguments.apply or arguments.confirm != expected_confirmation:
        raise SystemExit(
            "Dry-run only. To apply use --apply --confirm " + repr(expected_confirmation) + "."
        )

    if arguments.mode_only:
        inventory = pl_inventory(
            arguments.proxmox_host, arguments.pl_ctid, arguments.emergency_remark,
        )
        configure_connection_mode(
            arguments.proxmox_host, arguments.connection_mode,
            arguments.ru_ctid, arguments.pl_ctid, arguments.ru_address,
            arguments.pl_address, inventory["items"], arguments.emergency_remark,
        )
        validate_connection_mode(
            arguments.proxmox_host, arguments.connection_mode,
            arguments.ru_ctid, arguments.pl_ctid,
            arguments.ru_address, arguments.pl_address,
        )
        print(f"Ready: connection mode={arguments.connection_mode}; 3x-ui inbounds were not changed")
        return

    print("Creating rollback copies...")
    print("RU:", create_rollback(arguments.proxmox_host, arguments.ru_ctid))
    print("PL:", create_rollback(arguments.proxmox_host, arguments.pl_ctid))
    with tempfile.TemporaryDirectory(prefix="home-vpn-kit-") as temporary:
        installer = pathlib.Path(temporary) / "3x-ui-kit-v1.2.sh"
        download_verified_installer(installer)
        push_file(arguments.proxmox_host, arguments.pl_ctid, installer, "/root/3x-ui-kit-v1.2.sh", 0o700)
    emergency = clean_pl_except_emergency(arguments.proxmox_host, arguments.pl_ctid, arguments.emergency_remark)
    clean_ru(arguments.proxmox_host, arguments.ru_ctid)
    prepare_lxc_override(arguments.proxmox_host, arguments.pl_ctid)
    public_address = arguments.ru_address if arguments.connection_mode == "cascade" else arguments.pl_address
    install_kit(arguments.proxmox_host, arguments.pl_ctid, public_address, arguments.pl_sni, arguments.pl_cert, arguments.pl_key)
    install_backup(arguments.proxmox_host, arguments.ru_ctid)
    install_backup(arguments.proxmox_host, arguments.pl_ctid)
    inventory = pl_inventory(arguments.proxmox_host, arguments.pl_ctid, arguments.emergency_remark)
    items = inventory["items"]
    emergency_item = next(item for item in items if item["remark"] == arguments.emergency_remark)
    update_site(arguments.proxmox_host, arguments.ru_ctid, items, emergency_item["id"])
    configure_connection_mode(
        arguments.proxmox_host, arguments.connection_mode,
        arguments.ru_ctid, arguments.pl_ctid, arguments.ru_address,
        arguments.pl_address, items, arguments.emergency_remark,
    )
    validate_connection_mode(
        arguments.proxmox_host, arguments.connection_mode,
        arguments.ru_ctid, arguments.pl_ctid,
        arguments.ru_address, arguments.pl_address,
    )
    ru, pl = validate(arguments.proxmox_host, arguments.ru_ctid, arguments.pl_ctid, arguments.emergency_remark)
    print(f"Ready: mode={arguments.connection_mode}, RU local={ru['local']}, PL inbounds={len(pl['items'])}, Emergency ID={emergency_item['id']}")


if __name__ == "__main__":
    main()
