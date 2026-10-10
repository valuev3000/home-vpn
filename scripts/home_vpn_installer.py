#!/usr/bin/env python3
"""Shared component lifecycle backend used by HOME-VPN CLI wrappers."""

import json
import pathlib
import shutil
import subprocess
import urllib.parse
import urllib.request
import os

from home_vpn_config import SOURCE_ROOT, parse_env


MANIFEST_FILE = next((path for path in (
    SOURCE_ROOT / "config/component-manifests.json",
    pathlib.Path("/usr/local/share/home-vpn/component-manifests.json"),
) if path.is_file()), SOURCE_ROOT / "config/component-manifests.json")


def manifests():
    return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))


def removal_plan(component, purge=False):
    manifest = manifests().get(component)
    if not manifest: raise ValueError(f"Компонент {component!r} не имеет manifest владения")
    return {"services": list(manifest.get("services") or []), "paths": list(manifest.get("runtime") or []) + (list(manifest.get("data") or []) if purge else [])}


def copy_owned(source, target, mode=0o644):
    """Copy a project-owned file with deterministic target permissions."""
    target=pathlib.Path(target);shutil.copy2(source,target);os.chmod(target,mode)
    if os.geteuid()==0:os.chown(target,0,0)


def install_component_files(component, root=SOURCE_ROOT):
    """Install/update owned executable files without touching data or secrets."""
    root=pathlib.Path(root)
    pathlib.Path("/usr/local/share/home-vpn").mkdir(parents=True,exist_ok=True)
    for source,name in ((root/"config/config.example.json","config.example.json"),(root/"config/vpn-bot-menu.example.json","vpn-bot-menu.example.json"),(root/"config/component-manifests.json","component-manifests.json"),(root/"VERSION","VERSION")):
        copy_owned(source,pathlib.Path("/usr/local/share/home-vpn")/name)
    for name in ("home_vpn_config.py","home_vpn_installer.py"):
        copy_owned(root/"scripts"/name,pathlib.Path("/usr/local/sbin")/name)
    if component=="site":
        pathlib.Path("/opt/vpn-shop").mkdir(parents=True,exist_ok=True);pathlib.Path("/srv/home-vpn/assets").mkdir(parents=True,exist_ok=True)
        copy_owned(root/"vpn-shop/app.py","/opt/vpn-shop/app.py");copy_owned(root/"vpn-shop/vpn-shop.service","/etc/systemd/system/vpn-shop.service")
        for item in (root/"vpn-shop/static/original").glob("*"):copy_owned(item,pathlib.Path("/srv/home-vpn/assets")/item.name)
        for source,target,mode in (("home-vpn-healthcheck.sh","/usr/local/sbin/home-vpn-healthcheck",0o700),("home-vpn-system-check.py","/usr/local/sbin/home-vpn-system-check",0o700),("home-vpn-state-backup.py","/usr/local/sbin/home-vpn-state-backup",0o700)):
            copy_owned(root/"scripts"/source,target,mode)
        for name in ("home-vpn-healthcheck.service","home-vpn-healthcheck.timer","home-vpn-system-check.service","home-vpn-system-check.timer","home-vpn-site-backup.service","home-vpn-site-backup.timer"):
            copy_owned(root/"scripts"/name,pathlib.Path("/etc/systemd/system")/name)
        if pathlib.Path("/usr/local/bin/kit").exists():
            copy_owned(root/"scripts/home-vpn-kit-backup","/usr/local/sbin/home-vpn-kit-backup",0o700)
            for name in ("home-vpn-kit-backup.service","home-vpn-kit-backup.timer"):copy_owned(root/"scripts"/name,pathlib.Path("/etc/systemd/system")/name)
    elif component=="bot":
        pathlib.Path("/opt/vpn-bot").mkdir(parents=True,exist_ok=True)
        for source,target,mode in ((root/"vpn-bot/bot.py",pathlib.Path("/opt/vpn-bot/bot.py"),0o755),(root/"vpn-bot/admin.py",pathlib.Path("/opt/vpn-bot/admin.py"),0o755),(root/"vpn-bot/admin-control.py",pathlib.Path("/usr/local/sbin/vpn-bot-admin-control"),0o700),(root/"scripts/home-vpn-state-backup.py",pathlib.Path("/usr/local/sbin/home-vpn-state-backup"),0o700)):
            copy_owned(source,target,mode)
        shutil.copytree(root/"vpn-bot/assets","/opt/vpn-bot/assets",dirs_exist_ok=True)
        for name in ("vpn-bot.service","vpn-bot-admin.service"):copy_owned(root/"vpn-bot"/name,pathlib.Path("/etc/systemd/system")/name)
        copy_owned(root/"vpn-bot/vpn-bot-admin-sudoers","/etc/sudoers.d/vpn-bot-admin",0o440)
        for name in ("home-vpn-bot-backup.service","home-vpn-bot-backup.timer"):copy_owned(root/"scripts"/name,pathlib.Path("/etc/systemd/system")/name)
    else:raise ValueError(f"Общий backend не знает компонент {component!r}")


def cleanup_bot_egress_clients(ownership_path=pathlib.Path("/etc/home-vpn/bot-egress-ownership.json"), site_env_path=pathlib.Path("/etc/vpn-shop.env")):
    """Delete only deterministic service clients recorded by our installer."""
    if not ownership_path.is_file(): return []
    data=json.loads(ownership_path.read_text(encoding="utf-8"));environment=parse_env(site_env_path.read_text(encoding="utf-8")) if site_env_path.is_file() else {};removed=[]
    for item in data.get("clients") or []:
        email=str(item.get("email") or "")
        if not email.startswith("homevpn-bot-egress-"):continue
        token=environment.get(str(item.get("token_env") or ""),"");panel_url=str(item.get("panel_url") or "").rstrip("/")
        if not token or not panel_url:continue
        request=urllib.request.Request(panel_url+"/panel/api/clients/del/"+urllib.parse.quote(email),data=b"{}",method="POST",headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=20) as response:result=json.load(response)
        if result.get("success",True):removed.append(email)
    return removed


def remove_owned_component(component,purge=False):
    plan=removal_plan(component,purge)
    if component=="bot":
        try:cleanup_bot_egress_clients()
        except Exception as error:print("Предупреждение: служебные XHTTP-клиенты будут повторно очищены при доступности нод:",error)
    for service in plan["services"]:subprocess.run(["systemctl","disable","--now",service],check=False)
    for raw in plan["paths"]:
        path=pathlib.Path(raw)
        if path.is_dir() and not path.is_symlink():shutil.rmtree(path,ignore_errors=True)
        else:path.unlink(missing_ok=True)
    subprocess.run(["systemctl","daemon-reload"],check=False)
    if component=="site" and shutil.which("nginx"):
        if subprocess.run(["nginx","-t"],check=False).returncode==0:subprocess.run(["systemctl","reload","nginx"],check=False)
    return plan["paths"]
