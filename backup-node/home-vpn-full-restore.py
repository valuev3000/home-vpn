#!/usr/bin/env python3
"""Verify or restore HOME-VPN full-v2 application state on a replacement host."""

import argparse, hashlib, json, os, pathlib, shutil, sqlite3, subprocess, tarfile, tempfile

def digest(path):
    value=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):value.update(block)
    return value.hexdigest()

def validate_members(archive):
    for member in archive.getmembers():
        parts=pathlib.PurePosixPath(member.name).parts
        if member.name.startswith("/") or ".." in parts or member.isdev() or member.isfifo():raise RuntimeError("unsafe archive member")
        if (member.issym() or member.islnk()) and (os.path.isabs(member.linkname) or ".." in pathlib.PurePosixPath(member.linkname).parts):raise RuntimeError("unsafe archive link")

def replace_host(path,old,new):
    if not path.is_file() or path.stat().st_size>10*1024*1024:return
    try:text=path.read_text(encoding="utf-8")
    except (UnicodeDecodeError,OSError):return
    if old in text:path.write_text(text.replace(old,new),encoding="utf-8")

def main():
    parser=argparse.ArgumentParser();parser.add_argument("archive",type=pathlib.Path);parser.add_argument("--apply",action="store_true");parser.add_argument("--replace-host",metavar="OLD=NEW");args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit("run as root")
    with tempfile.TemporaryDirectory(prefix="home-vpn-restore-") as temporary:
        root=pathlib.Path(temporary)
        with tarfile.open(args.archive,"r:gz") as archive:validate_members(archive);archive.extractall(root)
        manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format")!="home-vpn-full-v2":raise RuntimeError("unsupported archive format")
        for relative,expected in manifest.get("files",{}).items():
            path=root/relative
            if not path.is_file() or digest(path)!=expected:raise RuntimeError("checksum failed: "+relative)
        for item in manifest.get("databases",[]):
            db=root/"databases"/item["name"]
            with sqlite3.connect(f"file:{db}?mode=ro",uri=True) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RuntimeError("SQLite failed: "+item["name"])
        print(f"Проверка успешна: {manifest['server_name']} · {manifest['created_at_utc']}")
        if not args.apply:return
        if input("Введите RESTORE для замены файлов: ").strip()!="RESTORE":raise SystemExit("cancelled")
        for service in manifest.get("services",[]):subprocess.run(["systemctl","stop",service],check=False)
        payload=root/"payload"
        if payload.exists():
            for item in payload.rglob("*"):
                relative=item.relative_to(payload);target=pathlib.Path("/")/relative
                if item.is_dir():target.mkdir(parents=True,exist_ok=True)
                elif item.is_symlink():
                    target.parent.mkdir(parents=True,exist_ok=True);target.unlink(missing_ok=True);target.symlink_to(os.readlink(item))
                elif item.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(item,target)
        for item in manifest.get("databases",[]):
            source=root/"databases"/item["name"];target=pathlib.Path(item["source"]);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        if args.replace_host:
            old,separator,new=args.replace_host.partition("=")
            if not separator or not old or not new:raise RuntimeError("format: OLD=NEW")
            for raw in manifest.get("paths",[]):
                path=pathlib.Path(raw)
                if path.is_dir():
                    for item in path.rglob("*"):replace_host(item,old,new)
                else:replace_host(path,old,new)
        subprocess.run(["systemctl","daemon-reload"],check=False)
        for service in manifest.get("services",[]):subprocess.run(["systemctl","start",service],check=False)
        print("Восстановление завершено. Проверьте DNS, TLS, firewall и адреса нод.")

if __name__=="__main__":main()
