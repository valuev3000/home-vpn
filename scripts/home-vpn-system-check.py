#!/usr/bin/env python3
"""Low-cost operational checks for a small HOME-VPN server."""

import json
import hashlib
import math
import os
import pathlib
import pwd
import shutil
import ssl
import subprocess
import time


OUTPUT = pathlib.Path('/var/lib/vpn-shop/system-health.json')
BACKUP_DIR = pathlib.Path('/root')
CERTIFICATES = (
    pathlib.Path('/etc/x-ui/cert/fullchain.pem'),
    pathlib.Path('/etc/x-ui/cert/panel.crt'),
    pathlib.Path('/root/cert/fullchain.pem'),
)
LETS_ENCRYPT_LIVE = pathlib.Path('/etc/letsencrypt/live')


def certificate_paths(live_dir=LETS_ENCRYPT_LIVE):
    paths = sorted(pathlib.Path(live_dir).glob('*/fullchain.pem')) + list(CERTIFICATES)
    result=[];seen=set()
    for path in paths:
        if not path.is_file():continue
        try:fingerprint=hashlib.sha256(path.read_bytes()).digest()
        except OSError:fingerprint=str(path.resolve())
        if fingerprint in seen:continue
        seen.add(fingerprint);result.append(path)
    return result


def certificate_info(path, now):
    result = subprocess.run(
        ['openssl', 'x509', '-enddate', '-noout', '-in', str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.startswith('notAfter='):
        return {'path': str(path), 'error': 'не удалось прочитать срок действия'}
    try:
        not_after = int(ssl.cert_time_to_seconds(result.stdout.partition('=')[2].strip()))
    except (ValueError, OverflowError):
        return {'path': str(path), 'error': 'некорректный срок действия'}
    seconds_left = not_after - now
    return {
        'path': str(path), 'not_after': not_after,
        'seconds_left': seconds_left,
        'days_left': math.ceil(seconds_left / 86400),
    }


def main() -> None:
    now = int(time.time());checks={};warnings=[]
    xui_loaded=subprocess.run(
        ['systemctl','show','-p','LoadState','--value','x-ui'],
        capture_output=True,text=True,
    ).stdout.strip()=='loaded'
    disk = shutil.disk_usage('/')
    disk_percent = round((disk.total-disk.free)*100/disk.total,1)
    checks['disk_used_percent']=disk_percent
    if disk_percent >= 90:warnings.append(f'Диск заполнен на {disk_percent}%')

    archives=sorted(BACKUP_DIR.glob('kit-backup-*.tar.gz'),key=lambda path:path.stat().st_mtime,reverse=True)
    backup_age_hours=round((now-archives[0].stat().st_mtime)/3600,1) if archives else None
    checks['last_backup_age_hours']=backup_age_hours
    if xui_loaded and backup_age_hours is None:warnings.append('Резервные копии 3x-ui не найдены')
    elif xui_loaded and backup_age_hours>36:warnings.append(f'Последняя резервная копия 3x-ui создана {backup_age_hours} ч. назад')

    services=['vpn-shop']
    if pathlib.Path('/etc/vpn-bot.env').is_file():services.extend(('vpn-bot','vpn-bot-admin','home-vpn-bot-egress'))
    if xui_loaded:services.append('x-ui')
    for service in services:
        active=subprocess.run(['systemctl','is-active','--quiet',service]).returncode==0
        checks['service_'+service]=active
        if not active:warnings.append(f'Служба {service} не работает')

    certificates=[certificate_info(path,now) for path in certificate_paths()]
    checks['certificates']=certificates
    readable=[item for item in certificates if item.get('not_after')]
    certificate=min(readable,key=lambda item:item['not_after']) if readable else None
    checks['certificate']=certificate['path'] if certificate else None
    checks['certificate_valid_more_than_14_days']=bool(certificate and certificate['seconds_left']>14*86400)
    for item in certificates:
        if item.get('error'):warnings.append(f"TLS-сертификат {item['path']}: {item['error']}")
        elif item['seconds_left']<=0:warnings.append(f"TLS-сертификат {item['path']} истёк")
        elif item['seconds_left']<=14*86400:warnings.append(f"TLS-сертификат {item['path']} истекает через {item['days_left']} дн.")
    if not certificates:warnings.append('TLS-сертификат не найден в стандартных каталогах и /etc/letsencrypt/live')

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
