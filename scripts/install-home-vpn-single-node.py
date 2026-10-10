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

cert_dir="/etc/letsencrypt/live/$site_domain"
certificate_ok=false
if [ -s "$cert_dir/fullchain.pem" ] && [ -s "$cert_dir/privkey.pem" ]; then
    if openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkend 604800 >/dev/null 2>&1 && \
       openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkhost "$site_domain" >/dev/null 2>&1 && \
       openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkhost "$panel_domain" >/dev/null 2>&1; then
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


def build_topology(*, public_ip, site_domain, panel_domain, country, node_name,
                   sni, bot_enabled, installer_url, installer_sha256, **_ignored):
    cert_dir = f"/etc/letsencrypt/live/{site_domain}"
    return {
        "mode": 3,
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
    installer_url = validate_https_url(ask("HTTPS URL установщика 3x-ui/KIT", DEFAULT_KIT_URL))
    installer_sha256 = ask("SHA-256 установщика", DEFAULT_KIT_SHA256).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", installer_sha256):
        raise SystemExit("SHA-256 должен содержать ровно 64 hex-символа")
    return {
        "public_ip": public_ip, "ssh_port": ssh_port,
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
    print("Панель 3x-ui:     ", xui.get("XUI_ACCESS_URL", "не найден"))
    print("Логин 3x-ui:      ", xui.get("XUI_USERNAME", "не найден"))
    print("Пароль 3x-ui:     ", xui.get("XUI_PASSWORD", "не найден"))
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
    if arguments.plan_only:
        print(f"План сохранён: {arguments.plan}\nVPS не изменялась.")
        return

    print("\nБудут установлены пакеты, UFW, TLS, 3x-ui, HOME-VPN и выбранные компоненты.")
    print(f"Сайт: https://{answers['site_domain']}")
    print(f"3x-ui: https://{answers['panel_domain']}")
    confirmation = input(f"Для продолжения введите {APPLY_CONFIRMATION!r}: ").strip()
    if confirmation != APPLY_CONFIRMATION:
        raise SystemExit("Установка отменена; сохранён только план")

    run(
        ["bash", "-s", "--", answers["site_domain"], answers["panel_domain"],
         answers["cert_email"], answers["ssh_port"]],
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
