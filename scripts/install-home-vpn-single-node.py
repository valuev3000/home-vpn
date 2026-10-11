#!/usr/bin/env python3
"""Interactive end-to-end installer run directly on a fresh HOME-VPN VPS."""

import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOPOLOGY_INSTALLER = ROOT / "scripts/install-home-vpn-topology.py"
SMOKE_TEST = ROOT / "scripts/smoke-home-vpn-topology.py"
DEFAULT_PLAN = pathlib.Path("/root/home-vpn-topology.json")
DEFAULT_KIT_URL = "https://raw.githubusercontent.com/itsnotkubrick/3X-UI_KIT/v1.2/scripts/3x-ui.sh"
DEFAULT_KIT_SHA256 = "2d7a0f0dcc26ea4f8bab3d60e1900e7046a9673a88fef85c03630502120961f0"
APPLY_CONFIRMATION = "INSTALL HOME VPN"
BACKUP_DIR = pathlib.Path("/var/backups/home-vpn")
BACKUP_GLOB = "home-vpn-full-*.tar.gz"
MANAGED_SERVICES = (
    "x-ui", "kit-sub", "nginx", "vpn-shop", "vpn-bot", "vpn-bot-admin",
    "home-vpn-bot-egress",
)


PREPARE_SERVER = r'''set -Eeuo pipefail
site_domain=$1
panel_domain=$2
ssh_port=$3
xui_mode=$4
export DEBIAN_FRONTEND=noninteractive

. /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:22.04|ubuntu:24.04) ;;
    *) echo 'Поддерживаются только Ubuntu 22.04 и Ubuntu 24.04' >&2; exit 1 ;;
esac

apt-get update
apt-get install -y ca-certificates certbot curl fail2ban openssl ufw unattended-upgrades
systemctl enable --now fail2ban unattended-upgrades

if [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 1500000 ] && ! swapon --show=NAME --noheadings | grep -q .; then
    if ! fallocate -l 1G /swapfile; then
        dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
    fi
    chmod 0600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if [ "$xui_mode" = fresh ]; then
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow "${ssh_port}/tcp"
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw allow 443/udp
    ufw allow 8388/tcp
    ufw allow 8388/udp
    ufw allow 8444/udp
    ufw allow 51821/udp
    ufw allow 51822/udp
    ufw --force enable
else
    echo 'Существующие правила UFW сохранены без изменений.'
fi

cert_dir="/etc/letsencrypt/live/$site_domain"
certificate_ok=false
if [ -s "$cert_dir/fullchain.pem" ] && [ -s "$cert_dir/privkey.pem" ]; then
    if openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkend 604800 >/dev/null 2>&1 && \
       openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkhost "$site_domain" >/dev/null 2>&1 && \
       { [ "$xui_mode" = preserve ] || openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkhost "$panel_domain" >/dev/null 2>&1; }; then
        certificate_ok=true
    fi
fi

if [ "$certificate_ok" != true ]; then
    nginx_was_active=false
    if systemctl is-active --quiet nginx; then
        nginx_was_active=true
        systemctl stop nginx
    fi
    restore_nginx() {
        if [ "$nginx_was_active" = true ]; then systemctl start nginx; fi
    }
    trap restore_nginx EXIT
    certbot certonly --standalone --non-interactive --agree-tos --no-eff-email --expand \
        --cert-name "$site_domain" --register-unsafely-without-email \
        -d "$site_domain" -d "$panel_domain"
    restore_nginx
    trap - EXIT
fi

test -s "$cert_dir/fullchain.pem"
test -s "$cert_dir/privkey.pem"
'''


PREINSTALL_BACKUP = r'''set -Eeuo pipefail
umask 077
install -d -m 0700 /var/backups/home-vpn
stage=$(mktemp -d /var/backups/home-vpn/.full-backup.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
for item in etc/x-ui etc/nginx etc/letsencrypt etc/kit usr/local/x-ui \
    etc/home-vpn etc/vpn-shop.env etc/vpn-bot.env etc/vpn-bot-admin.env \
    etc/vpn-bot-menu.json etc/home-vpn-mail etc/sudoers.d/vpn-bot-admin \
    etc/cron.d/x-ui etc/cron.d/3x-ui-pro \
    etc/ufw/user.rules etc/ufw/user6.rules etc/default/ufw \
    var/lib/vpn-shop var/lib/vpn-bot srv/home-vpn \
    root/home-vpn-topology.json root/home-vpn-mail-credentials.txt; do
    case "$item" in etc/x-ui|var/lib/vpn-shop) continue ;; esac
    [ ! -e "/$item" ] || tar -C / -cf - "$item" | tar -C "$stage" -xf -
done
if [ -d /etc/x-ui ]; then
    tar -C / --exclude=etc/x-ui/x-ui.db --exclude=etc/x-ui/x-ui.db-wal --exclude=etc/x-ui/x-ui.db-shm \
        -cf - etc/x-ui | tar -C "$stage" -xf -
fi
if [ -d /var/lib/vpn-shop ]; then
    tar -C / --exclude=var/lib/vpn-shop/shop.db --exclude=var/lib/vpn-shop/shop.db-wal --exclude=var/lib/vpn-shop/shop.db-shm \
        -cf - var/lib/vpn-shop | tar -C "$stage" -xf -
fi
if [ -s /var/lib/vpn-shop/shop.db ]; then
  mkdir -p "$stage/var/lib/vpn-shop"
  python3 - "$stage/var/lib/vpn-shop/shop.db" <<'PY'
import sqlite3,sys
source=sqlite3.connect('file:/var/lib/vpn-shop/shop.db?mode=ro',uri=True)
target=sqlite3.connect(sys.argv[1]);source.backup(target);target.close();source.close()
check=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
result=check.execute('PRAGMA integrity_check').fetchone()[0];check.close()
if result!='ok':raise SystemExit('vpn-shop SQLite integrity_check: '+str(result))
PY
fi
for item in /etc/systemd/system/x-ui.service /etc/systemd/system/kit-sub.service \
    /etc/systemd/system/vpn-shop.service \
    /etc/systemd/system/vpn-bot.service /etc/systemd/system/vpn-bot-admin.service \
    /etc/systemd/system/home-vpn-*.service /etc/systemd/system/home-vpn-*.timer; do
    [ ! -e "$item" ] || tar -C / -cf - "${item#/}" | tar -C "$stage" -xf -
done
if [ -s /etc/x-ui/x-ui.db ]; then
  mkdir -p "$stage/etc/x-ui"
  python3 - "$stage/etc/x-ui/x-ui.db" <<'PY'
import pathlib,sqlite3,sys
path=pathlib.Path('/etc/x-ui/x-ui.db')
source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
target=sqlite3.connect(sys.argv[1]);source.backup(target);target.close();source.close()
check=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
result=check.execute('PRAGMA integrity_check').fetchone()[0];check.close()
if result!='ok':raise SystemExit('3x-ui SQLite integrity_check: '+str(result))
PY
fi
python3 - "$stage/manifest.json" <<'PY'
import json,pathlib,sys,time
root=pathlib.Path(sys.argv[1]).parent
paths=sorted(str(path.relative_to(root)) for path in root.rglob('*') if path.name!='manifest.json')
pathlib.Path(sys.argv[1]).write_text(json.dumps({'format':1,'type':'home-vpn-full','created_at':int(time.time()),'paths':paths},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
PY
stamp=$(date -u +%Y%m%d-%H%M%S)
archive="/var/backups/home-vpn/home-vpn-full-${stamp}.tar.gz"
tar -czf "$archive" -C "$stage" .
chmod 0600 "$archive"
tar -tzf "$archive" >/dev/null
(cd "$(dirname "$archive")" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256")
chmod 0600 "${archive}.sha256"
(cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive").sha256" >/dev/null)
printf 'Проверенный полный архив: %s\n' "$archive"
'''


