#!/usr/bin/env python3
"""Install standalone HOME-VPN components on dedicated servers."""

import getpass
import json
import os
import pathlib
import shutil
import subprocess
import sys

from home_vpn_config import ENV_SCHEMAS, merge_env
from home_vpn_installer import install_component_files


ROOT = pathlib.Path(__file__).resolve().parents[1]
ANSWERS = {}
NON_INTERACTIVE = False


def ask(prompt, default="", secret=False):
    suffix = f" [{default}]" if default and not secret else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or default


def answer(key, prompt, default="", secret=False):
    if key in ANSWERS: return str(ANSWERS[key])
    if NON_INTERACTIVE: return str(default or "")
    return ask(prompt, default, secret)


def run(*args):
    subprocess.run(args, check=True)


def write_env(path, values):
    component="bot_admin" if path.name=="vpn-bot-admin.env" else "bot"
    merge_env(path,values,component=component,preserve_secrets=ENV_SCHEMAS[component]["secret"],mode=0o600)


def install_bot():
    public = answer("public_url", "Публичный домен или URL сайта", "https://my.domain.ru")
    if "://" not in public: public = "https://" + public
    public = public.rstrip("/")
    api = answer("shop_api_url", "Внутренний loopback URL API сайта", "http://127.0.0.1:8080")
    parsed_api=__import__('urllib.parse',fromlist=['urlparse']).urlparse(api)
    if parsed_api.hostname not in ("127.0.0.1","::1","localhost"):raise SystemExit("Внутренний API бота должен использовать loopback")
    secret = answer("shop_api_secret", "API_SECRET с сервера сайта", secret=True)
    token = answer("telegram_token", "Telegram Bot Token", secret=True)
    admin_id = answer("telegram_admin_id", "Цифровой Telegram ID администратора")
    if not secret or not token: raise SystemExit("Для бота нужны SHOP_API_SECRET и Telegram Bot Token")
    if not admin_id.isdigit(): raise SystemExit("Telegram ID должен состоять из цифр")
    channel = answer("telegram_channel", "Telegram-канал, необязательно")
    telegram_proxy = answer("telegram_proxy_url", "Локальный health-aware HTTP-прокси Telegram", "http://127.0.0.1:18080")
    parsed_proxy=__import__('urllib.parse',fromlist=['urlparse']).urlparse(telegram_proxy)
    if parsed_proxy.hostname not in ("127.0.0.1","::1","localhost"):raise SystemExit("Telegram egress должен слушать loopback; прямой обход запрещён")
    admin_path = answer("admin_panel_url", "Закрытый URL админ-панели", public + "/private-admin-path")
    panel_url = answer("bot_admin_url", "Публичный URL отдельной панели бота", "https://bot-admin.my.domain.ru")
    panel_bind = answer("bot_panel_bind", "Адрес прослушивания панели (127.0.0.1 для reverse proxy)", "127.0.0.1")
    if panel_bind not in ("127.0.0.1", "::1"): raise SystemExit("Панель бота может слушать только loopback 127.0.0.1 или ::1")
    panel_port = answer("bot_panel_port", "Внутренний порт панели", "8090")
    if not panel_port.isdigit(): raise SystemExit("Порт панели должен быть числом")
    panel_user = answer("bot_panel_user", "Логин панели бота", "bot-admin")
    generated_password = __import__('secrets').token_urlsafe(16)
    panel_password = answer("bot_panel_password", "Пароль панели бота (Enter = сгенерировать)", generated_password, True)
    panel_entry = "/bot-" + __import__('secrets').token_urlsafe(8)
    run("apt-get", "update"); run("apt-get", "install", "-y", "python3", "ca-certificates", "sudo")
    subprocess.run(["useradd", "--system", "--home", "/var/lib/vpn-bot", "--shell", "/usr/sbin/nologin", "vpnshopbot"], check=False)
    pathlib.Path("/var/lib/vpn-bot").mkdir(parents=True, exist_ok=True);install_component_files("bot",ROOT)
    run("visudo", "-cf", "/etc/sudoers.d/vpn-bot-admin")
    menu_config = pathlib.Path("/etc/vpn-bot-menu.json")
    if not menu_config.exists(): shutil.copy2(ROOT / "config/vpn-bot-menu.example.json", menu_config)
    run("chown", "root:vpnshopbot", str(menu_config)); menu_config.chmod(0o640)
    write_env(pathlib.Path("/etc/vpn-bot.env"), {
        "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ADMIN_ID": admin_id, "TELEGRAM_CHANNEL": channel,
        "TELEGRAM_PROXY_URL": telegram_proxy,
        "SHOP_API_URL": api.rstrip("/"), "PUBLIC_BASE_URL": public.rstrip("/"), "SHOP_API_SECRET": secret,
        "ADMIN_PANEL_URL": admin_path, "BOT_STATE_FILE": "/var/lib/vpn-bot/last_event_v2",
        "PAYMENT_TIMERS_STATE": "/var/lib/vpn-bot/payment_timers.json", "BOT_MENU_CONFIG": str(menu_config),
        "BOT_RUNTIME_CONFIG": "/var/lib/vpn-bot/runtime.json",
    })
    write_env(pathlib.Path("/etc/vpn-bot-admin.env"), {
        "BOT_PANEL_BIND": panel_bind, "BOT_PANEL_PORT": panel_port, "BOT_PANEL_USER": panel_user,
        "BOT_PANEL_PASSWORD": panel_password, "BOT_PANEL_SECRET": __import__('secrets').token_urlsafe(48),
        "BOT_PANEL_ENTRY_PATH": panel_entry, "BOT_PANEL_SECURE_COOKIE": "1" if panel_url.startswith("https://") else "0",
    })
    site_env=pathlib.Path('/etc/vpn-shop.env')
    if site_env.exists():
        merge_env(site_env,{"BOT_ADMIN_URL":panel_url.rstrip('/')+panel_entry},mode=0o600)
    run("chown", "-R", "vpnshopbot:vpnshopbot", "/opt/vpn-bot", "/var/lib/vpn-bot")
    run("systemctl", "daemon-reload"); run("systemctl", "enable", "--now", "vpn-bot", "vpn-bot-admin", "home-vpn-bot-backup.timer")
    if site_env.exists():run("systemctl","restart","vpn-shop")
    print("Telegram-бот и отдельная панель установлены.")
    print("Панель:", panel_url.rstrip('/') + panel_entry)
    print("Логин:", panel_user)
    print("Пароль:", panel_password)
    print("Сохраните эти данные сейчас. Настройте TLS/reverse proxy на", panel_bind + ":" + panel_port)


