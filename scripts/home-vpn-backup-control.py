#!/usr/bin/env python3
"""Restricted backup control for the HOME-VPN admin UI and SSH recovery."""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time


BACKUP_DIR = pathlib.Path(os.getenv("HOME_VPN_BACKUP_DIR", "/var/backups/home-vpn"))
STATE_DIR = pathlib.Path("/var/lib/home-vpn-backup")
STATE_FILE = STATE_DIR / "status.json"
LOCK_FILE = STATE_DIR / "control.lock"
ARCHIVE_RE = re.compile(r"home-vpn-\d{8}-\d{6}\.tar\.gz\Z")
SCOPES = {"site", "xui", "all", "full"}
DATABASE_TARGETS = {
    "site": (("databases/shop.db", pathlib.Path("/var/lib/vpn-shop/shop.db"), "vpnshop"),),
    "xui": (("databases/x-ui.db", pathlib.Path("/etc/x-ui/x-ui.db"), None),),
}
CONFIG_TARGETS = (
    ("configuration/etc/home-vpn/config.json", pathlib.Path("/etc/home-vpn/config.json")),
    ("configuration/etc/vpn-shop.env", pathlib.Path("/etc/vpn-shop.env")),
    ("configuration/etc/systemd/system/vpn-shop.service", pathlib.Path("/etc/systemd/system/vpn-shop.service")),
    ("configuration/etc/systemd/system/vpn-shop.service.d", pathlib.Path("/etc/systemd/system/vpn-shop.service.d")),
    ("configuration/etc/x-ui/cert", pathlib.Path("/etc/x-ui/cert")),
)


def write_state(status, action, message, **extra):
    STATE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    payload = {"status": status, "action": action, "message": message, "updated_at": int(time.time()), **extra}
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o644)
    os.replace(temporary, STATE_FILE)


def archive_path(name):
    if not ARCHIVE_RE.fullmatch(name or ""):
        raise ValueError("invalid backup filename")
    path = BACKUP_DIR / name
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError("backup not found")
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_members(bundle):
    members = bundle.getmembers()
    for member in members:
        pure = pathlib.PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk() or member.isdev():
            raise RuntimeError("unsafe archive member")
        if pure.parts and pure.parts[0] not in {"manifest.json", "databases", "configuration"}:
            raise RuntimeError("unexpected archive member")
    return members


def extract_verified(archive, destination):
    with tarfile.open(archive, "r:gz") as bundle:
        members = safe_members(bundle)
        try:
            bundle.extractall(destination, members=members, filter="data")
        except TypeError:
            bundle.extractall(destination, members=members)
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    if not isinstance(files, dict) or not files:
        raise RuntimeError("manifest is invalid")
    for relative, expected in files.items():
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise RuntimeError("invalid manifest path")
        source = destination / pathlib.Path(*pure.parts)
        if not source.is_file() or digest(source) != expected:
            raise RuntimeError(f"checksum mismatch: {relative}")
    return manifest