RESET_EXISTING_STACK = r'''set -Eeuo pipefail
systemctl disable --now x-ui nginx kit-sub vpn-shop vpn-bot vpn-bot-admin home-vpn-bot-egress 2>/dev/null || true
rm -rf -- /etc/x-ui /usr/local/x-ui /etc/nginx /etc/kit /opt/vpn-shop /opt/vpn-bot /etc/home-vpn /var/lib/vpn-shop /var/lib/vpn-bot
rm -f -- /usr/local/bin/kit /usr/local/bin/x-ui /usr/bin/x-ui /etc/vpn-shop.env /etc/vpn-bot.env /etc/vpn-bot-admin.env
rm -f -- /etc/systemd/system/x-ui.service /etc/systemd/system/kit-sub.service /etc/systemd/system/vpn-shop.service /etc/systemd/system/vpn-bot.service /etc/systemd/system/vpn-bot-admin.service /etc/systemd/system/home-vpn-bot-egress.service
rm -f -- /etc/systemd/system/home-vpn-*.service /etc/systemd/system/home-vpn-*.timer
rm -f -- /etc/cron.d/x-ui /etc/cron.d/3x-ui-pro
apt-get purge -y nginx nginx-common nginx-core nginx-full 2>/dev/null || true
systemctl daemon-reload
'''


INSTALL_RENEW_HOOK = r'''set -Eeuo pipefail
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
temporary=$(mktemp /etc/letsencrypt/renewal-hooks/deploy/.home-vpn-services.XXXXXX)
cat > "$temporary" <<'EOF'
#!/bin/sh
systemctl reload nginx
systemctl restart x-ui
EOF
chmod 0755 "$temporary"
mv -f -- "$temporary" /etc/letsencrypt/renewal-hooks/deploy/home-vpn-services
'''


def run(arguments, *, input_text=None, capture=False, timeout=None, check=True):
    return subprocess.run(
        [str(item) for item in arguments], input=input_text, text=True,
        capture_output=capture, timeout=timeout, check=check,
    )


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def ask_validated(prompt, default, validator):
    while True:
        try:return validator(ask(prompt, default))
        except (SystemExit, ValueError) as error:print(error)


def validate_or_ask(value, prompt, validator):
    if value:
        try:return validator(value)
        except (SystemExit,ValueError) as error:print(error)
    return ask_validated(prompt,"",validator)


def ask_yes_no(prompt, default=True):
    marker = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{marker}]: ").strip().lower()
        if not value:return default
        if value in ("y", "yes"):return True
        if value in ("n", "no"):return False
        print("Enter y or n")


def require_confirmation(prompt, expected):
    while True:
        value=input(f"{prompt}\nВведите {expected!r} или 0 для отмены: ").strip()
        if value==expected:return
        if value=="0":raise SystemExit("Операция отменена")
        print(f"Ответ не совпадает. Введите точно {expected!r} или 0 для отмены.")


def validate_domain(value, label):
    value = str(value or "").strip().rstrip(".").lower()
    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
        value,
    ):
        raise SystemExit(f"{label}: нужно полное DNS-имя, получено {value!r}")
    return value


def validate_country(value):
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,3}", value):
        raise SystemExit(
            "Код страны должен состоять из двух или трёх латинских букв, "
            "например EE или EST"
        )
    return value


def ask_country(prompt="Код страны ноды", default="DE"):
    while True:
        try:
            return validate_country(ask(prompt, default))
        except SystemExit as error:
            print(error)


def validate_https_url(value):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SystemExit("URL установщика должен быть полным HTTPS-адресом без credentials")
    return value


def validate_sha256(value):
    value=str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}",value):
        raise SystemExit("SHA-256 должен содержать ровно 64 hex-символа")
    return value


