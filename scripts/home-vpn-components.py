#!/usr/bin/env python3
"""Install standalone HOME-VPN components on dedicated servers."""

import getpass
import os
import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def ask(prompt, default="", secret=False):
    suffix = f" [{default}]" if default and not secret else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or default


def run(*args):
    subprocess.run(args, check=True)


def write_env(path, values):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    path.chmod(0o600)


def install_bot():
    public = ask("Публичный URL сайта", "https://my.domain.ru")
    api = ask("URL API сайта", public)
    secret = ask("API_SECRET с сервера сайта", secret=True)
    token = ask("Telegram Bot Token", secret=True)
    admin_id = ask("Цифровой Telegram ID администратора")
    if not admin_id.isdigit(): raise SystemExit("Telegram ID должен состоять из цифр")
    channel = ask("Telegram-канал, необязательно")
    admin_path = ask("Закрытый URL админ-панели", public + "/private-admin-path")
    panel_url = ask("Публичный URL отдельной панели бота", "https://bot-admin.my.domain.ru")
    panel_bind = ask("Адрес прослушивания панели (127.0.0.1 для reverse proxy)", "127.0.0.1")
    panel_port = ask("Внутренний порт панели", "8090")
    if not panel_port.isdigit(): raise SystemExit("Порт панели должен быть числом")
    panel_user = ask("Логин панели бота", "bot-admin")
    generated_password = __import__('secrets').token_urlsafe(16)
    panel_password = ask("Пароль панели бота (Enter = сгенерировать)", generated_password, True)
    panel_entry = "/bot-" + __import__('secrets').token_urlsafe(8)
    run("apt-get", "update"); run("apt-get", "install", "-y", "python3", "ca-certificates", "sudo")
    subprocess.run(["useradd", "--system", "--home", "/var/lib/vpn-bot", "--shell", "/usr/sbin/nologin", "vpnshopbot"], check=False)
    pathlib.Path("/opt/vpn-bot").mkdir(parents=True, exist_ok=True); pathlib.Path("/var/lib/vpn-bot").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "vpn-bot/bot.py", "/opt/vpn-bot/bot.py")
    shutil.copy2(ROOT / "vpn-bot/vpn-bot.service", "/etc/systemd/system/vpn-bot.service")
    shutil.copy2(ROOT / "vpn-bot/admin.py", "/opt/vpn-bot/admin.py")
    shutil.copy2(ROOT / "vpn-bot/admin-control.py", "/usr/local/sbin/vpn-bot-admin-control"); pathlib.Path('/usr/local/sbin/vpn-bot-admin-control').chmod(0o700)
    shutil.copy2(ROOT / "vpn-bot/vpn-bot-admin.service", "/etc/systemd/system/vpn-bot-admin.service")
    shutil.copy2(ROOT / "vpn-bot/vpn-bot-admin-sudoers", "/etc/sudoers.d/vpn-bot-admin"); pathlib.Path('/etc/sudoers.d/vpn-bot-admin').chmod(0o440)
    run("visudo", "-cf", "/etc/sudoers.d/vpn-bot-admin")
    menu_config = pathlib.Path("/etc/vpn-bot-menu.json")
    if not menu_config.exists(): shutil.copy2(ROOT / "config/vpn-bot-menu.example.json", menu_config)
    run("chown", "root:vpnshopbot", str(menu_config)); menu_config.chmod(0o640)
    write_env(pathlib.Path("/etc/vpn-bot.env"), {
        "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ADMIN_ID": admin_id, "TELEGRAM_CHANNEL": channel,
        "SHOP_API_URL": api.rstrip("/"), "PUBLIC_BASE_URL": public.rstrip("/"), "SHOP_API_SECRET": secret,
        "ADMIN_PANEL_URL": admin_path, "BOT_STATE_FILE": "/var/lib/vpn-bot/last_event_v2",
        "PAYMENT_TIMERS_STATE": "/var/lib/vpn-bot/payment_timers.json", "BOT_MENU_CONFIG": str(menu_config),
    })
    write_env(pathlib.Path("/etc/vpn-bot-admin.env"), {
        "BOT_PANEL_BIND": panel_bind, "BOT_PANEL_PORT": panel_port, "BOT_PANEL_USER": panel_user,
        "BOT_PANEL_PASSWORD": panel_password, "BOT_PANEL_SECRET": __import__('secrets').token_urlsafe(48),
        "BOT_PANEL_ENTRY_PATH": panel_entry, "BOT_PANEL_SECURE_COOKIE": "1" if panel_url.startswith("https://") else "0",
    })
    run("chown", "-R", "vpnshopbot:vpnshopbot", "/opt/vpn-bot", "/var/lib/vpn-bot")
    run("systemctl", "daemon-reload"); run("systemctl", "enable", "--now", "vpn-bot", "vpn-bot-admin")
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


def install_backup_node():
    print("Используйте отдельный сервер. Рабочие серверы подключаются после установки через GUI.")
    confirmation = ask("Введите YES для продолжения")
    if confirmation != "YES": raise SystemExit("Отменено")
    panel_ip=ask("IP для сертификата панели", "192.168.1.53")
    panel_host=ask("Публичный домен или IP", "backup.my.domain.ru")
    panel_bind=ask("Адрес прослушивания", "0.0.0.0")
    panel_port=ask("HTTPS-порт панели", "9443")
    if not panel_port.isdigit():raise SystemExit("Порт должен быть числом")
    run("python3",str(ROOT/"backup-node/install-backup-panel.py"),"--ip",panel_ip,"--public-host",panel_host,"--bind",panel_bind,"--port",panel_port)
    print("Откройте панель и нажмите «Добавить сервер». Ключи рабочих серверов создаются их backup-агентами.")


def install_backup_agent():
    print("Backup-агент ставится на рабочий сервер и только отправляет копии в хранилище.")
    server_id=ask("Уникальный ID сервера", "prod-ru")
    server_name=ask("Понятное название", "Основной RU")
    role=ask("Роль: site, bot или node", "site").lower()
    if role not in ("site","bot","node"):raise SystemExit("Допустимые роли: site, bot, node")
    remote_host=ask("IP или домен backup-сервера", "backup.my.domain.ru")
    remote_port=ask("SSH-порт backup-сервера", "22")
    if not remote_port.isdigit():raise SystemExit("SSH-порт должен быть числом")
    run("python3",str(ROOT/"backup-node/install-full-backup-agent.py"),"--server-id",server_id,"--server-name",server_name,"--role",role,"--remote",f"homevpnbackup@{remote_host}","--port",remote_port)


def main():
    if os.geteuid() != 0: raise SystemExit("Запустите через sudo")
    choice = sys.argv[1] if len(sys.argv) > 1 else ""
    if not choice:
        print("1 — Telegram-бот\n2 — почтовый сервер\n3 — backup-сервер\n4 — backup-агент")
        choice = ask("Компонент")
    if choice in ("1", "bot"): install_bot()
    elif choice in ("2", "mail"): install_mail()
    elif choice in ("3", "backup"): install_backup_node()
    elif choice in ("4", "backup-agent"): install_backup_agent()
    else: raise SystemExit("Неизвестный компонент")


if __name__ == "__main__": main()
