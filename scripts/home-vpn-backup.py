#!/usr/bin/env python3
"""Small, consistent HOME-VPN backups for low-resource installations."""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path


BACKUP_DIR = Path(os.getenv("HOME_VPN_BACKUP_DIR", "/var/backups/home-vpn"))
RETENTION_DAYS = max(1, int(os.getenv("HOME_VPN_BACKUP_RETENTION_DAYS", "14")))
REMOTE = os.getenv("HOME_VPN_BACKUP_REMOTE", "").strip()
REMOTE_PORT = os.getenv("HOME_VPN_BACKUP_REMOTE_PORT", "22").strip()
SSH_KEY = os.getenv("HOME_VPN_BACKUP_SSH_KEY", "/root/.ssh/home-vpn-backup").strip()
KNOWN_HOSTS = os.getenv("HOME_VPN_BACKUP_KNOWN_HOSTS", "/etc/home-vpn-backup-known-hosts").strip()
DATABASES = {
    "shop.db": Path(os.getenv("VPN_SHOP_DB", "/var/lib/vpn-shop/shop.db")),
    "x-ui.db": Path(os.getenv("XUI_DB", "/etc/x-ui/x-ui.db")),
}
CONFIG_PATHS = (
    Path("/etc/home-vpn/config.json"),
    Path("/etc/vpn-shop.env"),
    Path("/etc/systemd/system/vpn-shop.service"),
    Path("/etc/systemd/system/vpn-shop.service.d"),
    Path("/etc/x-ui/cert"),
)


def sqlite_backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"Integrity check failed for {source}: {result}")
    for suffix in ("-wal", "-shm"):
        destination.with_name(destination.name + suffix).unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    archive = BACKUP_DIR / f"home-vpn-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=BACKUP_DIR) as temporary:
        work = Path(temporary)
        database_dir = work / "databases"
        database_dir.mkdir()
        for name, source in DATABASES.items():
            sqlite_backup(source, database_dir / name)

        config_dir = work / "configuration"
        for source in CONFIG_PATHS:
            if not source.exists():
                continue
            destination = config_dir / source.relative_to("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)

        manifest = {
            "created_at_utc": stamp,
            "retention_days": RETENTION_DAYS,
            "files": {},
        }
        for path in sorted(work.rglob("*")):
            if path.is_file():
                manifest["files"][str(path.relative_to(work))] = sha256(path)
        (work / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        temporary_archive = BACKUP_DIR / f".{archive.name}.tmp"
        with tarfile.open(temporary_archive, "w:gz") as bundle:
            for item in sorted(work.iterdir()):
                bundle.add(item, arcname=item.name, recursive=True)
        os.chmod(temporary_archive, 0o600)
        os.replace(temporary_archive, archive)

    if REMOTE:
        command = [
            "/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "ConnectTimeout=15", "-p", REMOTE_PORT, "-i", SSH_KEY, REMOTE,
        ]
        with archive.open("rb") as stream:
            subprocess.run(command, stdin=stream, check=True, timeout=300)
        marker = Path('/var/lib/vpn-shop/remote-backup-success')
        marker.write_text(str(int(time.time()))+'\n',encoding='utf-8')
        os.chmod(marker,0o640)

    cutoff = time.time() - RETENTION_DAYS * 86400
    for old in BACKUP_DIR.glob("home-vpn-*.tar.gz"):
        if old != archive and old.stat().st_mtime < cutoff:
            old.unlink()
    print(archive)


if __name__ == "__main__":
    main()