def check_dns(domain, expected_ip):
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(
                domain, 443, family=socket.AF_INET, type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise SystemExit(f"DNS {domain} не разрешается: {error}") from error
    if expected_ip not in addresses:
        found = ", ".join(sorted(addresses)) or "нет A-записей"
        raise SystemExit(f"DNS {domain} указывает на {found}, ожидался {expected_ip}")


def resolved_ipv4(domain):
    try:
        return {
            item[4][0] for item in socket.getaddrinfo(
                domain, 443, family=socket.AF_INET, type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise SystemExit(f"DNS {domain} не разрешается: {error}") from error


def detect_public_ipv4(site_domain, panel_domain):
    shared = resolved_ipv4(site_domain) & resolved_ipv4(panel_domain)
    if not shared:
        raise SystemExit("Домены сайта и панели должны иметь общую IPv4 A-запись этой VPS")
    address = sorted(shared, key=lambda value: tuple(map(int, value.split("."))))[0]
    return str(ipaddress.IPv4Address(address))


def detected_ssh_port():
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) == 4 and connection[3].isdigit() and 1 <= int(connection[3]) <= 65535:
        return connection[3]
    return "22"


def existing_xui_detected():
    return pathlib.Path("/etc/x-ui/x-ui.db").is_file() and (
        pathlib.Path("/usr/local/x-ui/x-ui").is_file()
        or run(["systemctl","is-active","--quiet","x-ui"],check=False).returncode==0
    )


def choose_xui_mode():
    if not existing_xui_detected():
        print("3x-ui не обнаружена: будет выполнена чистая установка.")
        return "fresh"
    print("\nОбнаружена существующая 3x-ui.")
    print("  1 — сохранить панель, inbounds, клиентов, ссылки и текущие правила firewall")
    print("  2 — чистая переустановка VPN-стека (удалит 3x-ui, клиентов и HOME-VPN после backup)")
    while True:
        choice=ask("Выберите вариант", "1")
        if choice=="1":return "preserve"
        if choice=="2":return "fresh"
        print("Допустимы варианты 1 или 2")


def nginx_vhosts(nginx_root=pathlib.Path("/etc/nginx")):
    result = {}
    enabled = nginx_root / "sites-enabled"
    files = enabled.glob("*") if enabled.is_dir() else nginx_root.glob("**/*.conf")
    for item in files:
        if not item.is_file():
            continue
        try:
            text = item.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for domain in re.findall(r"\bserver_name\s+([^;]+);", text):
            for name in domain.split():
                try:
                    normalized = validate_domain(name, "server_name")
                except SystemExit:
                    continue
                result.setdefault(normalized, (item.resolve(), text))
    return result


def detect_existing_domains(database=pathlib.Path("/etc/x-ui/x-ui.db"),
                            nginx_root=pathlib.Path("/etc/nginx")):
    import sqlite3

    vhosts = nginx_vhosts(nginx_root)
    if len(vhosts) < 2:
        raise RuntimeError("Для автоопределения нужны два активных Nginx vhost")
    connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
    try:
        settings = dict(connection.execute("SELECT key,value FROM settings"))
    finally:
        connection.close()
    candidates = []
    for key, value in settings.items():
        if "cert" in str(key).lower() or "domain" in str(key).lower() or "host" in str(key).lower():
            candidates.extend(re.findall(r"/live/([^/]+)/", str(value)))
            candidates.extend(re.findall(r"(?:^|https?://)([A-Za-z0-9.-]+)(?:[:/]|$)", str(value)))
    panel_scores = {}
    base_path = str(settings.get("webBasePath") or "").strip("/")
    web_port = str(settings.get("webPort") or "").strip()
    for domain, (_, text) in vhosts.items():
        score = 0
        if domain in candidates:score += 20
        if "panel" in domain.split(".")[0]:score += 8
        if base_path and base_path in text:score += 4
        if web_port and re.search(r"127\.0\.0\.1:" + re.escape(web_port) + r"\b", text):score += 3
        panel_scores[domain] = score
    ordered = sorted(panel_scores, key=lambda domain: (-panel_scores[domain], domain))
    if len(ordered) < 2 or panel_scores[ordered[0]] == panel_scores[ordered[1]]:
        raise RuntimeError("Не удалось однозначно определить домен панели 3x-ui")
    panel_domain = ordered[0]
    site_candidates = [domain for domain in vhosts if domain != panel_domain]
    if len(site_candidates) != 1:
        rooted = [domain for domain in site_candidates if re.search(r"\broot\s+[^;]+;", vhosts[domain][1])]
        if len(rooted) != 1:
            raise RuntimeError("Не удалось однозначно определить домен сайта-заглушки")
        site_candidates = rooted
    return site_candidates[0], panel_domain


def find_preserved_vhost(domain, nginx_root=pathlib.Path("/etc/nginx")):
    candidates = (
        nginx_root / "sites-enabled" / domain,
        nginx_root / "sites-available" / domain,
        nginx_root / "conf.d" / f"{domain}.conf",
    )
    source = next((item.resolve() for item in candidates if item.exists()), None)
    if source is None and nginx_root.is_dir():
        pattern = re.compile(r"\bserver_name\s+" + re.escape(domain) + r"(?:\s|;)")
        for item in nginx_root.glob("**/*"):
            if not item.is_file():
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if pattern.search(text):
                source = item.resolve()
                break
    if source is None:
        raise RuntimeError(f"Не найден nginx vhost домена {domain}")
    return source


def preserve_preflight(site_domain, bot_enabled, nginx_root=pathlib.Path("/etc/nginx"),
                       database=pathlib.Path("/etc/x-ui/x-ui.db")):
    """Read-only compatibility check before touching an existing installation."""
    import sqlite3

    connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("3x-ui SQLite integrity_check: " + str(integrity))
        rows = connection.execute(
            "SELECT protocol,enable,stream_settings FROM inbounds"
        ).fetchall()
        settings = dict(connection.execute("SELECT key,value FROM settings"))
    finally:
        connection.close()
    if not any(bool(row[1]) for row in rows):
        raise RuntimeError("В существующей 3x-ui нет активных inbounds")
    if not str(settings.get("webBasePath") or "").strip("/"):
        raise RuntimeError("В существующей 3x-ui не задан webBasePath")
    if bot_enabled and not any(
        bool(enabled) and str(protocol).lower() == "vless"
        and json.loads(stream or "{}").get("network") == "xhttp"
        for protocol, enabled, stream in rows
    ):
        raise RuntimeError("Для Telegram-бота нужен активный VLESS/XHTTP inbound")
    vhost = find_preserved_vhost(site_domain, nginx_root)
    text = vhost.read_text(encoding="utf-8")
    marker = "include /etc/nginx/snippets/home-vpn-site.conf;"
    if marker not in text:
        if len(re.findall(r"\bserver\s*\{", text)) != 1:
            raise RuntimeError("Vhost содержит несколько server-блоков; автоматическое изменение запрещено")
        if re.search(r"\blocation\s+(?:\^~\s+|=\s+)?/\s*\{", text):
            raise RuntimeError("Vhost уже содержит location /; автоматическое изменение запрещено")
        included_roots = []
        for include in re.findall(r"\binclude\s+([^;*?\[\]]+)\s*;", text):
            included = pathlib.Path(include)
            if not included.is_file():
                continue
            included_text = included.read_text(encoding="utf-8")
            if re.search(r"\blocation\s+(?:\^~\s+|=\s+)?/\s*\{", included_text):
                included_roots.append(included)
        if len(included_roots) > 1:
            raise RuntimeError("Vhost получает location / из нескольких include; изменение запрещено")
    return {"inbounds": len(rows), "vhost": str(vhost)}


def build_topology(*, public_ip, site_domain, panel_domain, country, node_name,
                   sni, bot_enabled, installer_url, installer_sha256,
                   xui_install_mode="fresh", **_ignored):
    cert_dir = f"/etc/letsencrypt/live/{site_domain}"
    return {
        "mode": 3,
        "xui_install_mode": xui_install_mode,
        "protocol_installer": {
            "url": validate_https_url(installer_url),
            "sha256": installer_sha256,
        },
        "telegram_bot": {"enabled": bool(bot_enabled)},
        "telegram_egress": {
            "listen": "127.0.0.1", "port": 18080,
            "strategy": "leastPing", "probe_interval": "30s",
        },
        "site": {
            "ssh_host": "local", "address": public_ip,
            "public_url": f"https://{site_domain}",
            "tls_cert": cert_dir + "/fullchain.pem",
            "tls_key": cert_dir + "/privkey.pem",
        },
        "nodes": [{
            "id": "foreign-1", "name": node_name, "country": country,
            "flag": "🌐", "ssh_host": "local", "address": public_ip,
            "api_host": panel_domain, "public_host": panel_domain,
            "ingress_address": "", "sni": sni,
            "cert": cert_dir + "/fullchain.pem",
            "key": cert_dir + "/privkey.pem",
        }],
        "emergency_node": "foreign-1",
    }


def collect_answers(site_domain_override="", panel_domain_override=""):
    print("\nHOME-VPN — локальная установка одной VPS\n")
    xui_install_mode=choose_xui_mode()
    if not site_domain_override or not panel_domain_override:
        if existing_xui_detected():
            try:
                detected_site, detected_panel = detect_existing_domains()
                site_domain_override = site_domain_override or detected_site
                panel_domain_override = panel_domain_override or detected_panel
            except RuntimeError as error:
                print(f"Автоопределение доменов не удалось: {error}")
    panel_domain = validate_or_ask(
        panel_domain_override, "Укажите домен 3x-ui",
        lambda value:validate_domain(value,"Домен панели"),
    )
    site_domain = validate_or_ask(
        site_domain_override, "Укажите домен сайта-заглушки",
        lambda value:validate_domain(value,"Домен сайта"),
    )
    while site_domain == panel_domain:
        print("Для сайта и 3x-ui нужны два разных домена")
        site_domain=ask_validated(
            "Укажите другой домен сайта-заглушки", "",
            lambda value:validate_domain(value,"Домен сайта"),
        )
    print(f"Используются домены: сайт {site_domain}, 3x-ui {panel_domain}")
    while True:
        try:
            public_ip=detect_public_ipv4(site_domain,panel_domain)
            break
        except SystemExit as error:
            print(error)
            panel_domain=ask_validated(
                "Повторно укажите домен 3x-ui",panel_domain,
                lambda value:validate_domain(value,"Домен панели"),
            )
            site_domain=ask_validated(
                "Повторно укажите домен сайта-заглушки",site_domain,
                lambda value:validate_domain(value,"Домен сайта"),
            )
            while site_domain==panel_domain:
                print("Для сайта и 3x-ui нужны два разных домена")
                site_domain=ask_validated(
                    "Укажите другой домен сайта-заглушки","",
                    lambda value:validate_domain(value,"Домен сайта"),
                )
    ssh_port = detected_ssh_port()
    print(f"Определено автоматически: IPv4 {public_ip}, SSH-порт {ssh_port}")
    country = ask_country()
    node_name = ask("Название VPN-ноды", "Main VPN node")
    sni = ask_validated("SNI маскировки", "dl.google.com", lambda value:validate_domain(value,"SNI"))
    bot_enabled = ask_yes_no("Установить Telegram-бота?", True)
    installer_url = ask_validated("HTTPS URL установщика 3x-ui/KIT",DEFAULT_KIT_URL,validate_https_url) if xui_install_mode=="fresh" else DEFAULT_KIT_URL
    installer_sha256 = ask_validated("SHA-256 установщика",DEFAULT_KIT_SHA256,validate_sha256) if xui_install_mode=="fresh" else DEFAULT_KIT_SHA256
    return {
        "public_ip": public_ip, "ssh_port": ssh_port,"xui_install_mode":xui_install_mode,
        "site_domain": site_domain, "panel_domain": panel_domain,
        "country": country, "node_name": node_name,
        "sni": sni, "bot_enabled": bot_enabled,
        "installer_url": installer_url,
        "installer_sha256": installer_sha256,
    }


def save_plan(plan, path):
    if path.exists() and not ask_yes_no(f"Файл {path} уже существует. Перезаписать?", False):
        raise SystemExit("Установка отменена: план не изменён")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def validate_plan(path):
    result = run(
        [sys.executable, TOPOLOGY_INSTALLER, "--config", path],
        capture=True, timeout=60, check=False,
    )
    if result.returncode != 1 or "Dry-run:" not in result.stderr + result.stdout:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "План не прошёл проверку")
    print(result.stdout, end="")


def read_env(path):
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("'\"")
    return values


def existing_panel_details(plan_path):
    try:
        plan=json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
        domain=plan["nodes"][0]["api_host"]
        connection=__import__("sqlite3").connect("file:/etc/x-ui/x-ui.db?mode=ro",uri=True)
        path=connection.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()[0].strip("/")
        user=connection.execute("SELECT username FROM users ORDER BY id LIMIT 1").fetchone()[0]
        connection.close()
        return "https://"+domain+"/"+path,user
    except Exception:
        return "не найден","сохранён прежний"


def show_results(plan_path=DEFAULT_PLAN):
    site = read_env(pathlib.Path("/etc/vpn-shop.env"))
    bot = read_env(pathlib.Path("/etc/vpn-bot-admin.env"))
    xui = read_env(pathlib.Path("/etc/x-ui/install-result.env"))
    public_url = site.get("PUBLIC_URL", "").rstrip("/")
    admin_path = site.get("ADMIN_ENTRY_PATH", "")
    bot_path = bot.get("BOT_PANEL_ENTRY_PATH", "")
    print("\n" + "=" * 64)
    print("HOME-VPN — данные доступа")
    print("=" * 64)
    if public_url:
        print("Сайт:             ", public_url)
        print("Админка сайта:    ", public_url + admin_path)
    print("Логин сайта:      ", site.get("ADMIN_USER", "не найден"))
    print("Пароль сайта:     ", site.get("ADMIN_PASSWORD", "не найден"))
    if bot:
        print("Панель бота:      ", public_url + bot_path)
        print("Логин панели бота:", bot.get("BOT_PANEL_USER", "не найден"))
        print("Пароль панели:    ", bot.get("BOT_PANEL_PASSWORD", "не найден"))
    preserved_url,preserved_user=existing_panel_details(plan_path)
    print("Панель 3x-ui:     ", xui.get("XUI_ACCESS_URL") or preserved_url)
    print("Логин 3x-ui:      ", xui.get("XUI_USERNAME") or preserved_user)
    print("Пароль 3x-ui:     ", xui.get("XUI_PASSWORD") or "сохранён прежний (хеш нельзя восстановить)")
    print("Локальный план:   ", plan_path)
    print("=" * 64)
    print("Сохраните эти данные в менеджере паролей и очистите scrollback терминала.")


def installation_markers():
    markers = {
        "конфигурация": pathlib.Path("/etc/home-vpn/config.json"),
        "сайт": pathlib.Path("/etc/vpn-shop.env"),
        "Telegram-бот": pathlib.Path("/etc/vpn-bot.env"),
        "исходный код": pathlib.Path("/opt/home-vpn-source"),
        "служба сайта": pathlib.Path("/etc/systemd/system/vpn-shop.service"),
    }
    return [label for label, path in markers.items() if path.exists()]


def home_vpn_installed():
    return bool(installation_markers())


def choose_action():
    installed = home_vpn_installed()
    details = ", ".join(installation_markers())
    state = f"HOME-VPN обнаружен ({details})" if installed else "HOME-VPN не установлен"
    print(f"\n{state}. Выберите действие:")
    print("  1 — установить или повторно настроить")
    print("  2 — обновить HOME-VPN, сохранив настройки и пользователей")
    print("  3 — создать полный backup сайта, 3x-ui и Telegram-бота")
    print("  4 — восстановить настройки сайта, 3x-ui и Telegram-бота из backup")
    print("  5 — удалить HOME-VPN, сохранив 3x-ui и её клиентов")
    print("  0 — выйти без изменений")
    actions = {"1": "install", "2": "update", "3": "backup", "4": "restore", "5": "remove", "0": "exit"}
    while True:
        choice = ask("Действие", "2" if installed else "1")
        if choice in actions:return actions[choice]
        print("Допустимы варианты 0, 1, 2, 3, 4 или 5")


BACKUP_ALLOWED_ROOTS = (
    "etc/x-ui", "etc/nginx", "etc/letsencrypt", "etc/kit", "usr/local/x-ui",
    "etc/home-vpn", "etc/home-vpn-mail", "etc/vpn-shop.env", "etc/vpn-bot.env",
    "etc/vpn-bot-admin.env", "etc/vpn-bot-menu.json",
    "etc/sudoers.d/vpn-bot-admin", "etc/ufw/user.rules",
    "etc/ufw/user6.rules", "etc/default/ufw", "etc/cron.d/x-ui",
    "etc/cron.d/3x-ui-pro", "etc/systemd/system/x-ui.service",
    "etc/systemd/system/kit-sub.service",
    "etc/systemd/system/vpn-shop.service", "etc/systemd/system/vpn-bot.service",
    "etc/systemd/system/vpn-bot-admin.service", "etc/systemd/system/home-vpn-",
    "var/lib/vpn-shop", "var/lib/vpn-bot", "srv/home-vpn",
    "root/home-vpn-topology.json", "root/home-vpn-mail-credentials.txt",
)


def normalized_backup_path(value):
    parts=[]
    for part in pathlib.PurePosixPath(str(value).lstrip("/")).parts:
        if part in ("", "."):continue
        if part=="..":
            if not parts:raise ValueError("Путь выходит за пределы архива")
            parts.pop()
        else:parts.append(part)
    return "/".join(parts)


def backup_path_allowed(path):
    if path in (
        "", "manifest.json", "etc", "etc/systemd", "etc/systemd/system",
        "etc/sudoers.d", "etc/ufw", "etc/default", "etc/cron.d",
        "usr", "usr/local", "var", "var/lib", "srv", "root",
    ):
        return True
    return any(
        path==root or path.startswith(root + "/")
        or (root.endswith("home-vpn-") and path.startswith(root))
        for root in BACKUP_ALLOWED_ROOTS
    )


def safe_backup_members(bundle):
    seen=set();symlinks=set();validated=[]
    for member in bundle.getmembers():
        if pathlib.PurePosixPath(member.name).is_absolute():
            raise ValueError("Абсолютный путь запрещён в backup: "+member.name)
        path=normalized_backup_path(member.name)
        if not backup_path_allowed(path):raise ValueError("Недопустимый путь в backup: "+member.name)
        if path in seen:raise ValueError("Повторяющийся путь в backup: "+member.name)
        seen.add(path)
        if member.islnk():raise ValueError("Hardlink запрещён в backup: "+member.name)
        if member.issym():
            if not member.linkname:raise ValueError("Пустая ссылка в backup: "+member.name)
            target=normalized_backup_path(member.linkname if member.linkname.startswith("/") else str(pathlib.PurePosixPath(path).parent/member.linkname))
            if not backup_path_allowed(target):raise ValueError("Небезопасная ссылка в backup: "+member.name)
            symlinks.add(path)
        elif not (member.isfile() or member.isdir()):
            raise ValueError("Специальный файл запрещён в backup: "+member.name)
        validated.append((path,member))
    for path,_member in validated:
        if any(path.startswith(link+"/") for link in symlinks):
            raise ValueError("Путь внутри символической ссылки запрещён в backup: "+path)
    return [member for _path,member in validated]


def restorable_backup_members(bundle):
    return [member for member in safe_backup_members(bundle) if normalized_backup_path(member.name) not in ("","manifest.json")]


def file_sha256(path):
    digest=hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):digest.update(block)
    return digest.hexdigest()


