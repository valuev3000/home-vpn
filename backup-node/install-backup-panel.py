#!/usr/bin/env python3
"""Install the standalone HTTPS backup-management panel."""

import argparse, grp, ipaddress, os, pathlib, pwd, secrets, shutil, subprocess

HERE=pathlib.Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--bind",default="0.0.0.0");parser.add_argument("--port",type=int,default=9443);parser.add_argument("--ip",default="127.0.0.1");parser.add_argument("--public-host",default="backup.example.ru");args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit("run as root")
    subprocess.run(["apt-get","update"],check=True);subprocess.run(["apt-get","install","-y","python3","sudo","openssl","ca-certificates","openssh-server"],check=True)
    try:account=pwd.getpwnam("homevpnbackup")
    except KeyError:
        subprocess.run(["useradd","--system","--create-home","--home-dir","/var/lib/homevpnbackup","--shell","/bin/sh","homevpnbackup"],check=True);account=pwd.getpwnam("homevpnbackup")
    subprocess.run(["usermod","--shell","/bin/sh","homevpnbackup"],check=True)
    subprocess.run(["passwd","-d","homevpnbackup"],check=True,capture_output=True)
    ssh_dir=pathlib.Path(account.pw_dir)/".ssh";ssh_dir.mkdir(parents=True,exist_ok=True);os.chmod(ssh_dir,0o700);os.chown(ssh_dir,account.pw_uid,account.pw_gid)
    authorized=ssh_dir/"authorized_keys"
    if not authorized.exists():authorized.touch()
    os.chmod(authorized,0o600);os.chown(authorized,account.pw_uid,account.pw_gid)
    ssh_policy=pathlib.Path("/etc/ssh/sshd_config.d/90-home-vpn-backup.conf")
    ssh_policy.write_text("Match User homevpnbackup\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    AuthenticationMethods publickey\n    AllowTcpForwarding no\n    X11Forwarding no\n    PermitTunnel no\n    GatewayPorts no\n",encoding="utf-8")
    os.chmod(ssh_policy,0o644);subprocess.run(["sshd","-t"],check=True)
    target=pathlib.Path("/opt/home-vpn-backup-panel");target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(HERE/"home-vpn-backup-panel.py",target/"panel.py");shutil.copy2(HERE/"home-vpn-backup-panel-control.py","/usr/local/sbin/home-vpn-backup-panel-control");os.chmod("/usr/local/sbin/home-vpn-backup-panel-control",0o700)
    shutil.copy2(HERE/"home-vpn-backup-receive.py","/usr/local/sbin/home-vpn-backup-receive");os.chmod("/usr/local/sbin/home-vpn-backup-receive",0o755)
    shutil.copy2(HERE/"home-vpn-backup-panel.service","/etc/systemd/system/home-vpn-backup-panel.service");shutil.copy2(HERE/"home-vpn-backup-panel-sudoers","/etc/sudoers.d/home-vpn-backup-panel");os.chmod("/etc/sudoers.d/home-vpn-backup-panel",0o440);subprocess.run(["visudo","-cf","/etc/sudoers.d/home-vpn-backup-panel"],check=True)
    pathlib.Path("/srv/home-vpn-backups").mkdir(parents=True,exist_ok=True);os.chown("/srv/home-vpn-backups",account.pw_uid,account.pw_gid);os.chmod("/srv/home-vpn-backups",0o700)
    pathlib.Path("/srv/home-vpn-backups-removed").mkdir(parents=True,exist_ok=True);os.chmod("/srv/home-vpn-backups-removed",0o700)
    config=pathlib.Path("/etc/home-vpn-backup-panel");config.mkdir(parents=True,exist_ok=True);os.chmod(config,0o750);os.chown(config,0,account.pw_gid)
    registry=config/"servers.json"
    if not registry.exists():registry.write_text('{"servers":[]}\n',encoding="utf-8")
    os.chmod(registry,0o640);os.chown(registry,0,account.pw_gid)
    tls=config/"tls";tls.mkdir(exist_ok=True);cert=tls/"panel.crt";key=tls/"panel.key"
    if not cert.exists() or not key.exists():
        san="DNS:home-vpn-backup"
        try:san+=",IP:"+str(ipaddress.ip_address(args.ip))
        except ValueError:san+=",DNS:"+args.ip
        subprocess.run(["openssl","req","-x509","-newkey","rsa:3072","-sha256","-nodes","-days","825","-keyout",str(key),"-out",str(cert),"-subj","/CN=home-vpn-backup","-addext","subjectAltName="+san],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    os.chmod(key,0o640);os.chown(key,0,account.pw_gid);os.chmod(cert,0o644)
    password=secrets.token_urlsafe(18);entry="/backup-"+secrets.token_urlsafe(10);environment=pathlib.Path("/etc/home-vpn-backup-panel.env")
    if not environment.exists():
        environment.write_text(f"BACKUP_PANEL_BIND={args.bind}\nBACKUP_PANEL_PORT={args.port}\nBACKUP_PANEL_USER=backup-admin\nBACKUP_PANEL_PASSWORD={password}\nBACKUP_PANEL_SECRET={secrets.token_urlsafe(48)}\nBACKUP_PANEL_ENTRY_PATH={entry}\nBACKUP_PANEL_TLS_CERT={cert}\nBACKUP_PANEL_TLS_KEY={key}\nBACKUP_PANEL_PUBLIC_HOST={args.public_host}\n",encoding="utf-8");os.chmod(environment,0o600)
        credentials=pathlib.Path("/root/home-vpn-backup-panel-credentials.txt");credentials.write_text(f"PANEL_URL=https://{args.ip}:{args.port}{entry}\nPANEL_USER=backup-admin\nPANEL_PASSWORD={password}\n",encoding="utf-8");os.chmod(credentials,0o600)
    subprocess.run(["systemctl","daemon-reload"],check=True);subprocess.run(["systemctl","restart","ssh"],check=True);subprocess.run(["systemctl","enable","--now","home-vpn-backup-panel"],check=True)
    print("Панель установлена. Учётные данные: /root/home-vpn-backup-panel-credentials.txt")

if __name__=="__main__":main()
