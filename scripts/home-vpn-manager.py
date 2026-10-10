#!/usr/bin/env python3
"""HOME-VPN component lifecycle manager."""

import argparse
import html
import json
import os
import pathlib
import shutil
import subprocess
import sys

from home_vpn_config import PROJECT_VERSION
from home_vpn_installer import install_component_files, remove_owned_component

def source_root():
    if os.environ.get("HOME_VPN_SOURCE_ROOT"):
        return pathlib.Path(os.environ["HOME_VPN_SOURCE_ROOT"])
    marker = pathlib.Path("/etc/home-vpn/source-root")
    if marker.exists():
        return pathlib.Path(marker.read_text(encoding="utf-8").strip())
    return pathlib.Path(__file__).resolve().parents[1]


ROOT = source_root()
VERSION = PROJECT_VERSION
COMPONENTS = ("site", "bot", "domain", "xui", "placeholder", "mail")
LABELS = {"site":"Сайт и API", "bot":"Telegram-бот и панель", "domain":"Домен и TLS", "xui":"VLESS inbound в 3x-ui",
          "placeholder":"Сайт-заглушка", "mail":"SMTP/IMAP"}


def ask(prompt, default=""):
    suffix = f" [{default}]" if default else ""; value = input(f"{prompt}{suffix}: ").strip(); return value or default


def yes(prompt, default=False):
    value = ask(prompt, "Y/n" if default else "y/N").lower(); return default if value.lower() in ("y/n",) else value in ("y","yes","д","да")


def run(*args, check=True, **kwargs): return subprocess.run(args, check=check, **kwargs)


def active(name): return run("systemctl", "is-active", "--quiet", name, check=False).returncode == 0


def installed():
    return {
        "site": pathlib.Path("/etc/vpn-shop.env").exists() and pathlib.Path("/opt/vpn-shop/app.py").exists(),
        "bot": pathlib.Path("/etc/vpn-bot.env").exists() and pathlib.Path("/opt/vpn-bot/bot.py").exists(),
        "domain": pathlib.Path("/etc/home-vpn/tls/site.crt").exists(),
        "xui": pathlib.Path("/etc/systemd/system/x-ui.service").exists() or pathlib.Path("/usr/local/x-ui").exists(),
        "placeholder": pathlib.Path("/etc/systemd/system/home-vpn-placeholder.service").exists(),
        "mail": pathlib.Path("/etc/home-vpn-mail").exists() and pathlib.Path("/etc/dovecot/conf.d/99-home-vpn.conf").exists(),
    }


def status():
    state = installed(); services={"site":"vpn-shop","bot":"vpn-bot","placeholder":"home-vpn-placeholder","mail":"postfix","xui":"x-ui"}
    print(f"HOME-VPN {VERSION} · состояние компонентов")
    for key in COMPONENTS:
        service=services.get(key);runtime=(" · работает" if active(service) else " · остановлен") if service and state[key] else ""
        print(f" {'✓' if state[key] else '–'} {LABELS[key]}: {'установлен' if state[key] else 'нет'}{runtime}")


def ensure_source():
    if not (ROOT/"vpn-shop/app.py").exists(): raise SystemExit("Запустите install.sh из каталога распакованного проекта")


