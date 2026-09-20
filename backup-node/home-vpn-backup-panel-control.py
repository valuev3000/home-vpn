#!/usr/bin/env python3
"""Restricted root helper for the HOME-VPN multi-server backup panel."""

import argparse
import base64
import grp
import hashlib
import json
import os
import pathlib
import pwd
import re
import shutil
import tarfile
import tempfile
import time

REGISTRY = pathlib.Path("/etc/home-vpn-backup-panel/servers.json")
ROOT = pathlib.Path("/srv/home-vpn-backups")
DETACHED_ROOT = pathlib.Path("/srv/home-vpn-backups-removed")
AUTHORIZED = pathlib.Path("/var/lib/homevpnbackup/.ssh/authorized_keys")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
ARCHIVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}\.tar\.gz$")


def load():
    try:
        value = json.loads(REGISTRY.read_text(encoding="utf-8"))
        if not isinstance(value.get("servers"), list):return {"servers": [],"configurations":[]}
        value.setdefault("configurations",[])
        return value
    except (OSError, ValueError, AttributeError):
        return {"servers": [],"configurations":[]}


def save(data):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="servers.", dir=REGISTRY.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, grp.getgrnam("homevpnbackup").gr_gid)
        os.replace(temporary, REGISTRY)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_id(server_id):
    if not SLUG.fullmatch(server_id):
        raise RuntimeError("ID: 2–32 символа a-z, 0-9, _ или -")
    return server_id


def clean_key(raw):
    parts = raw.strip().split()
    if len(parts) < 2 or parts[0] not in ("ssh-ed25519", "ssh-rsa"):
        raise RuntimeError("Нужен публичный SSH-ключ")
    padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        base64.b64decode(padded, validate=True)
    except Exception as error:
        raise RuntimeError("Некорректный SSH-ключ") from error
    return parts[0], parts[1]


def migrate_registry_keys(data):
    """Import public keys from v0.0.1 authorized_keys without exposing secrets."""
    if not AUTHORIZED.exists():
        return False
    indexed = {}
    for line in AUTHORIZED.read_text(encoding="utf-8").splitlines():
        match = re.search(r"\s(ssh-(?:ed25519|rsa))\s+(\S+)\s+home-vpn:([a-z0-9_-]+)\s*$", line)
        if match:
            indexed[match.group(3)] = f"{match.group(1)} {match.group(2)}"
    changed = False
    for record in data.get("servers", []):
        server_id = record.get("id")
        if not record.get("public_key") and server_id in indexed:
            record["public_key"] = indexed[server_id]
            changed = True
        if "enabled" not in record:
            record["enabled"] = server_id in indexed
            changed = True
    return changed


def rewrite_key(server_id, key_type=None, key_body=None, retention=30, remove=False):
    account = pwd.getpwnam("homevpnbackup")
    AUTHORIZED.parent.mkdir(parents=True, exist_ok=True)
    lines = AUTHORIZED.read_text(encoding="utf-8").splitlines() if AUTHORIZED.exists() else []
    lines = [line for line in lines if f"home-vpn:{server_id}" not in line and (not key_body or key_body not in line)]
    if not remove:
        destination = ROOT / server_id
        destination.mkdir(parents=True, exist_ok=True)
        os.chown(destination, account.pw_uid, account.pw_gid)
        os.chmod(destination, 0o700)
        forced = (
            f'restrict,command="HOME_VPN_BACKUP_DESTINATION={destination} '
            f'HOME_VPN_BACKUP_RETENTION_DAYS={retention} '
            f'/usr/local/sbin/home-vpn-backup-receive" '
            f'{key_type} {key_body} home-vpn:{server_id}'
        )
        lines.append(forced)
    AUTHORIZED.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.chmod(AUTHORIZED, 0o600)
    os.chown(AUTHORIZED, account.pw_uid, account.pw_gid)


def add(raw):
    item = json.loads(raw)
    server_id = validate_id(str(item.get("id") or "").lower())
    data = load()
    if any(record.get("id") == server_id for record in data.get("servers", [])):
        raise RuntimeError("Сервер с таким ID уже существует")
    key_type, key_body = clean_key(str(item.get("public_key") or ""))
    retention = max(1, min(3650, int(item.get("retention_days") or 30)))
    padded = key_body + "=" * ((4 - len(key_body) % 4) % 4)
    record = {
        "id": server_id,
        "name": str(item.get("name") or server_id).strip()[:80],
        "role": str(item.get("role") or "node").strip()[:40],
        "expected_host": str(item.get("expected_host") or "").strip()[:255],
        "retention_days": retention,
        "created_at": int(time.time()),
        "enabled": True,
        "public_key": f"{key_type} {key_body}",
        "key_fingerprint": hashlib.sha256(base64.b64decode(padded)).hexdigest()[:16],
    }
    configuration_id=str(item.get("configuration_id") or "").strip()
    if configuration_id:
        validate_id(configuration_id)
        if not any(config.get("id")==configuration_id for config in data.get("configurations",[])):raise RuntimeError("Конфигурация не найдена")
        record["configuration_id"]=configuration_id
    data["servers"].append(record)
    save(data)
    rewrite_key(server_id, key_type, key_body, retention)
    print(json.dumps({"ok": True, "server": record}, ensure_ascii=False))


