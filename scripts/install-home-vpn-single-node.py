#!/usr/bin/env python3
"""Interactive end-to-end installer run directly on a fresh HOME-VPN VPS."""

import argparse
import ipaddress
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import urllib.parse


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOPOLOGY_INSTALLER = ROOT / "scripts/install-home-vpn-topology.py"
SMOKE_TEST = ROOT / "scripts/smoke-home-vpn-topology.py"
DEFAULT_PLAN = pathlib.Path("/root/home-vpn-topology.json")
DEFAULT_KIT_URL = "https://raw.githubusercontent.com/itsnotkubrick/3X-UI_KIT/v1.2/scripts/3x-ui.sh"
DEFAULT_KIT_SHA256 = "2d7a0f0dcc26ea4f8bab3d60e1900e7046a9673a88fef85c03630502120961f0"
APPLY_CONFIRMATION = "INSTALL HOME VPN"


PREPARE_SERVER = r'''set -Eeuo pipefail
site_domain=$1
panel_domain=$2
cert_email=$3
ssh_port=$4
xui_mode=$5
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
        --cert-name "$site_domain" --email "$cert_email" \
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
stage=$(mktemp -d /var/backups/home-vpn/.preinstall.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
tar -C / --exclude=etc/x-ui/x-ui.db --exclude=etc/x-ui/x-ui.db-wal --exclude=etc/x-ui/x-ui.db-shm \
    -cf - etc/x-ui etc/nginx etc/letsencrypt usr/local/x-ui 2>/dev/null | tar -C "$stage" -xf -
for item in etc/home-vpn etc/vpn-shop.env etc/vpn-bot.env etc/vpn-bot-admin.env var/lib/vpn-shop var/lib/vpn-bot; do
    [ ! -e "/$item" ] || tar -C / -cf - "$item" | tar -C "$stage" -xf -
done
mkdir -p "$stage/etc/x-ui"
python3 - "$stage/etc/x-ui/x-ui.db" <<'PY'
import pathlib,sqlite3,sys
path=pathlib.Path('/etc/x-ui/x-ui.db')
if not path.is_file():raise SystemExit('/etc/x-ui/x-ui.db не найдена')
source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
target=sqlite3.connect(sys.argv[1]);source.backup(target);target.close();source.close()
check=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)
result=check.execute('PRAGMA integrity_check').fetchone()[0];check.close()
if result!='ok':raise SystemExit('3x-ui SQLite integrity_check: '+str(result))
PY
stamp=$(date -u +%Y%m%d-%H%M%S)
archive="/var/backups/home-vpn/preinstall-${stamp}.tar.gz"
tar -czf "$archive" -C "$stage" .
chmod 0600 "$archive"
tar -tzf "$archive" >/dev/null
sha256sum "$archive" > "${archive}.sha256"
chmod 0600 "${archive}.sha256"
sha256sum -c "${archive}.sha256" >/dev/null
printf 'Проверенный архив перед установкой: %s\n' "$archive"
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


def ask_yes_no(prompt, default=True):
    marker = "Д/н" if default else "д/Н"
    value = input(f"{prompt} [{marker}]: ").strip().lower()
    if not value:
        return default
    if value in ("д", "да", "y", "yes"):
        return True
    if value in ("н", "нет", "n", "no"):
        return False
    raise SystemExit("Ответьте да или нет")


def validate_domain(value, label):
    value = str(value or "").strip().rstrip(".").lower()
    if len(value) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
        value,
    ):
        raise SystemExit(f"{label}: нужно полное DNS-имя, получено {value!r}")
    return value


def validate_email(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", value):
        raise SystemExit("Email для Let's Encrypt указан неверно")
    return value


def validate_https_url(value):
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SystemExit("URL установщика должен быть полным HTTPS-адресом без credentials")
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


def detected_ssh_port():
    connection = os.environ.get("SSH_CONNECTION", "").split()
    return connection[3] if len(connection) == 4 and connection[3].isdigit() else "22"


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
    choice=ask("Выберите вариант", "1")
    if choice=="1":return "preserve"
    if choice=="2":return "fresh"
    raise SystemExit("Допустимы варианты 1 или 2")


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


def collect_answers():
    print("\nHOME-VPN — локальная установка одной VPS\n")
    xui_install_mode=choose_xui_mode()
    public_ip = str(ipaddress.IPv4Address(ask("Публичный IPv4 этой VPS")))
    ssh_port = ask("SSH-порт этой VPS", detected_ssh_port())
    if not ssh_port.isdigit() or not 1 <= int(ssh_port) <= 65535:
        raise SystemExit("SSH-порт должен быть числом от 1 до 65535")
    site_domain = validate_domain(ask("Домен сайта, без https://"), "Домен сайта")
    panel_domain = validate_domain(ask("Домен панели 3x-ui, без https://"), "Домен панели")
    if site_domain == panel_domain:
        raise SystemExit("Для сайта и 3x-ui нужны два разных домена")
    cert_email = validate_email(ask("Email для Let's Encrypt"))
    country = ask("Код страны ноды", "DE").upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise SystemExit("Код страны должен состоять из двух латинских букв")
    node_name = ask("Название VPN-ноды", "Main VPN node")
    sni = validate_domain(ask("SNI маскировки", "dl.google.com"), "SNI")
    bot_enabled = ask_yes_no("Установить Telegram-бота?", True)
    installer_url = validate_https_url(ask("HTTPS URL установщика 3x-ui/KIT", DEFAULT_KIT_URL)) if xui_install_mode=="fresh" else DEFAULT_KIT_URL
    installer_sha256 = ask("SHA-256 установщика", DEFAULT_KIT_SHA256).lower() if xui_install_mode=="fresh" else DEFAULT_KIT_SHA256
    if not re.fullmatch(r"[0-9a-f]{64}", installer_sha256):
        raise SystemExit("SHA-256 должен содержать ровно 64 hex-символа")
    return {
        "public_ip": public_ip, "ssh_port": ssh_port,"xui_install_mode":xui_install_mode,
        "site_domain": site_domain, "panel_domain": panel_domain,
        "cert_email": cert_email, "country": country, "node_name": node_name,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, default=DEFAULT_PLAN,
                        help="куда сохранить JSON-план без секретов")
    parser.add_argument("--plan-only", action="store_true",
                        help="проверить DNS и сохранить план, не изменяя VPS")
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Запустите мастер от root: sudo bash install-single-node.sh")

    answers = collect_answers()
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
    confirmation = input(f"Для продолжения введите {APPLY_CONFIRMATION!r}: ").strip()
    if confirmation != APPLY_CONFIRMATION:
        raise SystemExit("Установка отменена; сохранён только план")

    if existing_xui_detected():
        run(["bash","-s"],input_text=PREINSTALL_BACKUP,timeout=1800)
    if answers["xui_install_mode"]=="fresh" and existing_xui_detected():
        destructive=input("Для удаления 3x-ui и всех клиентов введите 'ERASE 3X-UI AND CLIENTS': ").strip()
        if destructive!="ERASE 3X-UI AND CLIENTS":
            raise SystemExit("Очистка отменена; существующая 3x-ui не изменена")
        run(["bash","-s"],input_text=RESET_EXISTING_STACK,timeout=900)

    run(
        ["bash", "-s", "--", answers["site_domain"], answers["panel_domain"],
         answers["cert_email"], answers["ssh_port"],answers["xui_install_mode"]],
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
