#!/usr/bin/env python3
"""Install a portable backup agent on a HOME-VPN source server."""

import argparse,json,os,pathlib,re,shutil,subprocess

HERE=pathlib.Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--server-id",required=True);parser.add_argument("--server-name",required=True);parser.add_argument("--role",choices=("site","bot","node"),required=True);parser.add_argument("--remote",required=True);parser.add_argument("--port",type=int,default=22);parser.add_argument("--key",default="/root/.ssh/home-vpn-full-backup");parser.add_argument("--known-hosts",default="/etc/home-vpn-full-backup-known-hosts");parser.add_argument("--trust-host-key",action="store_true",help="accept scanned host key non-interactively");args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit("run as root")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}",args.server_id):raise SystemExit("server-id: 2–32 символа a-z, 0-9, _ или -")
    key=pathlib.Path(args.key);key.parent.mkdir(parents=True,exist_ok=True)
    if not key.exists():subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(key)],check=True)
    known_hosts=pathlib.Path(args.known_hosts);host=args.remote.rsplit("@",1)[-1]
    scan=subprocess.run(["ssh-keyscan","-t","ed25519","-p",str(args.port),host],capture_output=True,text=True,timeout=30,check=False)
    if scan.returncode or not scan.stdout.strip():raise SystemExit("Не удалось получить SSH host key backup-сервера")
    old=known_hosts.read_text(encoding="utf-8") if known_hosts.exists() else ""
    normalize=lambda value:sorted(line.strip() for line in value.splitlines() if line.strip() and not line.startswith("#"))
    if normalize(old)!=normalize(scan.stdout):
        fingerprint=subprocess.run(["ssh-keygen","-lf","-"],input=scan.stdout,capture_output=True,text=True,check=True).stdout.strip()
        print("Отпечаток SSH backup-сервера:\n"+fingerprint)
        if not args.trust_host_key and input("Сверьте отпечаток с backup-сервером и введите YES: ").strip()!="YES":raise SystemExit("Настройка отменена")
        known_hosts.write_text(scan.stdout,encoding="utf-8");os.chmod(known_hosts,0o600)
    common=["/etc/x-ui","/etc/systemd/system/x-ui.service"]
    if args.role=="site":
        paths=common+["/etc/home-vpn","/etc/vpn-shop.env","/opt/vpn-shop","/srv/home-vpn/assets","/etc/systemd/system/vpn-shop.service","/etc/home-vpn-backup.env"]
        databases=[{"name":"shop.db","path":"/var/lib/vpn-shop/shop.db"},{"name":"x-ui.db","path":"/etc/x-ui/x-ui.db"}];services=["x-ui","vpn-shop"]
    elif args.role=="bot":
        paths=common+["/etc/vpn-bot.env","/etc/vpn-bot-admin.env","/etc/vpn-bot-menu.json","/opt/vpn-bot","/var/lib/vpn-bot","/etc/systemd/system/vpn-bot.service","/etc/systemd/system/vpn-bot-admin.service"]
        databases=[{"name":"x-ui.db","path":"/etc/x-ui/x-ui.db"}];services=["x-ui","vpn-bot","vpn-bot-admin"]
    else:
        paths=common;databases=[{"name":"x-ui.db","path":"/etc/x-ui/x-ui.db"}];services=["x-ui"]
    profile={"server_id":args.server_id,"server_name":args.server_name,"role":args.role,"paths":paths,"databases":databases,"services":services,"local_dir":"/var/backups/home-vpn-full","local_retention_days":7,"remote":args.remote,"remote_port":args.port,"ssh_key":str(key),"known_hosts":args.known_hosts,"timeout":1200}
    pathlib.Path("/etc/home-vpn-full-backup.json").write_text(json.dumps(profile,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");os.chmod("/etc/home-vpn-full-backup.json",0o600)
    for source,target,mode in (("home-vpn-full-backup.py","/usr/local/sbin/home-vpn-full-backup",0o700),("home-vpn-full-restore.py","/usr/local/sbin/home-vpn-full-restore",0o700)):
        shutil.copy2(HERE/source,target);os.chmod(target,mode)
    shutil.copy2(HERE/"home-vpn-full-backup.service","/etc/systemd/system/home-vpn-full-backup.service");shutil.copy2(HERE/"home-vpn-full-backup.timer","/etc/systemd/system/home-vpn-full-backup.timer")
    subprocess.run(["systemctl","daemon-reload"],check=True);subprocess.run(["systemctl","enable","--now","home-vpn-full-backup.timer"],check=True)
    print("\nПубличный ключ для кнопки «Добавить сервер» в backup-панели:")
    print(key.with_suffix(".pub").read_text(encoding="utf-8").strip())
    print("\nПосле добавления сервера в панели запустите:")
    print("  systemctl start home-vpn-full-backup.service")
    print("  systemctl status home-vpn-full-backup.service --no-pager")

if __name__=="__main__":main()
