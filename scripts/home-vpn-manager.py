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

def source_root():
    if os.environ.get("HOME_VPN_SOURCE_ROOT"):
        return pathlib.Path(os.environ["HOME_VPN_SOURCE_ROOT"])
    marker = pathlib.Path("/etc/home-vpn/source-root")
    if marker.exists():
        return pathlib.Path(marker.read_text(encoding="utf-8").strip())
    return pathlib.Path(__file__).resolve().parents[1]


ROOT = source_root()
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "0.0.3-pre"
COMPONENTS = ("site", "bot", "domain", "xui", "placeholder", "mail", "backup", "backup-agent")
LABELS = {"site":"Сайт и API", "bot":"Telegram-бот и панель", "domain":"Домен и TLS", "xui":"VLESS inbound в 3x-ui",
          "placeholder":"Сайт-заглушка", "mail":"SMTP/IMAP", "backup":"Backup-сервер и GUI",
          "backup-agent":"Backup-агент рабочего сервера"}


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
        "backup": pathlib.Path("/usr/local/sbin/home-vpn-backup-receive").exists(),
        "backup-agent": pathlib.Path("/etc/home-vpn-full-backup.json").exists() and pathlib.Path("/usr/local/sbin/home-vpn-full-backup").exists(),
    }


def status():
    state = installed(); services={"site":"vpn-shop","bot":"vpn-bot","placeholder":"home-vpn-placeholder","mail":"postfix","backup":"home-vpn-backup-panel","backup-agent":"home-vpn-full-backup.timer","xui":"x-ui"}
    print(f"HOME-VPN {VERSION} · состояние компонентов")
    for key in COMPONENTS:
        service=services.get(key);runtime=(" · работает" if active(service) else " · остановлен") if service and state[key] else ""
        print(f" {'✓' if state[key] else '–'} {LABELS[key]}: {'установлен' if state[key] else 'нет'}{runtime}")


def ensure_source():
    if not (ROOT/"vpn-shop/app.py").exists(): raise SystemExit("Запустите install.sh из каталога распакованного проекта")


def install_placeholder():
    ensure_source(); title=ask("Название заглушки", "Домашняя приватная сеть"); main_url=ask("Ссылка на основной сайт", "https://my.domain.ru")
    port=ask("Локальный порт заглушки", "8181")
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
    if state[name] and name not in ("domain","xui","backup"):
        print(f"{LABELS[name]} уже установлен. Используйте обновление или перенастройку."); return
    if name=="site":
        environment=os.environ.copy();environment["HOME_VPN_INSTALL_BOT"]="0"
        run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"install",env=environment)
    elif name=="bot": run("python3",str(ROOT/"scripts/home-vpn-components.py"),"bot")
    elif name=="domain": run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"tls")
    elif name=="xui": run("python3",str(ROOT/"scripts/home-vpn-xui.py"),"configure")
    elif name=="placeholder": install_placeholder()
    elif name=="mail": run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")
    elif name=="backup":
        if state["backup"]:
            if pathlib.Path("/etc/systemd/system/home-vpn-backup-panel.service").exists():print("Backup-приёмник и панель уже установлены.")
            else:
                panel_ip=ask("IP для сертификата панели","192.168.1.53");panel_host=ask("Будущий публичный домен или IP","backup.my.domain.ru")
                run("python3",str(ROOT/"backup-node/install-backup-panel.py"),"--ip",panel_ip,"--public-host",panel_host)
        else:run("python3",str(ROOT/"scripts/home-vpn-components.py"),"backup")
    elif name=="backup-agent":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"backup-agent")


def update_site():
    ensure_source()
    if not installed()["site"]: raise RuntimeError("Сайт ещё не установлен")
    run("systemctl","start","home-vpn-backup.service",check=False)
    shutil.copy2(ROOT/"vpn-shop/app.py","/opt/vpn-shop/app.py");shutil.copy2(ROOT/"vpn-shop/vpn-shop.service","/etc/systemd/system/vpn-shop.service")
    pathlib.Path("/srv/home-vpn/assets").mkdir(parents=True,exist_ok=True)
    for item in (ROOT/"vpn-shop/static/original").glob("*"):shutil.copy2(item,pathlib.Path("/srv/home-vpn/assets")/item.name)
    for source,target,mode in (("home-vpn-backup.py","/usr/local/sbin/home-vpn-backup",0o700),("home-vpn-backup-control.py","/usr/local/sbin/home-vpn-backup-control",0o700),("home-vpn-healthcheck.sh","/usr/local/sbin/home-vpn-healthcheck",0o700),("home-vpn-system-check.py","/usr/local/sbin/home-vpn-system-check",0o700)):
        shutil.copy2(ROOT/"scripts"/source,target);os.chmod(target,mode)
    run("systemctl","daemon-reload");run("systemctl","restart","vpn-shop")