def install_mail():
    domain = ask("Почтовый домен", "my.domain.ru")
    host = ask("Имя почтового сервера", "mail." + domain)
    user = ask("Имя почтового ящика", "support")
    address = ask("Адрес отправителя", user + "@" + domain)
    bind_ip = ask("IP сервера для тестового сертификата", "192.168.1.50")
    environment = os.environ.copy(); environment.update({"MAIL_DOMAIN": domain, "MAIL_HOST": host, "MAIL_USER": user, "MAIL_ADDRESS": address, "MAIL_BIND_IP": bind_ip})
    subprocess.run(["bash", str(ROOT / "mail-server/setup-mail-server.sh")], check=True, env=environment)


def main():
    global ANSWERS, NON_INTERACTIVE
    if os.geteuid() != 0: raise SystemExit("Запустите через sudo")
    arguments=list(sys.argv[1:]);answers_path=None
    if "--answers" in arguments:
        index=arguments.index("--answers")
        if index+1>=len(arguments):raise SystemExit("После --answers нужен путь")
        answers_path=pathlib.Path(arguments[index+1]);del arguments[index:index+2]
        if answers_path.stat().st_mode & 0o077:raise SystemExit("Answers-файл должен иметь права 0600")
        ANSWERS=json.loads(answers_path.read_text(encoding="utf-8"));NON_INTERACTIVE=True
    choice = arguments[0] if arguments else ""
    if not choice:
        print("1 — Telegram-бот\n2 — почтовый сервер")
        choice = ask("Компонент")
    if choice in ("1", "bot"): install_bot()
    elif choice in ("2", "mail"): install_mail()
    else: raise SystemExit("Неизвестный компонент")


if __name__ == "__main__": main()
