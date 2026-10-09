#!/usr/bin/env python3
"""Create and verify separate site or Telegram-bot state backups."""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time


COMPONENTS = {
    "site": {
        "state": (pathlib.Path("/var/lib/vpn-shop"), pathlib.Path("/etc/home-vpn/config.json")),
        "secrets": (pathlib.Path("/etc/vpn-shop.env"),),
    },
    "bot": {
        "state": (pathlib.Path("/var/lib/vpn-bot"), pathlib.Path("/etc/vpn-bot-menu.json")),
        "secrets": (pathlib.Path("/etc/vpn-bot.env"), pathlib.Path("/etc/vpn-bot-admin.env")),
    },
}


def archive_name(component):
    return f"home-vpn-{component}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"


def checksum(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):digest.update(block)
    return digest.hexdigest()


def safe_members(bundle):
    for member in bundle.getmembers():
        path=pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise ValueError("Небезопасный путь в архиве: "+member.name)
        yield member


def verify_extracted(root, component):
    databases=list(root.rglob("*.db"))+list(root.rglob("*.sqlite"))+list(root.rglob("*.sqlite3"))
    for database in databases:
        connection=sqlite3.connect("file:"+str(database)+"?mode=ro",uri=True)
        try:result=connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:connection.close()
        if result!="ok":raise ValueError(f"SQLite integrity_check failed for {database}: {result}")
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("component")!=component:raise ValueError("Компонент архива не совпадает с ожидаемым")


def verify(archive, component, restore_dir=None):
    destination=pathlib.Path(restore_dir) if restore_dir else pathlib.Path(tempfile.mkdtemp(prefix=f"home-vpn-{component}-restore-test-"))
    temporary=restore_dir is None
    try:
        destination.mkdir(parents=True,exist_ok=True)
        with tarfile.open(archive,"r:gz") as bundle:bundle.extractall(destination,members=safe_members(bundle),filter="data")
        verify_extracted(destination,component)
        return destination
    finally:
        if temporary:shutil.rmtree(destination)


def create(component, output_dir, include_secrets=False, age_recipient=""):
    definition=COMPONENTS[component]
    if include_secrets and not age_recipient:raise ValueError("--include-secrets требует --age-recipient; открытый архив с секретами запрещён")
    output_dir.mkdir(parents=True,exist_ok=True);plain=output_dir/archive_name(component)
    with tempfile.TemporaryDirectory(prefix="home-vpn-backup-") as directory:
        staging=pathlib.Path(directory);included=[]
        for source in definition["state"]+(definition["secrets"] if include_secrets else ()):
            if not source.exists():continue
            target=staging/"rootfs"/source.relative_to("/");target.parent.mkdir(parents=True,exist_ok=True)
            if source.is_dir():shutil.copytree(source,target,symlinks=False)
            else:shutil.copy2(source,target)
            included.append(str(source))
        (staging/"manifest.json").write_text(json.dumps({"format":1,"component":component,"created_at":int(time.time()),"includes_secrets":include_secrets,"paths":included},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        with tarfile.open(plain,"w:gz") as bundle:
            bundle.add(staging/"manifest.json",arcname="manifest.json",recursive=False)
            if (staging/"rootfs").exists():bundle.add(staging/"rootfs",arcname="rootfs")
    os.chmod(plain,0o600);verify(plain,component)
    target=plain
    if include_secrets:
        encrypted=plain.with_suffix(plain.suffix+".age")
        subprocess.run(["age","-r",age_recipient,"-o",encrypted,plain],check=True);os.chmod(encrypted,0o600);plain.unlink();target=encrypted
    digest=checksum(target);checksum_path=target.with_name(target.name+".sha256");checksum_path.write_text(f"{digest}  {target.name}\n",encoding="ascii");os.chmod(checksum_path,0o600)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    create_parser=sub.add_parser("create");create_parser.add_argument("component",choices=COMPONENTS);create_parser.add_argument("--output-dir",type=pathlib.Path,default=pathlib.Path("/var/backups/home-vpn"));create_parser.add_argument("--include-secrets",action="store_true");create_parser.add_argument("--age-recipient",default="")
    verify_parser=sub.add_parser("verify");verify_parser.add_argument("component",choices=COMPONENTS);verify_parser.add_argument("archive",type=pathlib.Path);verify_parser.add_argument("--restore-dir",type=pathlib.Path)
    arguments=parser.parse_args()
    if arguments.command=="create":print(create(arguments.component,arguments.output_dir,arguments.include_secrets,arguments.age_recipient))
    else:print(verify(arguments.archive,arguments.component,arguments.restore_dir) or "verified")


if __name__=="__main__":main()
