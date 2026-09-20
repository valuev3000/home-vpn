#!/usr/bin/env python3
"""Install the optional HOME-VPN backup-node receiver."""

import argparse
import os
import pathlib
import pwd
import shutil
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description="Install a restricted HOME-VPN backup receiver")
    parser.add_argument("--public-key", required=True, help="Dedicated SSH public key from the main server")
    parser.add_argument("--destination", default="/srv/home-vpn-backups/primary")
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("Run through sudo")
    key = args.public_key.strip()
    if not (key.startswith("ssh-ed25519 ") or key.startswith("ssh-rsa ")):
        raise SystemExit("Invalid SSH public key")
    try:
        account = pwd.getpwnam("homevpnbackup")
    except KeyError:
        subprocess.run(["useradd", "--system", "--create-home", "--home-dir", "/var/lib/homevpnbackup", "--shell", "/bin/sh", "homevpnbackup"], check=True)
        account = pwd.getpwnam("homevpnbackup")
    destination = pathlib.Path(args.destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(destination, account.pw_uid, account.pw_gid)
    receiver = pathlib.Path("/usr/local/sbin/home-vpn-backup-receive")
    source = pathlib.Path(__file__).with_name("home-vpn-backup-receive.py")
    shutil.copy2(source, receiver)
    receiver.chmod(0o755)
    ssh_dir = pathlib.Path(account.pw_dir) / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    authorized = ssh_dir / "authorized_keys"
    forced = f'restrict,command="HOME_VPN_BACKUP_DESTINATION={destination} HOME_VPN_BACKUP_RETENTION_DAYS={max(1,args.retention_days)} /usr/local/sbin/home-vpn-backup-receive" {key}'
    existing = authorized.read_text(encoding="utf-8").splitlines() if authorized.exists() else []
    if forced not in existing: existing.append(forced)
    authorized.write_text("\n".join(existing)+"\n", encoding="utf-8")
    authorized.chmod(0o600)
    os.chown(ssh_dir, account.pw_uid, account.pw_gid)
    os.chown(authorized, account.pw_uid, account.pw_gid)
    subprocess.run(['passwd','-d','homevpnbackup'],check=True,capture_output=True)
    ssh_hardening = pathlib.Path('/etc/ssh/sshd_config.d/90-home-vpn-backup.conf')
    ssh_hardening.write_text('Match User homevpnbackup\n    PasswordAuthentication no\n    KbdInteractiveAuthentication no\n    AuthenticationMethods publickey\n    AllowTcpForwarding no\n    X11Forwarding no\n    PermitTunnel no\n    GatewayPorts no\n',encoding='utf-8')
    ssh_hardening.chmod(0o644)
    subprocess.run(['sshd','-t'],check=True)
    subprocess.run(['systemctl','restart','ssh'],check=True)
    print("Backup node is ready")


if __name__ == "__main__":
    main()