def update_bot():
    ensure_source()
    if not installed()["bot"]: raise RuntimeError("Бот ещё не установлен")
    for source,target,mode in ((ROOT/"vpn-bot/bot.py",pathlib.Path("/opt/vpn-bot/bot.py"),0o755),(ROOT/"vpn-bot/admin.py",pathlib.Path("/opt/vpn-bot/admin.py"),0o755),(ROOT/"vpn-bot/admin-control.py",pathlib.Path("/usr/local/sbin/vpn-bot-admin-control"),0o700)):
        shutil.copy2(source,target);os.chmod(target,mode)
    shutil.copy2(ROOT/"vpn-bot/vpn-bot.service","/etc/systemd/system/vpn-bot.service");shutil.copy2(ROOT/"vpn-bot/vpn-bot-admin.service","/etc/systemd/system/vpn-bot-admin.service")
    run("systemctl","daemon-reload");run("systemctl","restart","vpn-bot","vpn-bot-admin")


def update_backup():
    ensure_source()
    if not installed()["backup"]:raise RuntimeError("Backup-приёмник ещё не установлен")
    shutil.copy2(ROOT/"backup-node/home-vpn-backup-receive.py","/usr/local/sbin/home-vpn-backup-receive");os.chmod("/usr/local/sbin/home-vpn-backup-receive",0o755)
    if pathlib.Path("/etc/systemd/system/home-vpn-backup-panel.service").exists():
        pathlib.Path("/opt/home-vpn-backup-panel").mkdir(parents=True,exist_ok=True)
        pathlib.Path("/srv/home-vpn-backups-removed").mkdir(parents=True,exist_ok=True);os.chmod("/srv/home-vpn-backups-removed",0o700)
        shutil.copy2(ROOT/"backup-node/home-vpn-backup-panel.py","/opt/home-vpn-backup-panel/panel.py")
        shutil.copy2(ROOT/"backup-node/home-vpn-backup-panel-control.py","/usr/local/sbin/home-vpn-backup-panel-control");os.chmod("/usr/local/sbin/home-vpn-backup-panel-control",0o700)
        shutil.copy2(ROOT/"backup-node/home-vpn-backup-panel.service","/etc/systemd/system/home-vpn-backup-panel.service")
        shutil.copy2(ROOT/"backup-node/home-vpn-backup-panel-sudoers","/etc/sudoers.d/home-vpn-backup-panel");os.chmod("/etc/sudoers.d/home-vpn-backup-panel",0o440);run("visudo","-cf","/etc/sudoers.d/home-vpn-backup-panel")
        pathlib.Path("/etc/ssh/sshd_config.d/90-home-vpn-backup.conf").write_text("Match User homevpnbackup\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    AuthenticationMethods publickey\n    AllowTcpForwarding no\n    X11Forwarding no\n    PermitTunnel no\n    GatewayPorts no\n",encoding="utf-8")
        run("passwd","-d","homevpnbackup");run("sshd","-t");run("systemctl","daemon-reload");run("systemctl","restart","ssh");run("systemctl","restart","home-vpn-backup-panel")
    else:print("Приёмник обновлён. Для установки GUI выберите установку компонента backup ещё раз.")


def update_backup_agent():
    ensure_source()
    if not installed()["backup-agent"]:raise RuntimeError("Backup-агент ещё не установлен")
    for source,target in (("home-vpn-full-backup.py","/usr/local/sbin/home-vpn-full-backup"),("home-vpn-full-restore.py","/usr/local/sbin/home-vpn-full-restore")):
        shutil.copy2(ROOT/"backup-node"/source,target);os.chmod(target,0o700)
    shutil.copy2(ROOT/"backup-node/home-vpn-full-backup.service","/etc/systemd/system/home-vpn-full-backup.service")
    shutil.copy2(ROOT/"backup-node/home-vpn-full-backup.timer","/etc/systemd/system/home-vpn-full-backup.timer")
    run("systemctl","daemon-reload");run("systemctl","enable","--now","home-vpn-full-backup.timer")
    print("Backup-агент обновлён; профиль и SSH-ключ сохранены.")


def update_component(name):
    if name=="site": update_site()
    elif name=="bot": update_bot()
    elif name=="placeholder":
        if not installed()["placeholder"]: raise RuntimeError("Заглушка ещё не установлена")
        shutil.copy2(ROOT/"scripts/home-vpn-placeholder.py","/opt/home-vpn-placeholder/server.py");run("systemctl","restart","home-vpn-placeholder")
    elif name in ("domain","xui"): install_component(name)
    elif name=="mail":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")
    elif name=="backup":update_backup()
    elif name=="backup-agent":update_backup_agent()


def configure_component(name):
    if name=="site": run("python3",str(ROOT/"scripts/home-vpn-setup.py"),"configure")
    elif name=="domain": install_component("domain")
    elif name=="xui": install_component("xui")
    elif name=="placeholder": install_placeholder()
    elif name=="bot": print("Откройте отдельную веб-панель бота → «Меню и приветствие».")
    elif name=="mail":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"mail")
    elif name=="backup":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"backup")
    elif name=="backup-agent":run("python3",str(ROOT/"scripts/home-vpn-components.py"),"backup-agent")