def sqlite_ok(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {path}")


def list_archives():
    items = []
    for path in sorted(BACKUP_DIR.glob("home-vpn-*.tar.gz"), reverse=True):
        if not ARCHIVE_RE.fullmatch(path.name) or not path.is_file():
            continue
        item = {"name": path.name, "size": path.stat().st_size, "created_at": int(path.stat().st_mtime), "valid": False}
        try:
            with tempfile.TemporaryDirectory(prefix="home-vpn-verify-") as temporary_name:
                temporary = pathlib.Path(temporary_name); extract_verified(path, temporary)
                item["valid"] = all((temporary / relative).is_file() for relative in ("databases/shop.db", "databases/x-ui.db"))
                if item["valid"]:
                    sqlite_ok(temporary / "databases/shop.db"); sqlite_ok(temporary / "databases/x-ui.db")
        except (OSError, tarfile.TarError, RuntimeError, ValueError, json.JSONDecodeError, sqlite3.Error):
            pass
        items.append(item)
    state = {}
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    print(json.dumps({"archives": items, "state": state}, ensure_ascii=False))


def agent_status():
    def active(unit):
        return subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0
    details={"local_timer":active("home-vpn-backup.timer"),"local_service":active("home-vpn-backup.service"),"full_agent_installed":pathlib.Path("/usr/local/sbin/home-vpn-full-backup").exists(),"full_agent_timer":active("home-vpn-full-backup.timer"),"remote_configured":False,"server_id":"","remote":""}
    legacy=pathlib.Path("/etc/home-vpn-backup.env")
    if legacy.exists():
        values={}
        for line in legacy.read_text(encoding="utf-8").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key,value=line.split("=",1);values[key]=value
        details["remote_configured"]=bool(values.get("HOME_VPN_BACKUP_REMOTE"));details["remote"]=values.get("HOME_VPN_BACKUP_REMOTE","")
    full=pathlib.Path("/etc/home-vpn-full-backup.json")
    if full.exists():
        try:
            data=json.loads(full.read_text(encoding="utf-8"));details["server_id"]=str(data.get("server_id") or data.get("id") or "");details["remote"]=str(data.get("remote") or data.get("backup_server") or details["remote"]);details["remote_configured"]=bool(details["remote"])
        except (OSError,json.JSONDecodeError):details["config_error"]=True
    print(json.dumps(details,ensure_ascii=False))


def copy_database(source, target, owner_name=None):
    sqlite_ok(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    old_stat = target.stat() if target.exists() else None
    temporary = target.with_name("." + target.name + ".restore")
    shutil.copy2(source, temporary)
    sqlite_ok(temporary)
    if owner_name:
        account = pwd.getpwnam(owner_name)
        os.chown(temporary, account.pw_uid, account.pw_gid)
    elif old_stat:
        os.chown(temporary, old_stat.st_uid, old_stat.st_gid)
    os.chmod(temporary, old_stat.st_mode & 0o777 if old_stat else 0o600)
    os.replace(temporary, target)


def copy_config(source, target):
    if not source.exists():
        return
    if source.is_dir():
        temporary = target.with_name("." + target.name + ".restore")
        shutil.rmtree(temporary, ignore_errors=True)
        shutil.copytree(source, temporary, symlinks=False)
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name("." + target.name + ".restore")
        shutil.copy2(source, temporary)
        if target.exists():
            stat = target.stat(); os.chown(temporary, stat.st_uid, stat.st_gid); os.chmod(temporary, stat.st_mode & 0o777)
        os.replace(temporary, target)


def create_backup():
    STATE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_state("running", "create", "Создаётся резервная копия")
        try:
            subprocess.run(["systemctl", "start", "home-vpn-backup.service"], check=True, timeout=600)
            latest = max(BACKUP_DIR.glob("home-vpn-*.tar.gz"), key=lambda path: path.stat().st_mtime)
            archive = latest.name
            write_state("success", "create", "Резервная копия создана", archive=archive)
        except Exception as error:
            write_state("error", "create", str(error)[:500])
            raise


def restore_backup(name, scope):
    if scope not in SCOPES:
        raise ValueError("invalid restore scope")
    archive = archive_path(name)
    STATE_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write_state("running", "restore", "Проверка архива и создание страховочной копии", archive=name, scope=scope)
        services = []
        try:
            with tempfile.TemporaryDirectory(prefix="home-vpn-restore-") as temporary_name:
                temporary = pathlib.Path(temporary_name)
                extract_verified(archive, temporary)
                needed = []
                if scope in {"site", "all", "full"}: needed.extend(DATABASE_TARGETS["site"]); services.append("vpn-shop")
                if scope in {"xui", "all", "full"}: needed.extend(DATABASE_TARGETS["xui"]); services.append("x-ui")
                for relative, _, _ in needed:
                    if not (temporary / relative).is_file(): raise RuntimeError(f"required file is missing: {relative}")
                    sqlite_ok(temporary / relative)
                subprocess.run(["systemctl", "start", "home-vpn-backup.service"], check=True, timeout=600)
                write_state("running", "restore", "Службы остановлены, выполняется восстановление", archive=name, scope=scope)
                for service in services: subprocess.run(["systemctl", "stop", service], check=True)
                for relative, target, owner in needed: copy_database(temporary / relative, target, owner)
                if scope == "full":
                    for relative, target in CONFIG_TARGETS: copy_config(temporary / relative, target)
                    subprocess.run(["systemctl", "daemon-reload"], check=True)
                for service in reversed(services): subprocess.run(["systemctl", "start", service], check=True)
                write_state("success", "restore", "Восстановление завершено", archive=name, scope=scope)
        except Exception as error:
            for service in reversed(services): subprocess.run(["systemctl", "start", service], check=False)
            write_state("error", "restore", str(error)[:500], archive=name, scope=scope)
            raise


def queue(action, name=None, scope=None):
    stamp = f"{int(time.time())}-{os.getpid()}"
    command = ["systemd-run", "--collect", "--unit", f"home-vpn-backup-{action}-{stamp}", "/usr/local/sbin/home-vpn-backup-control", action]
    if name: command.append(name)
    if scope: command.append(scope)
    write_state("queued", action, "Задача поставлена в очередь", **({"archive": name, "scope": scope} if name else {}))
    subprocess.run(command, check=True, capture_output=True, text=True)
    print(json.dumps({"ok": True, "queued": True}, ensure_ascii=False))


def main():
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    sub.add_parser("agent-status")
    sub.add_parser("create")
    sub.add_parser("queue-create")
    restore = sub.add_parser("restore"); restore.add_argument("archive"); restore.add_argument("scope", choices=sorted(SCOPES))
    queued = sub.add_parser("queue-restore"); queued.add_argument("archive"); queued.add_argument("scope", choices=sorted(SCOPES))
    args = parser.parse_args()
    if args.action == "list": list_archives()
    elif args.action == "agent-status": agent_status()
    elif args.action == "create": create_backup()
    elif args.action == "queue-create": queue("create")
    elif args.action == "restore": restore_backup(args.archive, args.scope)
    else: archive_path(args.archive); queue("restore", args.archive, args.scope)


if __name__ == "__main__":
    try: main()
    except BlockingIOError: raise SystemExit("another backup operation is already running")
