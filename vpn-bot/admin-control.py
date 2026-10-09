#!/usr/bin/env python3
"""Restricted root helper for the standalone Telegram bot control panel."""

import argparse
import grp
import json
import os
import pathlib
import subprocess
import ssl
import tempfile
import sys
import urllib.error
import urllib.parse
import urllib.request


ENV_FILE = pathlib.Path("/etc/vpn-bot.env")


def telegram_open(request, values, timeout=15):
    proxy = values.get("TELEGRAM_PROXY_URL", "http://127.0.0.1:18080").strip()
    parsed=urllib.parse.urlparse(proxy)
    if parsed.scheme not in ("http","https") or parsed.hostname not in ("127.0.0.1","::1","localhost"):
        raise RuntimeError("Прямой Telegram egress запрещён: настройте loopback TELEGRAM_PROXY_URL")
    # Do not inherit a broad NO_PROXY from the administrator environment:
    # Telegram notifications must fail closed through the configured egress.
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    return opener.open(request, timeout=timeout)
def load_default_menu():
    for path in (pathlib.Path(__file__).resolve().parents[1]/"config/vpn-bot-menu.example.json",pathlib.Path("/usr/local/share/home-vpn/vpn-bot-menu.example.json")):
        try:return json.loads(path.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):pass
    raise RuntimeError("Не найден единый default Telegram menu")
DEFAULT_MENU=load_default_menu()


def environment():
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1); values[key] = value
    return values


def print_settings():
    values = environment()
    print(json.dumps({
        "admin_id": values.get("TELEGRAM_ADMIN_ID", ""),
        "token_configured": bool(values.get("TELEGRAM_BOT_TOKEN")),
    }, ensure_ascii=False))