def publish_manager():
    if not (ROOT/"scripts/home-vpn-manager.py").exists():return
    pathlib.Path("/etc/home-vpn").mkdir(parents=True,exist_ok=True)
    pathlib.Path("/etc/home-vpn/source-root").write_text(str(ROOT.resolve())+"\n",encoding="utf-8");os.chmod("/etc/home-vpn/source-root",0o644)
    shutil.copy2(ROOT/"scripts/home-vpn-manager.py","/usr/local/sbin/home-vpn-manager");os.chmod("/usr/local/sbin/home-vpn-manager",0o750)
    shutil.copy2(ROOT/"scripts/home-vpn-xui.py","/usr/local/sbin/home-vpn-xui");os.chmod("/usr/local/sbin/home-vpn-xui",0o750)


def remove_files(paths):
    for raw in paths:
        path=pathlib.Path(raw)
        if path.is_dir() and not path.is_symlink(): shutil.rmtree(path,ignore_errors=True)
        else:path.unlink(missing_ok=True)


def remove_component(name,purge=False):
    if name=="site":
        run("systemctl","start","home-vpn-backup.service",check=False)
        units=("vpn-shop","home-vpn-backup.timer","home-vpn-healthcheck.timer","home-vpn-system-check.timer")
        for unit in units:run("systemctl","disable","--now",unit,check=False)
        remove_files(["/opt/vpn-shop","/etc/systemd/system/vpn-shop.service","/etc/sudoers.d/home-vpn-backup","/usr/local/sbin/home-vpn-backup","/usr/local/sbin/home-vpn-backup-control","/usr/local/sbin/home-vpn-restore","/usr/local/sbin/home-vpn-healthcheck","/usr/local/sbin/home-vpn-system-check"]+[f"/etc/systemd/system/{x}" for x in ("home-vpn-backup.service","home-vpn-backup.timer","home-vpn-healthcheck.service","home-vpn-healthcheck.timer","home-vpn-system-check.service","home-vpn-system-check.timer")])
        if purge:remove_files(["/var/lib/vpn-shop","/etc/vpn-shop.env","/etc/home-vpn","/var/backups/home-vpn"])
    elif name=="bot":
        for unit in ("vpn-bot","vpn-bot-admin"):run("systemctl","disable","--now",unit,check=False)
        remove_files(["/opt/vpn-bot","/etc/systemd/system/vpn-bot.service","/etc/systemd/system/vpn-bot-admin.service","/etc/sudoers.d/vpn-bot-admin","/usr/local/sbin/vpn-bot-admin-control"])
        if purge:remove_files(["/var/lib/vpn-bot","/etc/vpn-bot.env","/etc/vpn-bot-admin.env","/etc/vpn-bot-menu.json"])
    elif name=="placeholder":
        run("systemctl","disable","--now","home-vpn-placeholder",check=False);remove_files(["/opt/home-vpn-placeholder","/etc/systemd/system/home-vpn-placeholder.service","/etc/home-vpn-placeholder.env"])
        if purge:remove_files(["/etc/home-vpn/placeholder.html"])
    elif name=="backup":
        run("systemctl","disable","--now","home-vpn-backup-panel",check=False)
        remove_files(["/usr/local/sbin/home-vpn-backup-receive","/usr/local/sbin/home-vpn-backup-panel-control","/opt/home-vpn-backup-panel","/etc/systemd/system/home-vpn-backup-panel.service","/etc/sudoers.d/home-vpn-backup-panel","/etc/ssh/sshd_config.d/90-home-vpn-backup.conf","/var/lib/homevpnbackup/.ssh/authorized_keys"])
        if purge:remove_files(["/srv/home-vpn-backups","/srv/home-vpn-backups-removed","/etc/home-vpn-backup-panel","/etc/home-vpn-backup-panel.env","/root/home-vpn-backup-panel-credentials.txt"])
        run("systemctl","restart","ssh",check=False)
    elif name=="backup-agent":
        run("systemctl","disable","--now","home-vpn-full-backup.timer",check=False)
        remove_files(["/usr/local/sbin/home-vpn-full-backup","/usr/local/sbin/home-vpn-full-restore","/etc/systemd/system/home-vpn-full-backup.service","/etc/systemd/system/home-vpn-full-backup.timer"])
        if purge:remove_files(["/etc/home-vpn-full-backup.json","/etc/home-vpn-full-backup-known-hosts","/var/backups/home-vpn-full"])
    elif name=="mail":
        print("Почтовые пакеты и пользователь не удаляются автоматически, чтобы не потерять чужую почту.")
        remove_files(["/etc/dovecot/conf.d/99-home-vpn.conf","/etc/fail2ban/jail.d/home-vpn-mail.local","/etc/systemd/system/postfix.service.d/lxc.conf","/etc/systemd/system/dovecot.service.d/lxc.conf"])
        if purge:remove_files(["/etc/home-vpn-mail","/root/home-vpn-mail-credentials.txt"])
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
