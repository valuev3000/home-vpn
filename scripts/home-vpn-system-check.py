#!/usr/bin/env python3
"""Low-cost operational checks for a small HOME-VPN server."""

import json
import os
import pathlib
import pwd
import shutil
import subprocess
import time


OUTPUT = pathlib.Path('/var/lib/vpn-shop/system-health.json')
BACKUP_DIR = pathlib.Path(os.getenv('HOME_VPN_BACKUP_DIR', '/var/backups/home-vpn'))
CERTIFICATES = (
    pathlib.Path('/etc/x-ui/cert/fullchain.pem'),
    pathlib.Path('/etc/x-ui/cert/panel.crt'),
    pathlib.Path('/root/cert/fullchain.pem'),
)


def main() -> None:
    now = int(time.time());checks={};warnings=[]
    disk = shutil.disk_usage('/')
    disk_percent = round((disk.total-disk.free)*100/disk.total,1)
    checks['disk_used_percent']=disk_percent
    if disk_percent >= 90:warnings.append(f'Диск заполнен на {disk_percent}%')

    archives=sorted(BACKUP_DIR.glob('home-vpn-*.tar.gz'),key=lambda path:path.stat().st_mtime,reverse=True)
    backup_age_hours=round((now-archives[0].stat().st_mtime)/3600,1) if archives else None
    checks['last_backup_age_hours']=backup_age_hours
    if backup_age_hours is None:warnings.append('Резервные копии не найдены')
    elif backup_age_hours>36:warnings.append(f'Последняя резервная копия создана {backup_age_hours} ч. назад')

    remote_configured=False
    remote_env=pathlib.Path('/etc/home-vpn-backup.env')
    if remote_env.is_file():
        remote_configured=any(line.startswith('HOME_VPN_BACKUP_REMOTE=') and line.split('=',1)[1].strip() for line in remote_env.read_text(encoding='utf-8').splitlines())
    checks['remote_backup_configured']=remote_configured
    if remote_configured:
        marker=pathlib.Path('/var/lib/vpn-shop/remote-backup-success')
        remote_age_hours=round((now-marker.stat().st_mtime)/3600,1) if marker.is_file() else None
        checks['last_remote_backup_age_hours']=remote_age_hours
        if remote_age_hours is None:warnings.append('Нет подтверждения отправки копии на backup-сервер')
        elif remote_age_hours>36:warnings.append(f'Последняя удалённая копия отправлена {remote_age_hours} ч. назад')

    for service in ('vpn-shop','x-ui'):
        active=subprocess.run(['systemctl','is-active','--quiet',service]).returncode==0
        checks['service_'+service]=active
        if not active:warnings.append(f'Служба {service} не работает')

    certificate=next((path for path in CERTIFICATES if path.is_file()),None)
    checks['certificate']=str(certificate) if certificate else None
    if certificate:
        valid=subprocess.run(['openssl','x509','-checkend',str(14*86400),'-noout','-in',str(certificate)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        checks['certificate_valid_more_than_14_days']=valid
        if not valid:warnings.append('TLS-сертификат истекает менее чем через 14 дней')
    else:warnings.append('TLS-сертификат не найден в стандартных каталогах')

    payload={'ok':not warnings,'checked_at':now,'warnings':warnings,'checks':checks}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    temporary=OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.chmod(temporary,0o640)
    try:
        account=pwd.getpwnam('vpnshop');os.chown(temporary,account.pw_uid,account.pw_gid)
    except KeyError:pass
    os.replace(temporary,OUTPUT)
    print('OK' if not warnings else '; '.join(warnings))


if __name__=='__main__':main()