def write_environment(values):
    handle, temporary = tempfile.mkstemp(prefix=".vpn-bot-env-", dir=str(ENV_FILE.parent), text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            for key, value in values.items():
                value = str(value)
                if "\n" in value or "\r" in value: raise RuntimeError("Недопустимое значение настройки")
                output.write(f"{key}={value}\n")
        os.chmod(temporary, 0o600); os.chown(temporary, 0, 0); os.replace(temporary, ENV_FILE)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def sync_site_admin(values, admin_id):
    url=values.get("SHOP_API_URL", "").rstrip("/")+"/api/bot/admin-settings"
    secret=values.get("SHOP_API_SECRET", "")
    if not url.startswith(("http://","https://")) or not secret:raise RuntimeError("Связь с API сайта не настроена")
    request=urllib.request.Request(url,json.dumps({"telegram_id":int(admin_id)}).encode(),headers={"X-API-Key":secret,"Content-Type":"application/json"},method="POST")
    context=ssl._create_unverified_context() if url.startswith("https://") else None
    with urllib.request.urlopen(request,timeout=15,context=context) as response:
        result=json.load(response)
    if not result.get("ok"):raise RuntimeError("Сайт не подтвердил новый Admin ID")


def site_catalog(values):
    url=values.get("SHOP_API_URL","").rstrip("/")+"/api/bot/catalog"
    request=urllib.request.Request(url,headers={"X-API-Key":values.get("SHOP_API_SECRET","")})
    context=ssl._create_unverified_context() if url.startswith("https://") else None
    with urllib.request.urlopen(request,timeout=10,context=context) as response:return json.load(response)


def save_settings(raw):
    try: incoming = json.loads(raw)
    except (ValueError, TypeError) as error: raise RuntimeError("Некорректные настройки") from error
    admin_id = str(incoming.get("admin_id") or "").strip()
    token = str(incoming.get("token") or "").strip()
    if not admin_id.isdigit() or int(admin_id) <= 0: raise RuntimeError("Telegram Admin ID должен быть положительным числом")
    if token and (len(token) < 20 or len(token) > 200 or any(character.isspace() for character in token)):
        raise RuntimeError("Некорректный Secret key / Bot Token")
    previous = ENV_FILE.read_bytes(); values = environment();previous_admin=values.get("TELEGRAM_ADMIN_ID","")
    values["TELEGRAM_ADMIN_ID"] = admin_id
    if token:
        candidate = dict(values); candidate["TELEGRAM_BOT_TOKEN"] = token
        account = telegram(candidate, "getMe")
        if not account or not account.get("username"): raise RuntimeError("Telegram не подтвердил Bot Token")
        values["TELEGRAM_BOT_TOKEN"] = token
    try:
        sync_site_admin(values,admin_id)
        write_environment(values)
        subprocess.run(["systemctl", "restart", "vpn-bot"], check=True, timeout=30)
    except Exception:
        ENV_FILE.write_bytes(previous); os.chmod(ENV_FILE, 0o600)
        if previous_admin.isdigit() and int(previous_admin)>0:
            try:sync_site_admin(values,previous_admin)
            except Exception:pass
        subprocess.run(["systemctl", "restart", "vpn-bot"], check=False, timeout=30)
        raise
    print(json.dumps({"ok": True, "token_changed": bool(token)}, ensure_ascii=False))


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
    try:
        with telegram_open(request, values, timeout=15) as response:result = json.load(response)
    except urllib.error.HTTPError as error:
        try:result=json.loads(error.read().decode())
        except Exception:result={}
        description=str(result.get("description") or f"Telegram HTTP {error.code}")
        retry_after=int((result.get("parameters") or {}).get("retry_after") or 0)
        if error.code==429:raise RuntimeError(f"Telegram временно ограничил запросы. Повторите через {retry_after or 'несколько'} сек.") from None
        if "chat not found" in description.lower():raise RuntimeError("Telegram не видит чат администратора. Откройте текущего бота в Telegram, нажмите Start и проверьте Telegram Admin ID в разделе «Доступ».") from None
        raise RuntimeError(description) from None
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
    try:
        catalog=site_catalog(values);result.update({"catalog_ok":True,"tariff_count":len(catalog.get("tariffs",[])),"catalog_currency":catalog.get("currency","")})
    except Exception as error:result.update({"catalog_ok":False,"catalog_error":str(error)[:300]})
    print(json.dumps(result, ensure_ascii=False))


def action(name):
    if name not in {"start", "stop", "restart"}: raise SystemExit("invalid action")
    subprocess.run(["systemctl", name, "vpn-bot"], check=True, timeout=30)
    print(json.dumps({"ok": True, "action": name}))


def notify():
    values = environment(); admin_id = values.get("TELEGRAM_ADMIN_ID", "")
    if not admin_id.isdigit() or int(admin_id) <= 0: raise RuntimeError("Telegram ID администратора не настроен")
    # A panel check is deliberately point-to-point: never use a channel,
    # subscriber list, linked debug profile or any recipient from a request.
    telegram(values, "sendMessage", {"chat_id": admin_id, "text": "✅ Тест панели управления ботом HOME-VPN выполнен успешно."})
    print(json.dumps({"ok": True, "recipient": "administrator"}, ensure_ascii=False))


def main():
    if os.geteuid() != 0: raise SystemExit("run as root")
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status"); command = sub.add_parser("action"); command.add_argument("name", choices=("start", "stop", "restart")); sub.add_parser("notify"); sub.add_parser("get-menu"); sub.add_parser("get-settings"); save = sub.add_parser("save-menu"); save.add_argument("json"); settings = sub.add_parser("save-settings"); settings.add_argument("json")
    args = parser.parse_args()
    if args.command == "status": status()
    elif args.command == "action": action(args.name)
    elif args.command == "notify": notify()
    elif args.command == "get-menu": print_menu()
    elif args.command == "get-settings": print_settings()
    elif args.command == "save-settings": save_settings(args.json)
    else: save_menu(args.json)


if __name__ == "__main__":
    try:main()
    except Exception as error:print(str(error),file=sys.stderr);raise SystemExit(1)
