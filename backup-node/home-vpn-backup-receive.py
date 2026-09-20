#!/usr/bin/env python3
"""Restricted SSH receiver for a dedicated HOME-VPN backup node."""

import datetime
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path


DESTINATION = Path(os.getenv("HOME_VPN_BACKUP_DESTINATION", "/srv/home-vpn-backups/primary"))
RETENTION_DAYS = max(1, int(os.getenv("HOME_VPN_BACKUP_RETENTION_DAYS", "30")))
MAX_BYTES = max(1, int(os.getenv("HOME_VPN_BACKUP_MAX_BYTES", str(1024 * 1024 * 1024))))


def main() -> None:
    DESTINATION.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DESTINATION, 0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=".incoming-", suffix=".tar.gz", dir=DESTINATION)
    temporary = Path(temporary_name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as target:
            while True:
                block = os.read(0, 1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_BYTES:
                    raise RuntimeError("backup is larger than the configured limit")
                target.write(block)
            target.flush()
            os.fsync(target.fileno())
        with tarfile.open(temporary, "r:gz") as archive:
            names = set(archive.getnames())
            for member in archive.getmembers():
                parts=Path(member.name).parts
                if member.name.startswith("/") or ".." in parts or member.isdev() or member.isfifo():raise RuntimeError("unsafe archive member")
                if (member.issym() or member.islnk()) and (os.path.isabs(member.linkname) or ".." in Path(member.linkname).parts):raise RuntimeError("unsafe archive link")
            if "manifest.json" not in names: raise RuntimeError("invalid HOME-VPN backup archive")
            manifest=json.load(archive.extractfile("manifest.json"))
            legacy={"databases/shop.db","databases/x-ui.db"}.issubset(names)
            if manifest.get("format")!="home-vpn-full-v2" and not legacy:raise RuntimeError("unsupported HOME-VPN archive")
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        prefix=str(manifest.get("server_id") or "home-vpn").replace("/","")
        final = DESTINATION / f"{prefix}-{stamp}.tar.gz"
        os.chmod(temporary, 0o600)
        os.replace(temporary, final)
        cutoff = time.time() - RETENTION_DAYS * 86400
        for old in DESTINATION.glob("*.tar.gz"):
            if old != final and old.stat().st_mtime < cutoff:
                old.unlink()
        print(final.name)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
