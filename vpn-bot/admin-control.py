#!/usr/bin/env python3
"""Restricted root helper for the standalone Telegram bot control panel."""

import argparse
import grp
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse
import urllib.request


ENV_FILE = pathlib.Path("/etc/vpn-bot.env")
DEFAULT_MENU = {
    "welcome_title": "Домашняя приватная сеть",
    "welcome_text": "Добро пожаловать! Здесь можно оформить и продлить доступ, управлять подключениями и обратиться в поддержку.",
    "order": ["new", "renew", "upgrade", "subscriptions", "link", "support", "site", "instructions", "status", "notifications"],
    "items": {
        "new": {"enabled": True, "label": "➕ Купить новую подписку"},
        "renew": {"enabled": True, "label": "🔄 Продлить подписку"},
        "upgrade": {"enabled": True, "label": "⬆️ Изменить тариф"},
        "subscriptions": {"enabled": True, "label": "📋 Подписки и текущие заказы"},
        "link": {"enabled": True, "label": "🔗 Привязать подписку"},
        "support": {"enabled": True, "label": "🎫 Поддержка"},
        "site": {"enabled": True, "label": "🌐 Перейти на сайт"},
        "instructions": {"enabled": True, "label": "📖 Инструкции по подключению"},
        "status": {"enabled": True, "label": "🟢 Статус серверов"},
        "notifications": {"enabled": True, "label": "🔕 Отписаться от уведомлений"},
    },
}


def environment():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1); values[key] = value
    return values


def menu_path(values=None):
    return pathlib.Path((values or environment()).get("BOT_MENU_CONFIG", "/etc/vpn-bot-menu.json"))


def menu_config(values=None):
    result = json.loads(json.dumps(DEFAULT_MENU, ensure_ascii=False))
    path = menu_path(values)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored.get("welcome_title"), str): result["welcome_title"] = stored["welcome_title"]
        if isinstance(stored.get("welcome_text"), str): result["welcome_text"] = stored["welcome_text"]
        order = stored.get("order")
        if isinstance(order, list) and len(order) == len(result["items"]) and set(order) == set(result["items"]): result["order"] = order
        for key in result["items"]:
            item = (stored.get("items") or {}).get(key) or {}
            if isinstance(item.get("enabled"), bool): result["items"][key]["enabled"] = item["enabled"]
            if isinstance(item.get("label"), str): result["items"][key]["label"] = item["label"]
    except (OSError, ValueError, TypeError):
        pass
    return result


def print_menu():
    print(json.dumps(menu_config(), ensure_ascii=False))


def save_menu(raw):
    try: incoming = json.loads(raw)
    except (ValueError, TypeError) as error: raise RuntimeError("Некорректный JSON меню") from error
    expected = set(DEFAULT_MENU["items"]); items = incoming.get("items")
    if not isinstance(items, dict) or set(items) != expected: raise RuntimeError("Неполный список пунктов меню")
    title = str(incoming.get("welcome_title") or "").strip(); welcome = str(incoming.get("welcome_text") or "").strip()
    if not 3 <= len(title) <= 80: raise RuntimeError("Заголовок: от 3 до 80 символов")
    if not 10 <= len(welcome) <= 500: raise RuntimeError("Приветствие: от 10 до 500 символов")
    order = incoming.get("order")
    if not isinstance(order, list) or len(order) != len(expected) or set(order) != expected: raise RuntimeError("Некорректный порядок пунктов меню")
    clean = {"welcome_title": title, "welcome_text": welcome, "order": order, "items": {}}
    for key, default in DEFAULT_MENU["items"].items():
        item = items[key]; label = str(item.get("label") or "").strip()
        if not 2 <= len(label) <= 64: raise RuntimeError(f"Подпись {key}: от 2 до 64 символов")
        clean["items"][key] = {"enabled": bool(item.get("enabled")), "label": label}
    if not any(item["enabled"] for item in clean["items"].values()): raise RuntimeError("Оставьте включённым хотя бы один пункт меню")
    path = menu_path(); path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".vpn-bot-menu-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(clean, output, ensure_ascii=False, indent=2); output.write("\n")
        os.chmod(temporary, 0o640); os.chown(temporary, 0, grp.getgrnam("vpnshopbot").gr_gid); os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    subprocess.run(["systemctl", "restart", "vpn-bot"], check=True, timeout=30)
    print(json.dumps({"ok": True}, ensure_ascii=False))


def telegram(values, method, payload=None):
    token = values.get("TELEGRAM_BOT_TOKEN", "")
    if not token: raise RuntimeError("Telegram Bot Token не настроен")
    data = urllib.parse.urlencode(payload or {}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if not result.get("ok"): raise RuntimeError(str(result.get("description") or "Telegram API error"))
    return result.get("result")


def status():
    values = environment(); active = subprocess.run(["systemctl", "is-active", "--quiet", "vpn-bot"]).returncode == 0
    logs = subprocess.run(["journalctl", "-u", "vpn-bot", "-n", "80", "--no-pager", "--output", "short-iso"], capture_output=True, text=True, check=False).stdout
    result = {"active": active, "public_url": values.get("PUBLIC_BASE_URL", ""), "api_url": values.get("SHOP_API_URL", ""), "channel": values.get("TELEGRAM_CHANNEL", ""), "admin_id_configured": bool(values.get("TELEGRAM_ADMIN_ID", "").isdigit() and int(values.get("TELEGRAM_ADMIN_ID", "0")) > 0), "token_configured": bool(values.get("TELEGRAM_BOT_TOKEN")), "logs": logs[-20000:]}
    try:
        account = telegram(values, "getMe"); result.update({"telegram_ok": True, "bot_username": account.get("username", "")})
    except Exception as error: result.update({"telegram_ok": False, "telegram_error": str(error)[:300]})
    try:
        request = urllib.request.Request(values.get("SHOP_API_URL", "").rstrip("/") + "/health")
        with urllib.request.urlopen(request, timeout=10, context=__import__('ssl')._create_unverified_context()) as response: result["site_ok"] = response.status == 200
    except Exception as error: result.update({"site_ok": False, "site_error": str(error)[:300]})
    print(json.dumps(result, ensure_ascii=False))


def action(name):
    if name not in {"start", "stop", "restart"}: raise SystemExit("invalid action")
    subprocess.run(["systemctl", name, "vpn-bot"], check=True, timeout=30)
    print(json.dumps({"ok": True, "action": name}))


def notify():
    values = environment(); admin_id = values.get("TELEGRAM_ADMIN_ID", "")
    if not admin_id.isdigit() or int(admin_id) <= 0: raise RuntimeError("Telegram ID администратора не настроен")
    telegram(values, "sendMessage", {"chat_id": admin_id, "text": "✅ Тест панели управления ботом HOME-VPN выполнен успешно."})
    print(json.dumps({"ok": True}, ensure_ascii=False))


def main():
    if os.geteuid() != 0: raise SystemExit("run as root")
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status"); command = sub.add_parser("action"); command.add_argument("name", choices=("start", "stop", "restart")); sub.add_parser("notify"); sub.add_parser("get-menu"); save = sub.add_parser("save-menu"); save.add_argument("json")
    args = parser.parse_args()
    if args.command == "status": status()
    elif args.command == "action": action(args.name)
    elif args.command == "notify": notify()
    elif args.command == "get-menu": print_menu()
    else: save_menu(args.json)


if __name__ == "__main__": main()