def verify_full_backup(archive, destination=None):
    archive=pathlib.Path(archive).resolve();checksum=archive.with_name(archive.name+".sha256")
    if not archive.is_file():raise ValueError(f"Архив не найден: {archive}")
    if not checksum.is_file():raise ValueError(f"Нет файла SHA-256: {checksum}")
    fields=checksum.read_text(encoding="ascii").split()
    if not fields:raise ValueError("Файл SHA-256 пуст")
    expected=fields[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}",expected) or file_sha256(archive)!=expected:
        raise ValueError("SHA-256 архива не совпадает")
    temporary=destination is None
    root=pathlib.Path(destination) if destination else pathlib.Path(tempfile.mkdtemp(prefix="home-vpn-full-verify-"))
    try:
        root.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive,"r:gz") as bundle:
            members=safe_backup_members(bundle);bundle.extractall(root,members=members)
        manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format")!=1 or manifest.get("type")!="home-vpn-full":
            raise ValueError("Неподдерживаемый формат полного backup")
        for database in list(root.rglob("*.db"))+list(root.rglob("*.sqlite"))+list(root.rglob("*.sqlite3")):
            connection=sqlite3.connect("file:"+str(database)+"?mode=ro",uri=True)
            try:result=connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:connection.close()
            if result!="ok":raise ValueError(f"SQLite повреждена: {database}: {result}")
        return root
    finally:
        if temporary:shutil.rmtree(root,ignore_errors=True)


