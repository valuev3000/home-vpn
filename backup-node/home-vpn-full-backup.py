#!/usr/bin/env python3
"""Create a portable HOME-VPN application-state backup and optionally push it."""

import datetime, hashlib, json, os, pathlib, shutil, sqlite3, subprocess, tarfile, tempfile, time

PROFILE=pathlib.Path(os.getenv("HOME_VPN_FULL_BACKUP_PROFILE","/etc/home-vpn-full-backup.json"))

def digest(path):
    result=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):result.update(block)
    return result.hexdigest()

def sqlite_copy(source,target):
    if not source.is_file():return False
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro",uri=True) as src,sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RuntimeError(f"SQLite повреждена: {source}")
    # SQLite may briefly leave WAL shared-memory sidecars next to the consistent
    # destination copy.  They are not part of the backup and can disappear
    # between manifest generation and tar creation, producing an unverifiable
    # archive.  Remove only sidecars belonging to the temporary copy.
    for suffix in ("-wal", "-shm"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    return True

def safe_copy(source,target):
    if source.is_symlink():
        link=os.readlink(source)
        if os.path.isabs(link) or ".." in pathlib.PurePath(link).parts:return
        target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(link);return
    if source.is_dir():shutil.copytree(source,target,symlinks=True,ignore_dangling_symlinks=True,dirs_exist_ok=True)
    elif source.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)

def main():
    if os.geteuid()!=0:raise SystemExit("run as root")
    profile=json.loads(PROFILE.read_text(encoding="utf-8"));server_id=str(profile["server_id"])
    if not server_id.replace("-","").replace("_","").isalnum():raise SystemExit("invalid server_id")
    destination=pathlib.Path(profile.get("local_dir") or "/var/backups/home-vpn-full");destination.mkdir(parents=True,exist_ok=True);os.chmod(destination,0o700)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S");final=destination/f"{server_id}-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory(prefix=".full-backup-",dir=destination) as temporary:
        work=pathlib.Path(temporary);payload=work/"payload";databases=work/"databases";included=[];dbmap=[]
        for item in profile.get("paths",[]):
            source=pathlib.Path(item)
            if source.exists():safe_copy(source,payload/source.relative_to("/"));included.append(str(source))
        for item in profile.get("databases",[]):
            source=pathlib.Path(item["path"]);name=str(item["name"])
            if sqlite_copy(source,databases/name):dbmap.append({"name":name,"source":str(source)})
        system=work/"system";system.mkdir();
        (system/"hostname.txt").write_text(subprocess.run(["hostname","-f"],capture_output=True,text=True,check=False).stdout.strip()+"\n")
        (system/"packages.txt").write_text(subprocess.run(["dpkg-query","-W","-f=${binary:Package}\t${Version}\n"],capture_output=True,text=True,check=False).stdout)
        manifest={"format":"home-vpn-full-v2","server_id":server_id,"server_name":profile.get("server_name",server_id),"role":profile.get("role","node"),"created_at_utc":stamp,"hostname":os.uname().nodename,"services":profile.get("services",[]),"paths":included,"databases":dbmap,"files":{}}
        for path in sorted(work.rglob("*")):
            if path.is_file() and not path.is_symlink():manifest["files"][str(path.relative_to(work))]=digest(path)
        (work/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        partial=destination/("."+final.name+".tmp")
        with tarfile.open(partial,"w:gz") as archive:
            for item in sorted(work.iterdir()):archive.add(item,arcname=item.name,recursive=True)
        os.chmod(partial,0o600);os.replace(partial,final)
    remote=str(profile.get("remote") or "").strip()
    if remote:
        command=["ssh","-T","-o","BatchMode=yes","-o","StrictHostKeyChecking=yes","-o",f"UserKnownHostsFile={profile['known_hosts']}","-o","ConnectTimeout=20","-p",str(profile.get("remote_port",22)),"-i",str(profile["ssh_key"]),remote]
        with final.open("rb") as stream:subprocess.run(command,stdin=stream,check=True,timeout=int(profile.get("timeout",900)))
    cutoff=time.time()-max(1,int(profile.get("local_retention_days",7)))*86400
    for old in destination.glob(f"{server_id}-*.tar.gz"):
        if old!=final and old.stat().st_mtime<cutoff:old.unlink()
    print(final)

if __name__=="__main__":main()