def install_placeholder():
    ensure_source()
    if installed()["site"]:
        raise SystemExit("На управляющей ноде с сайтом HOME-VPN заглушка запрещена")
    title=os.getenv("HOME_VPN_PLACEHOLDER_TITLE") or ask("Название заглушки", "Домашняя приватная сеть"); main_url=os.getenv("HOME_VPN_PLACEHOLDER_URL") or ask("Ссылка на основной сайт", "https://my.domain.ru")
    port=os.getenv("HOME_VPN_PLACEHOLDER_PORT") or ask("Локальный порт заглушки", "8181")
    if not port.isdigit(): raise SystemExit("Порт должен быть числом")
    pathlib.Path("/opt/home-vpn-placeholder").mkdir(parents=True, exist_ok=True); pathlib.Path("/etc/home-vpn").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/"scripts/home-vpn-placeholder.py", "/opt/home-vpn-placeholder/server.py")
    shutil.copy2(ROOT/"scripts/home-vpn-placeholder.service", "/etc/systemd/system/home-vpn-placeholder.service")
    page=(ROOT/"migration/edge-placeholder.html").read_text(encoding="utf-8").replace("HOME-VPN", html.escape(title)).replace("https://vpn.my.domain.ru", html.escape(main_url, quote=True))
    pathlib.Path("/etc/home-vpn/placeholder.html").write_text(page, encoding="utf-8")
    pathlib.Path("/etc/home-vpn-placeholder.env").write_text(f"PLACEHOLDER_BIND=127.0.0.1\nPLACEHOLDER_PORT={port}\nPLACEHOLDER_HTML=/etc/home-vpn/placeholder.html\n", encoding="utf-8")
    os.chmod("/etc/home-vpn-placeholder.env",0o644);os.chmod("/etc/home-vpn/placeholder.html",0o644)
    run("systemctl","daemon-reload");run("systemctl","enable","--now","home-vpn-placeholder")
    print(f"Заглушка работает на 127.0.0.1:{port}. Укажите этот порт как fallback при создании XHTTP.")


def install_component(name):
    ensure_source(); state=installed()
    if state[name] and name not in ("domain","xui"):
        print(f"{LABELS[name]} уже установлен. Используйте обновление или перенастройку."); return
    if name=="site":
        environment=os.environ.copy();environment["HOME_VPN_INSTALL_BOT"]="0"
        run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"install",env=environment)
    elif name=="bot": run("python3",str(ROOT/"scripts/home-vpn-components.py"),"bot")
    elif name=="domain": run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"tls")
    elif name=="xui": run("python3",str(ROOT/"scripts/home-vpn-xui.py"),"configure")
    elif name=="placeholder": install_placeholder()
    elif name=="mail": run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")


def update_site():
    ensure_source()
    if not installed()["site"]: raise RuntimeError("Сайт ещё не установлен")
    install_component_files("site",ROOT)
    run("systemctl","daemon-reload");run("systemctl","restart","vpn-shop");run("systemctl","enable","--now","home-vpn-site-backup.timer")
    if pathlib.Path("/usr/local/bin/kit").exists() or pathlib.Path("/etc/x-ui/x-ui.db").exists():run("systemctl","enable","--now","home-vpn-kit-backup.timer")


def update_bot():
    ensure_source()
    if not installed()["bot"]: raise RuntimeError("Бот ещё не установлен")
    install_component_files("bot",ROOT)
    run("systemctl","daemon-reload");run("systemctl","restart","vpn-bot","vpn-bot-admin");run("systemctl","enable","--now","home-vpn-bot-backup.timer")


def update_component(name):
    if name=="site": update_site()
    elif name=="bot": update_bot()
    elif name=="placeholder":
        if installed()["site"]:raise RuntimeError("На управляющей ноде должен работать сайт HOME-VPN, а не заглушка")
        if not installed()["placeholder"]: raise RuntimeError("Заглушка ещё не установлена")
        shutil.copy2(ROOT/"scripts/home-vpn-placeholder.py","/opt/home-vpn-placeholder/server.py");run("systemctl","restart","home-vpn-placeholder")
    elif name in ("domain","xui"): install_component(name)
    elif name=="mail":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")


def configure_component(name):
    if name=="site": run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"configure")
    elif name=="domain": install_component("domain")
    elif name=="xui": install_component("xui")
    elif name=="placeholder": install_placeholder()
    elif name=="bot": print("Откройте отдельную веб-панель бота → «Меню и приветствие».")
    elif name=="mail":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")


def publish_manager():
    if not (ROOT/"scripts/home-vpn-manager.py").exists():return
    pathlib.Path("/etc/home-vpn").mkdir(parents=True,exist_ok=True)
    pathlib.Path("/etc/home-vpn/source-root").write_text(str(ROOT.resolve())+"\n",encoding="utf-8");os.chmod("/etc/home-vpn/source-root",0o644)
    shutil.copy2(ROOT/"scripts/home-vpn-manager.py","/usr/local/sbin/home-vpn-manager");os.chmod("/usr/local/sbin/home-vpn-manager",0o750)
    shutil.copy2(ROOT/"scripts/home_vpn_config.py","/usr/local/sbin/home_vpn_config.py");os.chmod("/usr/local/sbin/home_vpn_config.py",0o644)
    shutil.copy2(ROOT/"scripts/home_vpn_installer.py","/usr/local/sbin/home_vpn_installer.py");os.chmod("/usr/local/sbin/home_vpn_installer.py",0o644)
    pathlib.Path("/usr/local/share/home-vpn").mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/"config/component-manifests.json","/usr/local/share/home-vpn/component-manifests.json")
    shutil.copy2(ROOT/"scripts/home-vpn-xui.py","/usr/local/sbin/home-vpn-xui");os.chmod("/usr/local/sbin/home-vpn-xui",0o750)