def deploy_current_source():
    destination = pathlib.Path("/opt/home-vpn-source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".home-vpn-source.", dir=destination.parent))
    previous = destination.with_name(destination.name + ".previous")
    try:
        tracked = run(
            ["git", "-C", ROOT, "ls-files", "-z"], capture=True, timeout=60,
        ).stdout.split("\0")
        for relative in filter(None, tracked):
            relative_path = pathlib.PurePosixPath(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise RuntimeError("Git вернул небезопасный путь: " + relative)
            source = ROOT / relative_path
            target = temporary / relative_path
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if previous.exists():shutil.rmtree(previous)
        if destination.exists():os.replace(destination, previous)
        os.replace(temporary, destination)
        if previous.exists():shutil.rmtree(previous)
    except Exception:
        if not destination.exists() and previous.exists():os.replace(previous, destination)
        raise
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return destination


def manager_command(source, action, components, purge=False, confirmation=""):
    command = [sys.executable, source / "scripts/home-vpn-manager.py", action, *components]
    if purge:command.append("--purge-data")
    environment = os.environ.copy();environment["HOME_VPN_SOURCE_ROOT"] = str(source)
    return subprocess.run(
        command, input=(confirmation + "\n") if confirmation else None,
        text=True, check=True, env=environment,
    )


def installed_managed_components():
    components = []
    if pathlib.Path("/etc/vpn-shop.env").is_file():components.append("site")
    if pathlib.Path("/etc/vpn-bot.env").is_file():components.append("bot")
    if pathlib.Path("/etc/systemd/system/home-vpn-placeholder.service").is_file():components.append("placeholder")
    if pathlib.Path("/etc/home-vpn-mail").is_dir():components.append("mail")
    return components


def active_managed_services():
    return [
        service for service in MANAGED_SERVICES
        if run(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0
    ]


def create_full_backup(require_installation=True):
    if require_installation and not (home_vpn_installed() or existing_xui_detected()):
        raise SystemExit("HOME-VPN и 3x-ui не обнаружены: сохранять нечего")
    BACKUP_DIR.mkdir(parents=True,exist_ok=True);BACKUP_DIR.chmod(0o700)
    before=set(BACKUP_DIR.glob(BACKUP_GLOB));active=active_managed_services()
    try:
        if active:run(["systemctl", "stop", *active], timeout=180)
        run(["bash", "-s"], input_text=PREINSTALL_BACKUP, timeout=1800)
    finally:
        if active:run(["systemctl", "start", *active], timeout=180, check=False)
    created=sorted(set(BACKUP_DIR.glob(BACKUP_GLOB))-before,key=lambda path:path.stat().st_mtime)
    if not created:raise RuntimeError("Сценарий backup не создал новый архив")
    archive=created[-1];verify_full_backup(archive)
    print(f"Полный проверенный backup: {archive}")
    print(f"SHA-256: {archive}.sha256")
    return archive


def backup_before_lifecycle_change():
    if not (home_vpn_installed() or existing_xui_detected()):return None
    return create_full_backup(require_installation=False)


def choose_backup_archive():
    archives=sorted(BACKUP_DIR.glob(BACKUP_GLOB),key=lambda path:path.stat().st_mtime,reverse=True)
    if archives:
        print("\nДоступные полные backup:")
        for index,path in enumerate(archives,1):
            stamp=time.strftime("%d.%m.%Y %H:%M",time.localtime(path.stat().st_mtime))
            print(f"  {index} — {path} ({stamp}, {path.stat().st_size//1024//1024} MiB)")
    while True:
        value=ask("Номер backup или абсолютный путь", "1" if archives else "")
        if value.isdigit() and 1<=int(value)<=len(archives):return archives[int(value)-1]
        candidate=pathlib.Path(value)
        if candidate.is_absolute() and candidate.is_file():return candidate
        print("Укажите существующий архив из списка или его абсолютный путь")


def restore_full_backup(plan_path=DEFAULT_PLAN):
    if not home_vpn_installed():
        raise SystemExit("Сначала установите HOME-VPN, затем восстановите его настройки из backup")
    archive=choose_backup_archive()
    with tempfile.TemporaryDirectory(prefix="home-vpn-full-restore-") as directory:
        staging=pathlib.Path(directory);working_archive=staging/archive.name
        if not archive.with_name(archive.name+".sha256").is_file():
            raise ValueError(f"Нет файла SHA-256: {archive}.sha256")
        shutil.copy2(archive,working_archive)
        shutil.copy2(archive.with_name(archive.name+".sha256"),working_archive.with_name(working_archive.name+".sha256"))
        verify_full_backup(working_archive)
        print(f"Backup проверен: {archive}")
        print("Будут заменены настройки и данные сайта, 3x-ui, TLS/Nginx и Telegram-бота.")
        require_confirmation("Подтвердите восстановление HOME-VPN", "RESTORE HOME VPN")
        rollback=create_full_backup()
        active=active_managed_services()
        try:
            if active:run(["systemctl", "stop", *active], timeout=180)
            with tarfile.open(working_archive,"r:gz") as bundle:
                members=restorable_backup_members(bundle)
                bundle.extractall("/",members=members)
            for database in (pathlib.Path("/etc/x-ui/x-ui.db"),pathlib.Path("/var/lib/vpn-shop/shop.db")):
                if not database.is_file():continue
                connection=sqlite3.connect("file:"+str(database)+"?mode=ro",uri=True)
                try:result=connection.execute("PRAGMA integrity_check").fetchone()[0]
                finally:connection.close()
                if result!="ok":raise RuntimeError(f"SQLite повреждена после восстановления: {database}: {result}")
            run(["systemctl","daemon-reload"])
            if shutil.which("nginx"):
                result=run(["nginx","-t"],capture=True,check=False)
                if result.returncode:raise RuntimeError(result.stderr.strip() or "nginx -t завершился ошибкой")
            if shutil.which("ufw"):run(["ufw","reload"],check=False)
        except Exception as error:
            print(f"Восстановление завершилось ошибкой: {error}")
            print(f"Rollback-архив текущего состояния: {rollback}")
            raise
        finally:
            if active:run(["systemctl", "start", *active], timeout=180, check=False)
    if pathlib.Path(plan_path).is_file():run([sys.executable,SMOKE_TEST,"--config",plan_path],check=False)
    show_results(plan_path)
    print(f"\nНастройки восстановлены из {archive}")
    print(f"Защитный rollback сохранён: {rollback}")


def update_home_vpn(plan_path=DEFAULT_PLAN):
    if not home_vpn_installed():
        raise SystemExit("HOME-VPN не установлен; выберите действие 1")
    backup_before_lifecycle_change()
    source = deploy_current_source()
    components = [name for name in installed_managed_components() if name in ("site", "bot", "placeholder")]
    if not components:raise SystemExit("Не найдены компоненты HOME-VPN для обновления")
    manager_command(source, "update", components)
    if pathlib.Path("/usr/local/sbin/home-vpn-setup").is_file():
        run(["/usr/local/sbin/home-vpn-setup", "validate", "--config", "/etc/home-vpn/config.json"])
        run(["/usr/local/sbin/home-vpn-setup", "doctor", "--config", "/etc/home-vpn/config.json"])
    if pathlib.Path(plan_path).is_file():run([sys.executable, SMOKE_TEST, "--config", plan_path])
    show_results(plan_path)
    print("\nHOME-VPN обновлён; базы, пароли, 3x-ui и её клиенты сохранены.")


def restore_preserved_nginx_route():
    marker = "include /etc/nginx/snippets/home-vpn-site.conf;"
    for source in pathlib.Path("/etc/nginx").glob("sites-available/*"):
        if not source.is_file():continue
        text = source.read_text(encoding="utf-8")
        if marker not in text:continue
        backup = source.with_name(source.name + ".pre-home-vpn")
        if not backup.is_file():
            raise RuntimeError(f"Нет исходного Nginx vhost для безопасного удаления: {backup}")
        original = backup.read_text(encoding="utf-8")
        original_includes = re.findall(r"\binclude\s+[^;]+;", original)
        replacement = next((item for item in original_includes if item not in text), "")
        if not replacement:
            raise RuntimeError("Не удалось восстановить исходный include Nginx")
        updated = text.replace(marker, replacement, 1)
        temporary = source.with_name(source.name + ".remove-home-vpn")
        temporary.write_text(updated, encoding="utf-8");temporary.chmod(0o644)
        os.replace(temporary, source)
        result = run(["nginx", "-t"], capture=True, check=False)
        if result.returncode:
            source.write_text(text, encoding="utf-8");source.chmod(0o644)
            raise RuntimeError(result.stderr.strip() or "nginx -t завершился ошибкой")
        run(["systemctl", "reload", "nginx"])
    snippet = pathlib.Path("/etc/nginx/snippets/home-vpn-site.conf")
    referenced = any(marker in item.read_text(encoding="utf-8", errors="ignore") for item in pathlib.Path("/etc/nginx").glob("sites-available/*") if item.is_file())
    if not referenced:snippet.unlink(missing_ok=True)


def remove_home_vpn():
    if not home_vpn_installed():raise SystemExit("HOME-VPN не установлен")
    backup_before_lifecycle_change()
    print("3x-ui, её inbounds и пользовательские клиенты удаляться не будут.")
    require_confirmation("Подтвердите удаление HOME-VPN", "DELETE HOME-VPN")
    purge = ask_yes_no("Удалить также базы HOME-VPN и его настройки?", False)
    restore_preserved_nginx_route()
    source = deploy_current_source()
    components = installed_managed_components()
    if components:manager_command(source, "remove", components, purge, "DELETE HOME-VPN")
    for path in (
        "/opt/home-vpn-source", "/usr/local/share/home-vpn",
        "/usr/local/sbin/home-vpn-manager", "/usr/local/sbin/home-vpn-setup",
        "/usr/local/sbin/home-vpn-xui", "/usr/local/sbin/home-vpn-components",
        "/usr/local/sbin/home-vpn-state-backup", "/usr/local/sbin/home_vpn_config.py",
        "/usr/local/sbin/home_vpn_installer.py",
        "/etc/letsencrypt/renewal-hooks/deploy/home-vpn-services",
    ):
        item = pathlib.Path(path)
        if item.is_dir() and not item.is_symlink():shutil.rmtree(item)
        else:item.unlink(missing_ok=True)
    if purge:
        pathlib.Path("/root/home-vpn-topology.json").unlink(missing_ok=True)
    print("HOME-VPN удалён. 3x-ui, VPN-подключения, сертификаты и проверенные backup сохранены.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, default=DEFAULT_PLAN,
                        help="куда сохранить JSON-план без секретов")
    parser.add_argument("--plan-only", action="store_true",
                        help="проверить DNS и сохранить план, не изменяя VPS")
    parser.add_argument("--site-domain", default="",
                        help="домен сайта для новой или нестандартной конфигурации")
    parser.add_argument("--panel-domain", default="",
                        help="домен 3x-ui для новой или нестандартной конфигурации")
    parser.add_argument("--action", choices=("install", "update", "backup", "restore", "remove"),
                        help="выбрать действие без стартового меню")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Запустите мастер от root: sudo bash install-single-node.sh")

    action = arguments.action or choose_action()
    if action == "exit":return
    if action == "update":update_home_vpn(arguments.plan);return
    if action == "backup":create_full_backup();return
    if action == "restore":restore_full_backup(arguments.plan);return
    if action == "remove":remove_home_vpn();return

    answers = collect_answers(arguments.site_domain, arguments.panel_domain)
    check_dns(answers["site_domain"], answers["public_ip"])
    check_dns(answers["panel_domain"], answers["public_ip"])
    plan = build_topology(**answers)
    save_plan(plan, arguments.plan)
    validate_plan(arguments.plan)
    if answers["xui_install_mode"] == "preserve":
        report = preserve_preflight(answers["site_domain"], answers["bot_enabled"])
        print(f"Read-only preflight: {report['inbounds']} inbounds; nginx vhost {report['vhost']}")
    if arguments.plan_only:
        print(f"План сохранён: {arguments.plan}\nVPS не изменялась.")
        return

    print("\nБудут установлены пакеты, UFW, TLS, 3x-ui, HOME-VPN и выбранные компоненты.")
    print(f"Сайт: https://{answers['site_domain']}")
    print(f"3x-ui: https://{answers['panel_domain']}")
    print("Режим 3x-ui:","сохранение существующей конфигурации" if answers["xui_install_mode"]=="preserve" else "чистая установка")
    require_confirmation("Подтвердите установку",APPLY_CONFIRMATION)

    if existing_xui_detected():backup_before_lifecycle_change()
    if answers["xui_install_mode"]=="fresh" and existing_xui_detected():
        require_confirmation("Подтвердите удаление 3x-ui и всех клиентов","ERASE 3X-UI AND CLIENTS")
        run(["bash","-s"],input_text=RESET_EXISTING_STACK,timeout=900)

    run(
        ["bash", "-s", "--", answers["site_domain"], answers["panel_domain"],
         answers["ssh_port"], answers["xui_install_mode"]],
        input_text=PREPARE_SERVER, timeout=1800,
    )
    run([
        sys.executable, TOPOLOGY_INSTALLER, "--config", arguments.plan,
        "--apply", "--confirm", "INSTALL HOME VPN TOPOLOGY",
    ])
    run(["bash", "-s"], input_text=INSTALL_RENEW_HOOK, timeout=60)
    run([sys.executable, SMOKE_TEST, "--config", arguments.plan])
    show_results(arguments.plan)
    print("\nHOME-VPN установлен и проверен.")
    if answers["bot_enabled"]:
        print("Откройте нового Telegram-бота, отправьте /start и выполните: systemctl restart vpn-bot")
    print("Сохраните выведенные логины, пароли и скрытые URL в менеджере паролей.")


if __name__ == "__main__":
    main()