def adopt_archive(raw):
    item = json.loads(raw)
    server_id = validate_id(str(item.get("id") or "").lower())
    directory = ROOT / server_id
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError("Каталог архивов не найден")
    data = load()
    if any(record.get("id") == server_id for record in data.get("servers", [])):
        raise RuntimeError("Профиль с таким ID уже существует")
    record = {
        "id": server_id,
        "name": str(item.get("name") or server_id).strip()[:80],
        "role": "archive",
        "profile_type": "archive",
        "expected_host": "",
        "retention_days": 0,
        "created_at": int(time.time()),
        "enabled": False,
    }
    data["servers"].append(record)
    save(data)
    print(json.dumps({"ok": True, "server": record}, ensure_ascii=False))


def upsert_configuration(raw):
    item=json.loads(raw);configuration_id=validate_id(str(item.get("id") or "").lower())
    name=str(item.get("name") or configuration_id).strip()[:80]
    if not name:raise RuntimeError("Укажите название конфигурации")
    members=item.get("server_ids") or []
    if not isinstance(members,list):raise RuntimeError("Некорректный список серверов")
    members=list(dict.fromkeys(validate_id(str(server_id)) for server_id in members))
    data=load();known={server.get("id") for server in data.get("servers",[])}
    missing=[server_id for server_id in members if server_id not in known]
    if missing:raise RuntimeError("Серверы не найдены: "+", ".join(missing))
    record={"id":configuration_id,"name":name,"description":str(item.get("description") or "").strip()[:240],"updated_at":int(time.time())}
    existing=next((config for config in data.get("configurations",[]) if config.get("id")==configuration_id),None)
    if existing:record["created_at"]=existing.get("created_at") or int(time.time())
    else:record["created_at"]=int(time.time())
    data["configurations"]=[config for config in data.get("configurations",[]) if config.get("id")!=configuration_id]+[record]
    if item.get("replace_members",True):
        for server in data.get("servers",[]):
            if server.get("configuration_id")==configuration_id and server.get("id") not in members:server.pop("configuration_id",None)
    for server in data.get("servers",[]):
        if server.get("id") in members:server["configuration_id"]=configuration_id
    save(data);print(json.dumps({"ok":True,"configuration":record,"members":members},ensure_ascii=False))


def set_enabled(server_id, enabled):
    server_id = validate_id(server_id)
    data = load()
    record = next((item for item in data.get("servers", []) if item.get("id") == server_id), None)
    if not record:
        raise RuntimeError("Сервер не найден")
    if enabled:
        key_type, key_body = clean_key(record.get("public_key", ""))
        rewrite_key(server_id, key_type, key_body, int(record.get("retention_days") or 30))
    else:
        rewrite_key(server_id, remove=True)
    record["enabled"] = enabled
    save(data)
    print(json.dumps({"ok": True, "enabled": enabled}))


def remove_server(server_id, purge):
    server_id = validate_id(server_id)
    data = load()
    if not any(item.get("id") == server_id for item in data.get("servers", [])):
        raise RuntimeError("Сервер не найден")
    rewrite_key(server_id, remove=True)
    source = ROOT / server_id
    detached = None
    if source.exists():
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError("Некорректный каталог сервера")
        if purge:
            shutil.rmtree(source)
        else:
            DETACHED_ROOT.mkdir(parents=True, exist_ok=True)
            detached = DETACHED_ROOT / f"{server_id}-{time.strftime('%Y%m%d-%H%M%S')}"
            source.rename(detached)
    data["servers"] = [item for item in data.get("servers", []) if item.get("id") != server_id]
    save(data)
    print(json.dumps({"ok": True, "purged": purge, "detached": str(detached or "")}, ensure_ascii=False))


def archive_path(server_id, name):
    validate_id(server_id)
    if not ARCHIVE.fullmatch(name):
        raise RuntimeError("invalid path")
    path = ROOT / server_id / name
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("archive not found")
    return path