def remove_files(paths):
    for raw in paths:
        path=pathlib.Path(raw)
        if path.is_dir() and not path.is_symlink(): shutil.rmtree(path,ignore_errors=True)
        else:path.unlink(missing_ok=True)


def remove_component(name,purge=False):
    if name in ("site","bot","placeholder"):
        remove_owned_component(name,purge)
    elif name=="mail":
        print("Почтовые пакеты и пользователь не удаляются автоматически, чтобы не потерять чужую почту.")
        remove_owned_component(name,purge)
    elif name in ("domain","xui"):
        print("TLS и 3x-ui не удаляются менеджером: они могут обслуживать другие сервисы. Удалите только созданный inbound вручную в 3x-ui.")
    run("systemctl","daemon-reload",check=False)


def select_components(allow_domain=True):
    choices=list(COMPONENTS if allow_domain else [x for x in COMPONENTS if x not in ("domain","xui")])
    for index,key in enumerate(choices,1):print(f" {index}. {LABELS[key]}")
    raw=ask("Номера через запятую или all")
    if raw.lower()=="all":return choices
    selected=[]
    for part in raw.split(","):
        if part.strip().isdigit() and 0<int(part)<=len(choices):selected.append(choices[int(part)-1])
    if not selected:raise SystemExit("Компоненты не выбраны")
    return list(dict.fromkeys(selected))


def interactive():
    while True:
        print(f"\nHOME-VPN {VERSION}\n1 — Проверить установленные компоненты\n2 — Установить компоненты\n3 — Обновить компоненты из локального каталога\n4 — Изменить настройки\n5 — Полное удаление компонентов\n0 — Выход\nGitHub-обновление: временно отключено")
        choice=ask("Действие")
        if choice=="0":return
        if choice=="1":status();continue
        if choice in ("2","3","4"):
            selected=select_components()
            for name in selected:
                try:(install_component if choice=="2" else update_component if choice=="3" else configure_component)(name)
                except Exception as error:print(f"Ошибка {LABELS[name]}: {error}")
            continue
        if choice=="5":
            selected=select_components(False);print("Будут удалены: "+", ".join(LABELS[x] for x in selected))
            if ask("Для подтверждения введите DELETE HOME-VPN")!="DELETE HOME-VPN":print("Отменено");continue
            purge=yes("Удалить также базы, настройки и локальные резервные копии",False)
            for name in selected:remove_component(name,purge)
            print("Удаление завершено. 3x-ui и её inbound не изменялись.")


def main():
    parser=argparse.ArgumentParser(description=f"HOME-VPN {VERSION} manager");parser.add_argument("--version",action="version",version=VERSION);sub=parser.add_subparsers(dest="command")
    sub.add_parser("menu");sub.add_parser("status")
    for action in ("install","update","configure","remove"):
        command=sub.add_parser(action);command.add_argument("components",nargs="+",choices=COMPONENTS);command.add_argument("--purge-data",action="store_true") if action=="remove" else None
    args=parser.parse_args()
    if args.command=="status":status();return
    if os.geteuid()!=0:raise SystemExit("Запустите: sudo bash install.sh")
    publish_manager()
    if not args.command or args.command=="menu":interactive()
    elif args.command=="install":[install_component(x) for x in args.components]
    elif args.command=="update":[update_component(x) for x in args.components]
    elif args.command=="configure":[configure_component(x) for x in args.components]
    elif args.command=="remove":
        if ask("Для подтверждения введите DELETE HOME-VPN")!="DELETE HOME-VPN":raise SystemExit("Отменено")
        [remove_component(x,args.purge_data) for x in args.components]


if __name__=="__main__":main()