def manifest(path):
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name.startswith("/") or ".." in pathlib.PurePosixPath(member.name).parts or member.isdev():
                raise RuntimeError("unsafe archive")
            if (member.issym() or member.islnk()) and (os.path.isabs(member.linkname) or ".." in pathlib.PurePosixPath(member.linkname).parts):
                raise RuntimeError("unsafe archive link")
        if "manifest.json" not in archive.getnames():
            raise RuntimeError("manifest missing")
        return json.load(archive.extractfile("manifest.json"))


def quick_manifest(path):
    with tarfile.open(path, "r:gz") as archive:
        for _ in range(32):
            member = archive.next()
            if member is None:
                break
            if member.name.startswith("/") or ".." in pathlib.PurePosixPath(member.name).parts or member.isdev():
                raise RuntimeError("unsafe archive")
            if member.name == "manifest.json":
                return json.load(archive.extractfile(member))
    raise RuntimeError("manifest missing")


def verify(server_id, name):
    path = archive_path(server_id, name)
    info = manifest(path)
    with tempfile.TemporaryDirectory(prefix="verify-") as temporary:
        with tarfile.open(path, "r:gz") as archive:
            archive.extractall(temporary)
        root = pathlib.Path(temporary)
        for relative, expected in info.get("files", {}).items():
            candidate = root / relative
            if not candidate.is_file():
                raise RuntimeError("missing: " + relative)
            checksum = hashlib.sha256()
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
            if checksum.hexdigest() != expected:
                raise RuntimeError("checksum: " + relative)
    print(json.dumps({"ok": True, "manifest": info}, ensure_ascii=False))


def inventory():
    data = load()
    if migrate_registry_keys(data):
        save(data)
    known = {item["id"]: item for item in data.get("servers", [])}
    directories = [item for item in ROOT.iterdir() if item.is_dir() and not item.name.startswith("_")] if ROOT.exists() else []
    result = []
    for directory in sorted(directories, key=lambda item: item.name):
        server = dict(known.get(directory.name) or {"id": directory.name, "name": "Нераспознанный: " + directory.name, "role": "legacy", "expected_host": "", "retention_days": 0, "enabled": False})
        archives = []
        for path in sorted(directory.glob("*.tar.gz"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                info = quick_manifest(path)
                archive_format = info.get("format") or "legacy-v1"
                created = info.get("created_at_utc") or ""
            except Exception as error:
                archive_format, created, info = "invalid", "", {"error": str(error)[:120]}
            archives.append({"name": path.name, "size": path.stat().st_size, "mtime": int(path.stat().st_mtime), "format": archive_format, "created": created, "role": info.get("role", "")})
        server.setdefault("enabled", True)
        server["archives"] = archives
        server["total_size"] = sum(item["size"] for item in archives)
        result.append(server)
    for server_id, server in known.items():
        if not any(item["id"] == server_id for item in result):
            item = dict(server)
            item.update(archives=[], total_size=0)
            item.setdefault("enabled", True)
            result.append(item)
    stat = os.statvfs(ROOT)
    print(json.dumps({"servers": result,"configurations":data.get("configurations",[]), "disk": {"total": stat.f_blocks * stat.f_frsize, "free": stat.f_bavail * stat.f_frsize}}, ensure_ascii=False))


def main():
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    add_parser = sub.add_parser("add")
    add_parser.add_argument("json")
    adopt_parser = sub.add_parser("adopt-archive")
    adopt_parser.add_argument("json")
    configuration_parser=sub.add_parser("upsert-configuration")
    configuration_parser.add_argument("json")
    for command in ("disable", "enable"):
        action = sub.add_parser(command)
        action.add_argument("server_id")
    remove = sub.add_parser("remove-server")
    remove.add_argument("server_id")
    remove.add_argument("--purge", action="store_true")
    delete = sub.add_parser("delete")
    delete.add_argument("server_id")
    delete.add_argument("name")
    check = sub.add_parser("verify")
    check.add_argument("server_id")
    check.add_argument("name")
    args = parser.parse_args()
    if args.command == "inventory":
        inventory()
    elif args.command == "add":
        add(args.json)
    elif args.command == "adopt-archive":
        adopt_archive(args.json)
    elif args.command == "upsert-configuration":
        upsert_configuration(args.json)
    elif args.command == "disable":
        set_enabled(args.server_id, False)
    elif args.command == "enable":
        set_enabled(args.server_id, True)
    elif args.command == "remove-server":
        remove_server(args.server_id, args.purge)
    elif args.command == "delete":
        archive_path(args.server_id, args.name).unlink()
        print('{"ok":true}')
    else:
        verify(args.server_id, args.name)


if __name__ == "__main__":
    main()
